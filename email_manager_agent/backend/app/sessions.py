"""In-memory sessions. Restarting the backend forgets them."""

from __future__ import annotations

import secrets
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from .approvals import PendingOperation


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Session:
    id: str
    last_used: datetime
    history: list[dict[str, Any]] = field(default_factory=list)
    pending: "OrderedDict[str, PendingOperation]" = field(default_factory=OrderedDict)
    used_operations: set[str] = field(default_factory=set)
    executed: set[str] = field(default_factory=set)  # signatures of executed writes (repeat warning)
    updates: list[str] = field(default_factory=list)  # approval outcomes the model hasn't seen yet
    lock: threading.Lock = field(default_factory=threading.Lock)


class SessionStore:
    def __init__(self, ttl_seconds: int, max_sessions: int, clock: Callable[[], datetime] = utcnow):
        self._ttl = timedelta(seconds=ttl_seconds)
        self._max = max_sessions
        self._clock = clock
        self._sessions: OrderedDict[str, Session] = OrderedDict()
        self._lock = threading.Lock()

    def create(self) -> Session:
        session = Session(id=secrets.token_hex(16), last_used=self._clock())
        with self._lock:
            self._purge()
            self._sessions[session.id] = session
            while len(self._sessions) > self._max:
                self._sessions.popitem(last=False)
        return session

    def get(self, session_id: str | None) -> Session | None:
        if not session_id:
            return None
        with self._lock:
            self._purge()
            session = self._sessions.get(session_id)
            if session is not None:
                session.last_used = self._clock()
                self._sessions.move_to_end(session_id)
            return session

    def delete(self, session_id: str | None) -> None:
        if not session_id:
            return
        with self._lock:
            self._sessions.pop(session_id, None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)

    def _purge(self) -> None:
        cutoff = self._clock() - self._ttl
        for sid in [sid for sid, s in self._sessions.items() if s.last_used < cutoff and not s.lock.locked()]:
            del self._sessions[sid]
