"""Audit trail (SQLite, append-only) + structured JSON log lines.

Every important operation goes through ``AuditLogger.record``, which:
1. redacts secrets (known secret values and common token patterns),
2. appends a row to the ``audit`` table,
3. emits one JSON log line.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime
from typing import Any, Iterable, Optional

from .models import iso, utcnow
from .store import Store, StoreError, new_id

LOGGER_NAME = "oncall_agent"

_TOKEN_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"lin_api_[A-Za-z0-9]{16,}"),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-._~+/]{12,}=*"),
    re.compile(r"(?i)((?:password|passwd|token|api[_-]?key|secret)\s*[=:]\s*)[^\s,'\"}]+"),
]


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": iso(datetime.fromtimestamp(record.created).astimezone()),
            "level": record.levelname.lower(),
            "event": getattr(record, "event", None) or record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_json_logging(level: int = logging.INFO, stream=None) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    for h in list(logger.handlers):
        logger.removeHandler(h)
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    return logger


def log_event(event: str, level: int = logging.INFO, **fields: Any) -> None:
    logging.getLogger(LOGGER_NAME).log(level, event, extra={"event": event, "fields": fields})


class Redactor:
    def __init__(self, secrets: Iterable[str] = ()):
        self._secrets = sorted({s for s in secrets if s and len(s) >= 4}, key=len, reverse=True)

    def __call__(self, text: Optional[str]) -> Optional[str]:
        if text is None:
            return None
        for secret in self._secrets:
            text = text.replace(secret, "***REDACTED***")
        for pattern in _TOKEN_PATTERNS:
            if pattern.groups:
                text = pattern.sub(lambda m: m.group(1) + "***REDACTED***", text)
            else:
                text = pattern.sub("***REDACTED***", text)
        return text


def _summarise(value: Any, limit: int = 2000) -> Optional[str]:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, default=str, sort_keys=True)
    return text if len(text) <= limit else text[: limit - 15] + "...[truncated]"


class AuditLogger:
    """Event types: detection, analysis, decision, external_api, retry, approval,
    issue_create, issue_update, resolution, failure, cycle."""

    def __init__(self, store: Store, secrets: Iterable[str] = ()):
        self.store = store
        self.redact = Redactor(secrets)

    def record(
        self,
        event_type: str,
        action: str,
        *,
        incident_id: Optional[str] = None,
        input: Any = None,
        output: Any = None,
        result: str = "ok",
        error: Any = None,
        strict: bool = False,
    ) -> Optional[str]:
        """Append an audit record. With ``strict=True`` a storage failure raises
        StoreError (used before external writes so we never act un-audited)."""
        record = {
            "id": new_id("aud"),
            "ts": iso(utcnow()),
            "incident_id": incident_id,
            "event_type": event_type,
            "action": action,
            "input_summary": self.redact(_summarise(input)),
            "output_summary": self.redact(_summarise(output)),
            "result": result,
            "error": self.redact(_summarise(str(error))) if error is not None else None,
        }
        level = logging.WARNING if result in {"error", "failed"} else logging.INFO
        log_event(event_type, level, action=action, incident_id=incident_id, result=result,
                  input=record["input_summary"], output=record["output_summary"], error=record["error"])
        try:
            self.store.append_audit(record)
        except StoreError:
            log_event("audit_write_failed", logging.ERROR, action=action, incident_id=incident_id)
            if strict:
                raise
            return None
        return record["id"]
