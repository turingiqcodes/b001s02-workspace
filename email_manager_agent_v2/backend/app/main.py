"""FastAPI application factory. Dependencies (bridge, LLM) can be injected so
tests run without Google or Groq (NFR-19)."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.agent.llm import GroqClient, LLMClient, UnconfiguredLLM
from app.agent.loop import AgentRunner
from app.api.routes import router
from app.config import Settings, load_settings
from app.errors import AppError
from app.services.base import ToolContext
from app.services.bridge import BridgeClient, HttpBridgeClient, UnconfiguredBridge
from app.sessions import SessionStore

log = logging.getLogger("email_manager")


class ProfileCache:
    """Caches the connected account (email, time zone) from the bridge."""

    def __init__(self, bridge: BridgeClient, ttl_seconds: float = 600):
        self._bridge = bridge
        self._ttl = ttl_seconds
        self._value: dict[str, Any] | None = None
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    async def get(self) -> dict[str, Any]:
        async with self._lock:
            if self._value is None or time.monotonic() - self._fetched_at > self._ttl:
                self._value = await self._bridge.call("get_profile", {}) or {}
                self._fetched_at = time.monotonic()
            return self._value


def create_app(
    settings: Settings | None = None,
    bridge: BridgeClient | None = None,
    llm: LLMClient | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    if bridge is None:
        bridge = (
            HttpBridgeClient(settings.apps_script_url, settings.apps_script_secret, settings.bridge_timeout_seconds)
            if settings.bridge_configured
            else UnconfiguredBridge()
        )
    if llm is None:
        llm = GroqClient(settings) if settings.llm_configured else UnconfiguredLLM()

    app = FastAPI(title="Email Manager Agent", version="1.0.0")
    store = SessionStore()
    profile = ProfileCache(bridge)
    ctx = ToolContext(bridge=bridge, settings=settings, get_profile=profile.get)

    app.state.settings = settings
    app.state.bridge = bridge
    app.state.store = store
    app.state.profile = profile
    app.state.runner = AgentRunner(llm, ctx, store)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message, "code": exc.code})

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        parts = []
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body") or "request"
            parts.append(f"{loc}: {str(err.get('msg', 'invalid')).removeprefix('Value error, ')}")
        return JSONResponse(
            status_code=422, content={"detail": "Invalid request - " + "; ".join(parts), "code": "invalid_request"}
        )

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", exc_info=exc)
        return JSONResponse(
            status_code=500, content={"detail": "Something went wrong on the server. Please try again.", "code": "internal_error"}
        )

    app.include_router(router)
    return app
