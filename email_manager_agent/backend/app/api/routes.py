"""/api/status, /api/prompt, /api/confirm, /api/reset.

Handlers are plain ``def`` so FastAPI runs them in a worker thread: model and
Google calls block for seconds to minutes.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterator

from fastapi import APIRouter, Query, Request

from .. import approvals
from ..agent.llm import LLMClient
from ..agent.loop import AgentLoop
from ..config import Settings
from ..errors import SESSION_GONE, AppError, BridgeError, ConfigurationError, NotFoundError, SessionBusy, ValidationAppError
from ..schemas import ConfirmIn, ConfirmOut, PromptIn, PromptOut, ResetIn, ResetOut, StatusOut
from ..services.base import Context
from ..services.bridge import Bridge
from ..sessions import Session, SessionStore

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


@dataclass
class Deps:
    settings: Settings
    store: SessionStore
    llm: LLMClient | None
    bridge: Bridge | None
    clock: Callable[[], datetime]

    def context(self) -> Context:
        if self.bridge is None:
            raise ConfigurationError("The Google bridge is not configured: " + " ".join(self.settings.bridge_issues()))
        return Context(bridge=self.bridge, settings=self.settings, now=self.clock)


def deps(request: Request) -> Deps:
    return request.app.state.deps


@contextmanager
def locked(session: Session) -> Iterator[None]:
    if not session.lock.acquire(blocking=False):
        raise SessionBusy("Another request for this session is still running. Wait for the current reply to finish.")
    try:
        yield
    finally:
        session.lock.release()


def _pending(session: Session | None) -> list[dict[str, Any]]:
    return [op.to_public() for op in session.pending.values()] if session else []


@router.get("/status", response_model=StatusOut)
def status(request: Request, session_id: str | None = Query(default=None, max_length=128),
           check_bridge: bool = Query(default=False)) -> dict[str, Any]:
    d = deps(request)
    s = d.settings
    llm_issues, bridge_issues = s.llm_issues(), s.bridge_issues()
    if s.timezone_issue:
        bridge_issues = bridge_issues + [s.timezone_issue]
    llm = {"provider": s.llm_provider, "model": s.active_model, "configured": not s.llm_issues(),
           "issues": llm_issues, "reachable": None, "detail": None}
    bridge = {"configured": not s.bridge_issues(), "issues": bridge_issues, "reachable": None, "detail": None}

    if check_bridge:
        if d.llm is None:
            llm.update(reachable=False, detail="Not configured.")
        else:
            llm["reachable"], llm["detail"] = d.llm.ping()
        if d.bridge is None:
            bridge.update(reachable=False, detail="Not configured.")
        else:
            try:
                d.bridge.call("ping", {})
                bridge["reachable"] = True
            except BridgeError as exc:
                bridge.update(reachable=False, detail=exc.message)

    session = d.store.get(session_id)
    if session is not None and session.lock.acquire(blocking=False):
        try:
            approvals.drop_expired(session, d.clock())
        finally:
            session.lock.release()
    return {"llm": llm, "bridge": bridge, "timezone": s.user_timezone, "limits": s.limits(),
            "session_id": session.id if session else None, "pending_operations": _pending(session)}


@router.post("/prompt", response_model=PromptOut)
def prompt(body: PromptIn, request: Request) -> dict[str, Any]:
    d = deps(request)
    text = body.prompt.strip()
    if not text:
        raise ValidationAppError("Type a request first.")
    if len(text) > d.settings.max_prompt_chars:
        raise ValidationAppError(f"The request is too long (max {d.settings.max_prompt_chars} characters).")
    if body.session_id:
        session = d.store.get(body.session_id)
        if session is None:
            raise NotFoundError(SESSION_GONE)
    else:
        session = None
    if d.llm is None:
        provider = "Ollama" if d.settings.llm_provider == "ollama" else "Claude"
        raise ConfigurationError(f"{provider} is not configured: " + " ".join(d.settings.llm_issues()))
    ctx = d.context()
    if session is None:
        session = d.store.create()
    with locked(session):
        return AgentLoop(d.llm, ctx).run(session, text)


@router.post("/confirm", response_model=ConfirmOut)
def confirm(body: ConfirmIn, request: Request) -> dict[str, Any]:
    d = deps(request)
    session = d.store.get(body.session_id)
    if session is None:
        raise NotFoundError(SESSION_GONE)
    with locked(session):
        try:
            ctx = d.context()
        except AppError:
            if body.decision == "approve":
                raise
            ctx = Context(bridge=None, settings=d.settings, now=d.clock)  # type: ignore[arg-type]
        result = approvals.confirm(session, body.operation_id, body.decision, body.acknowledge_warnings, ctx)
        return {**result, "pending_operations": _pending(session)}


@router.post("/reset", response_model=ResetOut)
def reset(request: Request, body: ResetIn | None = None) -> dict[str, Any]:
    d = deps(request)
    old = d.store.get(body.session_id if body else None)
    if old is not None:
        with locked(old):
            d.store.delete(old.id)
    return {"session_id": d.store.create().id, "status": "reset"}
