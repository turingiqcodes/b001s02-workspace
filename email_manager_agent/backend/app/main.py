"""FastAPI app: localhost guards, CORS, error handlers, dependency wiring."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .agent.claude_client import ClaudeClient
from .agent.llm import LLMClient
from .agent.ollama_client import OllamaClient
from .api.routes import Deps, router
from .config import Settings, is_loopback_host
from .errors import AppError
from .services.bridge import AppsScriptBridge, Bridge
from .sessions import SessionStore, utcnow

log = logging.getLogger("email_manager")


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def build_llm(settings: Settings) -> LLMClient | None:
    if settings.llm_issues():
        return None
    if settings.llm_provider == "ollama":
        return OllamaClient(settings.ollama_base_url, settings.ollama_model or "", num_ctx=settings.ollama_num_ctx,
                            timeout=settings.ollama_timeout_seconds, keep_alive=settings.ollama_keep_alive,
                            think=settings.ollama_think)
    return ClaudeClient(settings.claude_model or "", api_key=settings.anthropic_api_key,
                        effort=settings.claude_effort, refusal_fallback=settings.claude_refusal_fallback)


def build_bridge(settings: Settings) -> Bridge | None:
    if settings.bridge_issues():
        return None
    return AppsScriptBridge(settings.apps_script_url or "", settings.apps_script_shared_secret or "",
                            timeout=settings.bridge_timeout_seconds)


def _host_name(host_header: str) -> str:
    host = host_header.strip().lower()
    if host.startswith("["):  # [::1]:8000
        return host[1:host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


def create_app(settings: Settings | None = None, llm: LLMClient | None = None, bridge: Bridge | None = None,
               clock: Callable[[], datetime] | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    clock = clock or utcnow

    app = FastAPI(title="Email Manager Agent", version="1.0.0", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.deps = Deps(
        settings=settings,
        store=SessionStore(settings.session_ttl_seconds, settings.max_sessions, clock=clock),
        llm=llm if llm is not None else build_llm(settings),
        bridge=bridge if bridge is not None else build_bridge(settings),
        clock=clock,
    )

    @app.middleware("http")
    async def localhost_only(request: Request, call_next: Any) -> Any:
        if _host_name(request.headers.get("host", "")) not in {"localhost", "127.0.0.1", "::1"}:
            return _error(400, "invalid_host", "Invalid host header. Open the app as http://localhost:5173.")
        client = request.client.host if request.client else ""
        if not is_loopback_host(client):
            return _error(403, "forbidden", "This app only accepts requests from this computer.")
        return await call_next(request)

    # Added after the guard so it wraps it: CORS headers are present even on guard errors.
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type"])

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        if exc.status >= 500:
            log.warning("request failed: %s", exc.code)
        return JSONResponse(status_code=exc.status, content=exc.to_body())

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        first = errors[0] if errors else {}
        where = ".".join(str(p) for p in first.get("loc", ()) if p not in ("body", "query"))
        message = f"{where}: {first.get('msg', 'invalid value')}" if where else "The request body is missing or malformed."
        return _error(422, "validation_error", message)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return _error(exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled error: %s", type(exc).__name__)
        return _error(500, "internal_error", "Something went wrong on our side. Please try again.")

    app.include_router(router)
    return app
