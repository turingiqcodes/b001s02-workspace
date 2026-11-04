"""FastAPI app: chat endpoints plus an approval endpoint for pending actions.

Run:  uvicorn app.main:app --reload --port 8000   (from backend/)
"""

from __future__ import annotations

import logging
from typing import Any

import anthropic
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .agent import EmailAgent, Session, TurnConflict
from .bridge import build_bridge
from .config import Settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("email_manager")


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


class Decision(BaseModel):
    action_id: str
    approve: bool


class DecisionsIn(BaseModel):
    decisions: list[Decision] = Field(min_length=1)


def create_app(settings: Settings | None = None, agent: EmailAgent | None = None) -> FastAPI:
    settings = settings or Settings()
    if agent is None:
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else anthropic.Anthropic()
        agent = EmailAgent(client=client, bridge=build_bridge(settings), settings=settings)

    app = FastAPI(title="Email Manager Agent", version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )
    app.state.agent = agent

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": "The request was missing something or malformed."})

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled error", exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "Something went wrong on our side. Please try again."})

    def _session(session_id: str) -> Session:
        session = agent.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="This conversation has expired. Start a new one.")
        return session

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "model": settings.model,
            "bridge_mode": settings.bridge_mode,
            "anthropic_key_configured": bool(settings.anthropic_api_key),
        }

    @app.post("/api/sessions")
    def create_session() -> dict[str, str]:
        return {"session_id": agent.new_session().id}

    @app.delete("/api/sessions/{session_id}")
    def delete_session(session_id: str) -> dict[str, bool]:
        agent.delete_session(session_id)
        return {"deleted": True}

    @app.post("/api/sessions/{session_id}/messages")
    def send_message(session_id: str, body: MessageIn) -> dict[str, Any]:
        session = _session(session_id)
        try:
            result = agent.send_message(session, body.text.strip())
        except TurnConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return result.to_dict(session.id)

    @app.post("/api/sessions/{session_id}/decisions")
    def decide(session_id: str, body: DecisionsIn) -> dict[str, Any]:
        session = _session(session_id)
        try:
            result = agent.decide(session, {d.action_id: d.approve for d in body.decisions})
        except TurnConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return result.to_dict(session.id)

    return app


app = create_app()
