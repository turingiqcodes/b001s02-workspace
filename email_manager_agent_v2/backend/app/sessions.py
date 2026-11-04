"""In-memory sessions (section 7.1). Lost on server restart by design (NFR-16)."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

ActionStatus = Literal["pending", "approved", "declined", "executed", "failed"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class PendingAction:
    """Section 7.2. `arguments` is the exact bridge payload that runs on approval;
    nothing (including the model) can change it after the preview is created (FR-35)."""

    action_id: str
    session_id: str
    operation: str  # tool name the user sees, e.g. "draft_reply"
    bridge_operation: str
    arguments: dict[str, Any]
    preview: dict[str, Any]
    status: ActionStatus = "pending"
    created_at: datetime = field(default_factory=_now)
    message: str | None = None
    result: Any = None

    def to_public(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "operation": self.operation,
            "status": "needs_confirmation" if self.status == "pending" else self.status,
            "preview": self.preview,
            "created_at": self.created_at.isoformat(),
            "message": self.message,
        }


@dataclass
class Session:
    session_id: str
    conversation: list[dict[str, Any]] = field(default_factory=list)
    pending_actions: dict[str, PendingAction] = field(default_factory=dict)
    agent_state: Literal["idle", "running"] = "idle"
    created_at: datetime = field(default_factory=_now)
    # Outcomes of approvals/cancellations, told to the agent on its next turn (FR-36).
    agent_notes: list[str] = field(default_factory=list)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def pending(self) -> list[PendingAction]:
        return [a for a in self.pending_actions.values() if a.status == "pending"]


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._action_index: dict[str, str] = {}

    def create(self) -> Session:
        session = Session(session_id=f"session_{uuid.uuid4().hex[:12]}")
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str | None) -> Session | None:
        return self._sessions.get(session_id) if session_id else None

    def get_or_create(self, session_id: str | None) -> Session:
        return self.get(session_id) or self.create()

    def add_action(self, session: Session, action: PendingAction) -> None:
        session.pending_actions[action.action_id] = action
        self._action_index[action.action_id] = session.session_id

    def find_action(self, action_id: str) -> tuple[Session, PendingAction] | None:
        session = self.get(self._action_index.get(action_id))
        if session and action_id in session.pending_actions:
            return session, session.pending_actions[action_id]
        return None

    def reset(self, session_id: str | None) -> None:
        ids = [session_id] if session_id else list(self._sessions)
        for sid in ids:
            session = self._sessions.pop(sid, None)
            if session:
                for aid in session.pending_actions:
                    self._action_index.pop(aid, None)


def new_action_id() -> str:
    return f"action_{uuid.uuid4().hex[:12]}"
