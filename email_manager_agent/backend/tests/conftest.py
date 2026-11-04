"""Shared fakes: a scripted model, an in-memory bridge, a fixed clock."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import pytest
from starlette.testclient import TestClient

from app.agent.llm import LLMResponse, ToolCall
from app.config import Settings
from app.errors import BridgeError
from app.main import create_app
from app.services.base import Context

SECRET = "s3cret-value-for-tests-0123456789"
# Monday 2 Nov 2026, 10:00 UTC
NOW = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)


def make_settings(**overrides: Any) -> Settings:
    base = dict(
        llm_provider="ollama", ollama_model="qwen3:8b",
        apps_script_url="https://script.google.com/macros/s/abc/exec", apps_script_shared_secret=SECRET,
        user_timezone="UTC",
    )
    base.update(overrides)
    return Settings(**base)


class Clock:
    def __init__(self, now: datetime = NOW):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


EVENTS = [
    {"id": "ev1", "title": "Standup", "start": "2026-11-02T11:00:00+00:00", "end": "2026-11-02T11:30:00+00:00",
     "all_day": False, "attendees": ["bob@example.com"], "status": "confirmed", "location": "", "html_link": "",
     "organizer_self": True, "transparency": "opaque", "self_response": "accepted"},
    {"id": "ev2", "title": "Planning", "start": "2026-11-03T15:00:00+00:00", "end": "2026-11-03T16:00:00+00:00",
     "all_day": False, "attendees": [], "status": "confirmed", "location": "", "html_link": "",
     "organizer_self": True, "transparency": "opaque", "self_response": None},
]

MESSAGE = {
    "id": "m1", "thread_id": "t1", "from": "Alice <alice@example.com>", "to": "me@example.com", "cc": "",
    "subject": "Invoice October", "date": "2026-11-02T08:00:00+00:00", "labels": ["INBOX", "UNREAD"],
    "body": "Hi, please find the invoice attached. SECRET-BODY-TEXT",
    "attachments": [
        {"attachment_id": "a1", "filename": "invoice.pdf", "mime_type": "application/pdf", "size": 120_000},
        {"attachment_id": "a2", "filename": "huge.zip", "mime_type": "application/zip", "size": 30 * 1024 * 1024},
    ],
}


class FakeBridge:
    """Records calls; answers from per-action handlers (data or callable)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.events = copy.deepcopy(EVENTS)
        self.handlers: dict[str, Any] = {
            "ping": {"ok": True, "email": "me@example.com", "timezone": "UTC", "time": NOW.isoformat()},
            "search_emails": {"messages": [{"id": "m1", "thread_id": "t1", "from": MESSAGE["from"], "to": "me@example.com",
                                            "subject": MESSAGE["subject"], "date": MESSAGE["date"], "snippet": "Hi",
                                            "labels": ["INBOX"], "unread": True, "has_attachments": True}],
                              "result_size_estimate": 1},
            "read_email": copy.deepcopy(MESSAGE),
            "list_labels": {"labels": [{"id": "INBOX", "name": "INBOX", "type": "system"},
                                       {"id": "Label_1", "name": "Receipts", "type": "user"}]},
            "list_drafts": {"drafts": [{"id": "d1", "message_id": "dm1", "to": ["x@example.com"], "subject": "Hello",
                                        "snippet": "Hi"}]},
            "get_draft": {"id": "d1", "message_id": "dm1", "to": ["x@example.com"], "cc": [], "bcc": [],
                          "subject": "Hello", "body": "Draft body"},
            "list_events": self._list_events,
            "get_event": self._get_event,
            "search_drive_files": {"files": [{"id": "f1", "name": "invoice.pdf", "mime_type": "application/pdf",
                                              "modified_time": NOW.isoformat(), "size": 1000, "web_view_link": "https://x",
                                              "parents": ["root"]}]},
            "get_file": lambda p: {"id": p["file_id"], "name": f"name-{p['file_id']}", "mime_type": "x"},
            "send_email": {"message_id": "sent1", "thread_id": "t9"},
            "reply_to_email": {"message_id": "r1", "thread_id": "t1"},
            "forward_email": {"message_id": "fw1", "thread_id": "t5", "skipped_attachments": []},
            "create_draft": {"draft_id": "d2", "message_id": "dm2"},
            "update_draft": {"draft_id": "d1", "message_id": "dm1"},
            "send_draft": {"message_id": "sd1", "thread_id": "t7"},
            "create_label": {"id": "Label_2", "name": "New"},
            "modify_labels": {"modified": 1},
            "create_event": lambda p: {"id": "ev9", "title": p["title"], "start": p["start"], "end": p["end"]},
            "move_event": lambda p: {"id": p["event_id"], "start": p["new_start"], "end": p["new_end"]},
            "cancel_event": lambda p: {"cancelled": True, "event_id": p["event_id"]},
            "create_drive_folder": {"id": "fold1", "name": "X"},
            "move_drive_file": {"id": "f1", "name": "invoice.pdf"},
            "save_attachments_to_drive": {"folder": {"id": "fold", "name": "Email Attachments", "web_view_link": ""},
                                          "saved": [{"filename": "invoice.pdf", "file_id": "nf", "web_view_link": ""}],
                                          "skipped": []},
        }

    def _list_events(self, p: dict[str, Any]) -> dict[str, Any]:
        s, e = datetime.fromisoformat(p["start"]), datetime.fromisoformat(p["end"])
        evs = [ev for ev in self.events
               if datetime.fromisoformat(ev["start"]) < e and datetime.fromisoformat(ev["end"]) > s]
        return {"events": evs[: p.get("max_results", 25)]}

    def _get_event(self, p: dict[str, Any]) -> dict[str, Any]:
        for ev in self.events:
            if ev["id"] == p["event_id"]:
                return copy.deepcopy(ev)
        raise BridgeError("Google reported an error: not found", bridge_code="not_found")

    def fail(self, action: str, code: str = "internal", message: str = "boom") -> None:
        def raiser(_: dict[str, Any]) -> Any:
            raise BridgeError(message, bridge_code=code)
        self.handlers[action] = raiser

    def call(self, action: str, params: dict[str, Any] | None = None) -> Any:
        params = copy.deepcopy(params or {})
        self.calls.append((action, params))
        handler = self.handlers.get(action)
        if handler is None:
            raise BridgeError(f"Unknown action: {action}", bridge_code="unknown_action")
        return handler(params) if callable(handler) else copy.deepcopy(handler)

    def actions(self) -> list[str]:
        return [a for a, _ in self.calls]


class FakeLLM:
    provider = "ollama"
    model = "fake"

    def __init__(self, *responses: LLMResponse | Exception) -> None:
        self.queue: list[LLMResponse | Exception] = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.ping_result: tuple[bool | None, str | None] = (True, None)

    def push(self, *responses: LLMResponse | Exception) -> None:
        self.queue.extend(responses)

    def chat(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        self.calls.append({"system": system, "messages": copy.deepcopy(messages), "tools": tools})
        if not self.queue:
            return LLMResponse(text="(fake: done)")
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def ping(self) -> tuple[bool | None, str | None]:
        return self.ping_result


def say(text: str) -> LLMResponse:
    return LLMResponse(text=text)


_counter = {"n": 0}


def call(tool_name: str, /, **args: Any) -> LLMResponse:
    _counter["n"] += 1
    return LLMResponse(text="", tool_calls=[ToolCall(id=f"c{_counter['n']}", name=tool_name, arguments=args)],
                       stop_reason="tool_use")


def calls(*pairs: tuple[str, dict[str, Any]]) -> LLMResponse:
    out = []
    for name, args in pairs:
        _counter["n"] += 1
        out.append(ToolCall(id=f"c{_counter['n']}", name=name, arguments=args))
    return LLMResponse(text="", tool_calls=out, stop_reason="tool_use")


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def bridge() -> FakeBridge:
    return FakeBridge()


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def ctx(bridge: FakeBridge, settings: Settings, clock: Clock) -> Context:
    return Context(bridge=bridge, settings=settings, now=clock)


ClientFactory = Callable[..., TestClient]


@pytest.fixture
def make_client(bridge: FakeBridge, llm: FakeLLM, clock: Clock) -> ClientFactory:
    def factory(settings: Settings | None = None, *, use_llm: bool = True, use_bridge: bool = True,
                base_url: str = "http://localhost", client_ip: str = "127.0.0.1", **kw: Any) -> TestClient:
        s = settings or make_settings()
        app = create_app(s, llm=llm if use_llm else None, bridge=bridge if use_bridge else None, clock=clock)
        if not use_llm:
            app.state.deps.llm = None
        if not use_bridge:
            app.state.deps.bridge = None
        return TestClient(app, base_url=base_url, client=(client_ip, 50000), **kw)
    return factory


@pytest.fixture
def client(make_client: ClientFactory) -> TestClient:
    return make_client()
