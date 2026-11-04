"""Settings loaded from backend environment variables (backend/.env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


def _bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str | None = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY") or None)
    model: str = field(default_factory=lambda: os.getenv("CLAUDE_MODEL", "claude-opus-5-5"))
    effort: str = field(default_factory=lambda: os.getenv("CLAUDE_EFFORT", "medium"))
    enable_fallbacks: bool = field(default_factory=lambda: _bool(os.getenv("CLAUDE_ENABLE_FALLBACKS"), True))
    bridge_mode: str = field(default_factory=lambda: os.getenv("BRIDGE_MODE", "mock").strip().lower())
    apps_script_url: str | None = field(default_factory=lambda: os.getenv("APPS_SCRIPT_URL") or None)
    bridge_token: str | None = field(default_factory=lambda: os.getenv("BRIDGE_TOKEN") or None)
    user_timezone: str = field(default_factory=lambda: os.getenv("USER_TIMEZONE", "UTC"))
    cors_origins: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()
        )
    )
    max_tool_rounds: int = 15
