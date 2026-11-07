"""Failure fingerprints: sha256(dag_id | task_id | normalized_error_line).

The same underlying error must always produce the same fingerprint, even when
run ids, timestamps, IPs or counters change between occurrences.
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional

_ERROR_LINE_HINTS = re.compile(
    r"(error|exception|traceback|failed|failure|timeout|timed out|refused|denied|not found|"
    r"does not exist|undefined|invalid|cannot|can't|unable|killed|sigterm|sigkill|oom)",
    re.IGNORECASE,
)
_LOG_PREFIX = re.compile(
    r"^\s*\[?\d{4}-\d{2}-\d{2}[ T][\d:.,]+(?:Z|[+-]\d{2}:?\d{2})?\]?\s*"
    r"(?:\{[^}]*\}\s*)?(?:(?:DEBUG|INFO|WARNING|ERROR|CRITICAL)\s*-?\s*)?"
)

_SUBSTITUTIONS: list[tuple[re.Pattern[str], str]] = [
    # Airflow run ids: manual__2026-11-06T09:00:00+00:00, scheduled__..., backfill__..., asset_triggered__...
    (re.compile(r"\b(?:manual|scheduled|backfill|dataset_triggered|asset_triggered)__\S+"), "<RUN_ID>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "<UUID>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?"), "<TS>"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), "<DATE>"),
    (re.compile(r"\b\d{1,2}:\d{2}:\d{2}(?:[.,]\d+)?\b"), "<TIME>"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?\b"), "<IP>"),
    (re.compile(r"\b(?:[0-9a-f]{1,4}:){4,7}[0-9a-f]{1,4}\b", re.I), "<IP>"),
    (re.compile(r"\b0x[0-9a-f]+\b", re.I), "<HEX>"),
    (re.compile(r"\b[0-9a-f]{12,}\b", re.I), "<HEX>"),
    # Numbers, including ones with a unit suffix ("21s", "30.5ms", "512MB" -> "<N>s", "<N>ms", "<N>MB").
    # The leading \b keeps identifiers such as "md5" or "v2" intact.
    (re.compile(r"\b\d+(?:\.\d+)?(?=[A-Za-z%]*\b)"), "<N>"),
]


def pick_error_line(error_text: str) -> str:
    """Choose the single most informative line of an error / traceback.

    Preference: the last line that looks like an exception (``SomeError: msg``),
    then the last line containing an error keyword, then the last non-empty line.
    """
    lines = [ln.strip() for ln in (error_text or "").splitlines() if ln.strip()]
    if not lines:
        return ""
    exception_like = re.compile(r"^[A-Za-z_][\w.]*(Error|Exception|Exit|Timeout|Interrupt)\b.*")
    for line in reversed(lines):
        cleaned = _LOG_PREFIX.sub("", line)
        if exception_like.match(cleaned):
            return cleaned
    for line in reversed(lines):
        if _ERROR_LINE_HINTS.search(line):
            return _LOG_PREFIX.sub("", line)
    return _LOG_PREFIX.sub("", lines[-1])


def normalize_error_line(error_text: str) -> str:
    line = pick_error_line(error_text)
    for pattern, replacement in _SUBSTITUTIONS:
        line = pattern.sub(replacement, line)
    line = re.sub(r"\s+", " ", line).strip()
    return line[:500]


def fingerprint(dag_id: str, task_id: Optional[str], error_text: str) -> tuple[str, str]:
    """Return (fingerprint_hex, normalized_error_line)."""
    normalized = normalize_error_line(error_text)
    raw = f"{dag_id}|{task_id or ''}|{normalized}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest(), normalized
