"""Shared types for the Gmail, Calendar and Drive service helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from ..config import Settings
from .bridge import Bridge


@dataclass
class Context:
    bridge: Bridge
    settings: Settings
    now: Callable[[], datetime]


@dataclass
class Proposal:
    """What the approval card shows, and the exact parameters that will run."""

    title: str
    summary: str
    params: dict[str, Any]
    details: list[dict[str, str]] = field(default_factory=list)
    body: str | None = None
    warnings: list[str] = field(default_factory=list)


class ProposalRejected(ValueError):
    """The write can't be proposed (e.g. nothing to save). Reported to the model as an invalid call."""


def join(values: list[str] | None) -> str:
    return ", ".join(values) if values else "—"


def short(text: str | None, limit: int = 80) -> str:
    text = (text or "").strip().replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def fmt_time(iso: str, settings: Settings) -> str:
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    if dt.tzinfo is not None:
        dt = dt.astimezone(settings.tz)
    return dt.strftime("%a %d %b %Y, %H:%M")
