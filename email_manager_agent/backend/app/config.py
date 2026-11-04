"""All settings and limits, read from ``email_manager_agent/.env`` and the environment.

Environment variables override values in ``.env``. List values use JSON
(``WORK_DAYS=[0,1,2,3,4]``). Malformed values raise :class:`ConfigError` at
startup; missing values that only some features need (model, bridge) are
reported as *issues* so the app still starts and the status bar can explain.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
PROVIDERS = ("ollama", "claude")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


class ConfigError(ValueError):
    """A setting in .env or the environment has an invalid value."""


def _get(env: Mapping[str, str | None], key: str, default: str | None = None) -> str | None:
    value = env.get(key)
    if value is None:
        return default
    value = value.strip()
    # Tolerate quotes around values copied into .env by hand.
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1].strip()
    return value if value != "" else default


def _int(env: Mapping[str, str | None], key: str, default: int, lo: int, hi: int) -> int:
    raw = _get(env, key)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a whole number (got {raw!r}).") from exc
    if not lo <= value <= hi:
        raise ConfigError(f"{key} must be between {lo} and {hi} (got {value}).")
    return value


def _float(env: Mapping[str, str | None], key: str, default: float, lo: float, hi: float) -> float:
    raw = _get(env, key)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number (got {raw!r}).") from exc
    if not lo <= value <= hi:
        raise ConfigError(f"{key} must be between {lo:g} and {hi:g} (got {value:g}).")
    return value


def _bool(env: Mapping[str, str | None], key: str, default: bool | None) -> bool | None:
    raw = _get(env, key)
    if raw is None:
        return default
    lowered = raw.lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{key} must be true or false (got {raw!r}).")


def _time(env: Mapping[str, str | None], key: str, default: str) -> str:
    raw = _get(env, key, default) or default
    if not _TIME_RE.match(raw):
        raise ConfigError(f"{key} must be a 24-hour time like 09:00 (got {raw!r}).")
    return raw


def _work_days(env: Mapping[str, str | None]) -> tuple[int, ...]:
    raw = _get(env, "WORK_DAYS")
    if raw is None:
        return (0, 1, 2, 3, 4)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"WORK_DAYS must be a JSON list like [0,1,2,3,4] (got {raw!r}).") from exc
    if (not isinstance(value, list) or not value
            or not all(isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6 for d in value)):
        raise ConfigError("WORK_DAYS must be a non-empty JSON list of days, Monday=0 to Sunday=6.")
    return tuple(sorted(set(value)))


def is_loopback_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class Settings:
    # Model
    llm_provider: str = "ollama"
    ollama_model: str | None = None
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_num_ctx: int = 16384
    ollama_timeout_seconds: float = 600.0
    ollama_keep_alive: str = "30m"
    ollama_think: bool | None = None
    anthropic_api_key: str | None = None
    claude_model: str | None = None
    claude_effort: str = "medium"
    claude_refusal_fallback: bool = True
    # Bridge
    apps_script_url: str | None = None
    apps_script_shared_secret: str | None = None
    bridge_timeout_seconds: float = 30.0
    # User
    user_timezone: str = "UTC"
    work_days: tuple[int, ...] = (0, 1, 2, 3, 4)
    work_start: str = "09:00"
    work_end: str = "18:00"
    # Limits
    max_agent_steps: int = 8
    max_prompt_chars: int = 4000
    max_search_results: int = 25
    max_email_body_chars: int = 20000
    max_attachment_mb: int = 20
    max_recipients: int = 20
    approval_ttl_seconds: int = 600
    session_ttl_seconds: int = 7200
    max_sessions: int = 50
    max_tool_result_chars: int = 12000
    # Server
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    frontend_origin: str = "http://localhost:5173"
    log_level: str = "INFO"
    timezone_issue: str | None = field(default=None, compare=False)

    # -- loading ---------------------------------------------------------------
    @classmethod
    def from_env(cls, env: Mapping[str, str | None] | None = None) -> "Settings":
        """Build settings from a mapping. With no mapping: ``.env`` overlaid by ``os.environ``."""
        if env is None:
            merged: dict[str, str | None] = {}
            if ENV_FILE.is_file():
                merged.update(dotenv_values(ENV_FILE))
            merged.update(os.environ)
            env = merged

        provider = (_get(env, "LLM_PROVIDER", "ollama") or "ollama").lower()
        if provider not in PROVIDERS:
            raise ConfigError(f"LLM_PROVIDER must be 'ollama' or 'claude' (got {provider!r}).")
        effort = (_get(env, "CLAUDE_EFFORT", "medium") or "medium").lower()
        if effort not in EFFORT_LEVELS:
            raise ConfigError(f"CLAUDE_EFFORT must be one of {', '.join(EFFORT_LEVELS)} (got {effort!r}).")

        work_start = _time(env, "WORK_START", "09:00")
        work_end = _time(env, "WORK_END", "18:00")
        if work_end <= work_start:
            raise ConfigError("WORK_END must be later than WORK_START.")

        tz = _get(env, "USER_TIMEZONE", "UTC") or "UTC"
        tz_issue = None
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            tz_issue = f"USER_TIMEZONE '{tz}' is not a valid IANA timezone; using UTC."
            tz = "UTC"

        log_level = (_get(env, "LOG_LEVEL", "INFO") or "INFO").upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigError(f"LOG_LEVEL must be DEBUG, INFO, WARNING or ERROR (got {log_level!r}).")

        return cls(
            llm_provider=provider,
            ollama_model=_get(env, "OLLAMA_MODEL"),
            ollama_base_url=(_get(env, "OLLAMA_BASE_URL", "http://127.0.0.1:11434") or "").rstrip("/"),
            ollama_num_ctx=_int(env, "OLLAMA_NUM_CTX", 16384, 2048, 1_048_576),
            ollama_timeout_seconds=_float(env, "OLLAMA_TIMEOUT_SECONDS", 600.0, 5, 7200),
            ollama_keep_alive=_get(env, "OLLAMA_KEEP_ALIVE", "30m") or "30m",
            ollama_think=_bool(env, "OLLAMA_THINK", None),
            anthropic_api_key=_get(env, "ANTHROPIC_API_KEY"),
            claude_model=_get(env, "CLAUDE_MODEL"),
            claude_effort=effort,
            claude_refusal_fallback=bool(_bool(env, "CLAUDE_REFUSAL_FALLBACK", True)),
            apps_script_url=_get(env, "APPS_SCRIPT_URL"),
            apps_script_shared_secret=_get(env, "APPS_SCRIPT_SHARED_SECRET"),
            bridge_timeout_seconds=_float(env, "BRIDGE_TIMEOUT_SECONDS", 30.0, 1, 600),
            user_timezone=tz,
            work_days=_work_days(env),
            work_start=work_start,
            work_end=work_end,
            max_agent_steps=_int(env, "MAX_AGENT_STEPS", 8, 1, 50),
            max_prompt_chars=_int(env, "MAX_PROMPT_CHARS", 4000, 100, 100_000),
            max_search_results=_int(env, "MAX_SEARCH_RESULTS", 25, 1, 100),
            max_email_body_chars=_int(env, "MAX_EMAIL_BODY_CHARS", 20000, 500, 500_000),
            max_attachment_mb=_int(env, "MAX_ATTACHMENT_MB", 20, 1, 50),
            max_recipients=_int(env, "MAX_RECIPIENTS", 20, 1, 100),
            approval_ttl_seconds=_int(env, "APPROVAL_TTL_SECONDS", 600, 10, 86_400),
            session_ttl_seconds=_int(env, "SESSION_TTL_SECONDS", 7200, 60, 604_800),
            max_sessions=_int(env, "MAX_SESSIONS", 50, 1, 10_000),
            backend_host=_get(env, "BACKEND_HOST", "127.0.0.1") or "127.0.0.1",
            backend_port=_int(env, "BACKEND_PORT", 8000, 1, 65535),
            frontend_origin=(_get(env, "FRONTEND_ORIGIN", "http://localhost:5173") or "").rstrip("/"),
            log_level=log_level,
            timezone_issue=tz_issue,
        )

    # -- derived ---------------------------------------------------------------
    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.user_timezone)

    @property
    def active_model(self) -> str | None:
        return self.ollama_model if self.llm_provider == "ollama" else self.claude_model

    @property
    def cors_origins(self) -> list[str]:
        origins = [self.frontend_origin]
        for alt in ("http://localhost:5173", "http://127.0.0.1:5173"):
            if alt not in origins:
                origins.append(alt)
        return origins

    def llm_issues(self) -> list[str]:
        issues: list[str] = []
        if self.llm_provider == "ollama":
            if not self.ollama_model:
                issues.append("OLLAMA_MODEL is not set. Set it to the model you pulled, e.g. qwen3:8b.")
            if not self.ollama_base_url.startswith(("http://", "https://")):
                issues.append("OLLAMA_BASE_URL must start with http:// or https://.")
        else:
            if not self.anthropic_api_key:
                issues.append("ANTHROPIC_API_KEY is not set.")
            if not self.claude_model:
                issues.append("CLAUDE_MODEL is not set, e.g. claude-opus-5-5.")
        return issues

    def bridge_issues(self) -> list[str]:
        issues: list[str] = []
        url = self.apps_script_url
        if not url:
            issues.append("APPS_SCRIPT_URL is not set.")
        elif not (url.startswith("https://script.google.com/") and url.rstrip("/").endswith("/exec")):
            issues.append("APPS_SCRIPT_URL must be the Web App URL starting with https://script.google.com/ and ending in /exec.")
        secret = self.apps_script_shared_secret
        if not secret:
            issues.append("APPS_SCRIPT_SHARED_SECRET is not set.")
        elif len(secret) < 16:
            issues.append("APPS_SCRIPT_SHARED_SECRET must be at least 16 characters.")
        return issues

    def limits(self) -> dict[str, object]:
        return {
            "max_agent_steps": self.max_agent_steps,
            "max_prompt_chars": self.max_prompt_chars,
            "approval_ttl_seconds": self.approval_ttl_seconds,
            "max_search_results": self.max_search_results,
            "max_attachment_mb": self.max_attachment_mb,
            "work_days": list(self.work_days),
            "work_start": self.work_start,
            "work_end": self.work_end,
        }
