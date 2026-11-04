"""Start the backend on BACKEND_HOST:BACKEND_PORT (loopback only)."""

from __future__ import annotations

import sys
from typing import Any, Callable

from app.config import ConfigError, Settings, is_loopback_host


def main(settings: Settings | None = None, serve: Callable[..., Any] | None = None) -> int:
    try:
        settings = settings or Settings.from_env()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    if not is_loopback_host(settings.backend_host):
        print(f"Refusing to listen on {settings.backend_host!r}: this app is local only. "
              "Set BACKEND_HOST=127.0.0.1.", file=sys.stderr)
        return 2
    if serve is None:
        import uvicorn

        serve = uvicorn.run
    from app.main import create_app

    serve(create_app(settings), host=settings.backend_host, port=settings.backend_port,
          log_level=settings.log_level.lower())
    return 0


if __name__ == "__main__":
    sys.exit(main())
