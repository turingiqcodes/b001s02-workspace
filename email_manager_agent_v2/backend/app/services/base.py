"""Shared types and helpers for the Gmail / Calendar / Drive service modules."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable

from app.config import Settings
from app.services.bridge import BridgeClient

EMAIL_RE = re.compile(r"^[^@\s<>,;\"]+@[^@\s<>,;\"]+\.[^@\s<>,;\"]+$")
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024  # FR-31


@dataclass
class ToolContext:
    bridge: BridgeClient
    settings: Settings
    get_profile: Callable[[], Awaitable[dict[str, Any]]]


@dataclass
class Preview:
    """What the user sees on an approval card (FR-34, FR-38, 6.1.2)."""

    title: str
    summary: str
    target: dict[str, Any] | None = None
    existing: dict[str, Any] | None = None
    proposed: dict[str, Any] | None = None
    recipients: list[str] | None = None
    availability: dict[str, Any] | None = None
    notify_attendees: bool | None = None
    warnings: list[str] = field(default_factory=list)
    irreversible: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PreparedWrite:
    """A validated write: the preview plus the exact bridge call to run on approval."""

    preview: Preview
    bridge_operation: str
    bridge_args: dict[str, Any]


# ---------- validation helpers ----------

def validate_email(value: str) -> str:
    value = value.strip()
    if not EMAIL_RE.match(value):
        raise ValueError(f"'{value}' is not a valid email address")
    return value


def validate_header_text(value: str) -> str:
    if "\r" in value or "\n" in value:
        raise ValueError("must not contain line breaks")
    return value


def parse_aware_datetime(value: str) -> datetime:
    """Parse ISO 8601 and require a UTC offset so times are never ambiguous."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"'{value}' is not an ISO 8601 date-time (example: 2026-11-05T16:00:00+05:30)"
        ) from exc
    if dt.tzinfo is None:
        raise ValueError(f"'{value}' needs a UTC offset (example: 2026-11-05T16:00:00+05:30)")
    return dt


def coerce_date(value: object) -> object:
    """Accept '2026-11-04' or '2026-11-04T00:00:00+05:30' (models send both); keep the date."""
    if isinstance(value, str) and len(value) > 10 and value[10] in "T ":
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            return value[:10]
    return value


def truncate_text(text: str | None, limit: int) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[truncated, {len(text) - limit} more characters]"


def human_size(num_bytes: int | None) -> str:
    if num_bytes is None:
        return "unknown size"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"
