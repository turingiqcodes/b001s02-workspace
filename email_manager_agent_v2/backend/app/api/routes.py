"""REST endpoints (API-01..API-04)."""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.agent.loop import AgentRunner
from app.approvals import decline_action, execute_action
from app.errors import AppError, NotConfiguredError, NotFoundError, SessionBusyError
from app.schemas import (
    ConfirmRequest,
    ConfirmResponse,
    PendingActionOut,
    PromptRequest,
    PromptResponse,
    ResetRequest,
    ResetResponse,
    StatusResponse,
)
from app.sessions import Session

router = APIRouter(prefix="/api")


def _busy_guard(session: Session) -> None:
    # NFR-12: one request per session at a time.
    if session.lock.locked():
        raise SessionBusyError("I'm still working on your previous request. Please wait for it to finish.")


@router.get("/status", response_model=StatusResponse)
async def status(request: Request) -> StatusResponse:
    state = request.app.state
    settings = state.settings
    email = tz = detail = None
    if settings.bridge_configured:
        try:
            profile = await state.profile.get()
            email, tz = profile.get("email"), profile.get("time_zone")
        except AppError as exc:
            detail = exc.message
    if not settings.llm_configured or not settings.bridge_configured:
        overall = "not_configured"
        missing = [n for n, ok in (("GROQ_API_KEY", settings.llm_configured),
                                    ("APPS_SCRIPT_URL / APPS_SCRIPT_SECRET", settings.bridge_configured)) if not ok]
        detail = detail or f"Missing in backend/.env: {', '.join(missing)}"
    elif email is None:
        overall = "bridge_error"
    else:
        overall = "ready"
    return StatusResponse(
        llm_configured=settings.llm_configured,
        llm_model=settings.groq_model,
        bridge_configured=settings.bridge_configured,
        gmail_connected=email is not None,
        gmail_address=email,
        time_zone=tz,
        status=overall,
        detail=detail,
    )


@router.post("/prompt", response_model=PromptResponse)
async def submit_prompt(body: PromptRequest, request: Request) -> PromptResponse:
    state = request.app.state
    if not state.settings.llm_configured:
        raise NotConfiguredError("The AI model isn't configured. Set GROQ_API_KEY in backend/.env and restart.")
    session = state.store.get_or_create(body.session_id)
    _busy_guard(session)
    runner: AgentRunner = state.runner
    async with session.lock:
        previously_pending = [a.action_id for a in session.pending()]
        session.agent_state = "running"
        try:
            result = await runner.run(session, body.prompt)
        finally:
            session.agent_state = "idle"
    return PromptResponse(
        session_id=session.session_id,
        response=result.response,
        pending_actions=[PendingActionOut(**a.to_public()) for a in result.new_actions],
        declined_actions=previously_pending,
    )


@router.post("/confirm", response_model=ConfirmResponse)
async def confirm(body: ConfirmRequest, request: Request) -> ConfirmResponse:
    state = request.app.state
    found = state.store.find_action(body.action_id)
    if not found:
        raise NotFoundError("That action wasn't found. It may have expired or the session was reset.")
    session, action = found
    _busy_guard(session)
    async with session.lock:
        if body.approved:
            await execute_action(session, action, state.bridge)
        else:
            decline_action(session, action, "the user clicked Cancel")
    return ConfirmResponse(action_id=action.action_id, status=action.status, message=action.message or "")


@router.post("/reset", response_model=ResetResponse)
async def reset(request: Request, body: ResetRequest | None = None) -> ResetResponse:
    store = request.app.state.store
    session_id = body.session_id if body else None
    session = store.get(session_id)
    if session is not None:
        _busy_guard(session)
    store.reset(session_id)
    return ResetResponse()
