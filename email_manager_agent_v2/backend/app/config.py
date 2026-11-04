"""Application configuration loaded from environment variables (backend/.env).

Secrets (GROQ_API_KEY, APPS_SCRIPT_SECRET) live only here and are never
returned by the API or written to logs (NFR-01, NFR-18).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent

VALID_REASONING_EFFORTS = {"", "low", "medium", "high"}


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


@dataclass(frozen=True)
class Settings:
    groq_api_key: str = field(default="", repr=False)
    groq_model: str = "openai/gpt-oss-120b"
    # low | medium | high for reasoning models such as gpt-oss; empty = don't send
    groq_reasoning_effort: str = "medium"
    groq_max_tokens: int = 8192

    apps_script_url: str = ""
    apps_script_secret: str = field(default="", repr=False)
    bridge_timeout_seconds: int = 90  # NFR-09: Apps Script cold starts

    agent_max_steps: int = 15  # FR-04

    # NFR-10 context limits (SRS maximums: 6000 / 3000 / 60000). Defaults are lower so
    # requests fit Groq's free-tier tokens-per-minute limit; raise them on a paid tier.
    email_body_limit: int = 2500
    list_body_limit: int = 300
    tool_result_limit: int = 8000
    # Approximate character budget for the whole conversation sent to the model.
    history_char_budget: int = 10000

    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")

    def __repr__(self) -> str:  # never print secrets
        return (
            f"Settings(groq_model={self.groq_model!r}, llm_configured={self.llm_configured}, "
            f"bridge_configured={self.bridge_configured}, agent_max_steps={self.agent_max_steps})"
        )

    @property
    def llm_configured(self) -> bool:
        return bool(self.groq_api_key)

    @property
    def bridge_configured(self) -> bool:
        return bool(self.apps_script_url and self.apps_script_secret)


def load_settings(env_file: Path | None = None) -> Settings:
    load_dotenv(env_file or BACKEND_DIR / ".env", override=False)
    effort = os.getenv("GROQ_REASONING_EFFORT", "medium").strip().lower()
    if effort not in VALID_REASONING_EFFORTS:
        raise ValueError("GROQ_REASONING_EFFORT must be low, medium, high, or empty")
    origins = tuple(
        o.strip() for o in os.getenv("CORS_ORIGINS", "").split(",") if o.strip()
    ) or Settings.cors_origins
    return Settings(
        groq_api_key=os.getenv("GROQ_API_KEY", "").strip(),
        groq_model=os.getenv("GROQ_MODEL", "").strip() or "openai/gpt-oss-120b",
        groq_reasoning_effort=effort,
        groq_max_tokens=_int_env("GROQ_MAX_TOKENS", 8192, 256),
        apps_script_url=os.getenv("APPS_SCRIPT_URL", "").strip(),
        apps_script_secret=os.getenv("APPS_SCRIPT_SECRET", "").strip(),
        bridge_timeout_seconds=_int_env("BRIDGE_TIMEOUT_SECONDS", 90),
        agent_max_steps=_int_env("AGENT_MAX_STEPS", 15),
        email_body_limit=min(_int_env("EMAIL_BODY_LIMIT", 2500, 200), 6000),
        list_body_limit=min(_int_env("LIST_BODY_LIMIT", 300, 0), 3000),
        tool_result_limit=min(_int_env("TOOL_RESULT_LIMIT", 8000, 1000), 60000),
        history_char_budget=_int_env("HISTORY_CHAR_BUDGET", 10000, 3000),
        cors_origins=origins,
    )
