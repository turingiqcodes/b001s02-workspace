"""Runtime settings, read from the environment (CLI flags in server.py override them)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ALL_TOOLSETS = ("repos", "pulls", "actions", "issues", "search")
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines from a .env file without overriding real environment variables.

    Empty values are skipped so a blank placeholder never masks a token set in the shell.
    """
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip("'\"")
        if value and key not in os.environ:
            os.environ[key] = value


# The project root .env, so it is found even when an MCP client launches the server from another directory.
load_dotenv(Path(os.getenv("GITHUB_MCP_ENV_FILE", PROJECT_ROOT / ".env")))


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _toolsets() -> tuple[str, ...]:
    raw = os.getenv("GITHUB_TOOLSETS", "").strip()
    if not raw or raw == "all":
        return ALL_TOOLSETS
    return tuple(t.strip() for t in raw.split(",") if t.strip())


@dataclass
class Settings:
    token: str | None = field(
        default_factory=lambda: os.getenv("GITHUB_PERSONAL_ACCESS_TOKEN") or os.getenv("GITHUB_TOKEN")
    )
    api_url: str = field(default_factory=lambda: os.getenv("GITHUB_API_URL", "https://api.github.com"))
    toolsets: tuple[str, ...] = field(default_factory=_toolsets)
    read_only: bool = field(default_factory=lambda: _bool("GITHUB_READ_ONLY"))


settings = Settings()
