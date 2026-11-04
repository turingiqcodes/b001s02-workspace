"""Test doubles: an in-memory Google bridge and a scripted LLM (NFR-19).
No test needs Google or an Anthropic API key."""

from __future__ import annotations

import copy
import itertools
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from app.agent.llm import LLMResponse
from app.config import Settings
from app.errors import BridgeError
from app.main import create_app
from app.services.bridge import ALLOWED_OPERATIONS

WRITE_OPS = {
    "create_draft", "update_draft", "delete_draft", "send_draft", "send_email",
    "update_email_labels", "trash_email", "restore_email", "create_calendar_event",
    "update_calendar_event", "delete_calendar_event", "save_attachments_to_drive", "trash_drive_file",
}

MB = 1024 * 1024


def make_email(i: int, **overrides: Any) -> dict[str, Any]:
    email = {
        "id": f"m{i}",
        "thread_id": f"t{i}",
        "thread_message_count": 1,
        "subject": f"Subject {i}",
        "from": f"Sender {i} <sender{i}@example.com>",
        "to": ["me@example.com"],
        "cc": [],
        "date": f"2026-11-0{i % 9 + 1}T10:00:00+05:30",
        "labels": ["INBOX"],
        "unread": True,
        "snippet": "snippet",
        "body": f"Body of email {i}",
        "attachments": [],
        "message_id_header": f"<msg{i}@mail.example.com>",
        "references": "",
        "reply_to": "",
    }
    email.update(overrides)
    return email


class FakeBridge:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail: dict[str, BridgeError] = {}
        self.profile = {"email": "me@example.com", "time_zone": "Asia/Kolkata"}
        self.emails = {
            "m1": make_email(1, subject="Meeting", **{"from": "Priya <priya@example.com>"},
                             cc=["raj@example.com"], to=["me@example.com", "team@example.com"]),
            "m2": make_email(2, attachments=[
                {"name": "report.pdf", "size": 2 * MB, "mime_type": "application/pdf", "attachment_id": "a1"},
                {"name": "video.mp4", "size": 30 * MB, "mime_type": "video/mp4", "attachment_id": "a2"},
            ]),
            "m3": make_email(3, labels=["TRASH"], thread_message_count=3),
        }
        self.labels = [
            {"id": "INBOX", "name": "INBOX", "type": "system"},
            {"id": "UNREAD", "name": "UNREAD", "type": "system"},
            {"id": "STARRED", "name": "STARRED", "type": "system"},
            {"id": "IMPORTANT", "name": "IMPORTANT", "type": "system"},
            {"id": "Work", "name": "Work", "type": "user"},
            {"id": "Receipts", "name": "Receipts", "type": "user"},
        ]
        self.drafts = {
            "d1": {"id": "d1", "message_id": "dm1", "thread_id": "t9", "to": ["bob@example.com"], "cc": [],
                   "bcc": [], "subject": "Draft subject", "body": "Draft body", "in_reply_to": "", "references": ""},
            "d2": {"id": "d2", "message_id": "dm2", "thread_id": None, "to": [], "cc": [], "bcc": [],
                   "subject": "No recipient", "body": "x", "in_reply_to": "", "references": ""},
        }
        self.events = {
            "e1": {"id": "e1", "title": "Design review", "start": "2026-11-05T14:00:00+05:30",
                   "end": "2026-11-05T15:00:00+05:30", "all_day": False, "attendees": [{"email": "priya@example.com"}],
                   "status": "confirmed", "location": "", "description": ""},
            "e2": {"id": "e2", "title": "Lunch", "start": "2026-11-05T12:30:00+05:30",
                   "end": "2026-11-05T13:30:00+05:30", "all_day": False, "attendees": [], "status": "confirmed",
                   "location": "", "description": ""},
            "e3": {"id": "e3", "title": "Holiday", "start": "2026-11-05", "end": "2026-11-06", "all_day": True,
                   "attendees": [], "status": "confirmed", "location": "", "description": ""},
        }
        self.files = {
            "f1": {"id": "f1", "name": "notes.txt", "mime_type": "text/plain", "size": 120,
                   "url": "https://drive.google.com/file/d/f1", "created": "2026-10-01T00:00:00Z",
                   "updated": "2026-10-02T00:00:00Z", "trashed": False},
        }
        self.busy: list[dict[str, str]] = []
        self._ids = itertools.count(100)

    # ---- protocol ----
    async def call(self, operation: str, args: dict[str, Any] | None = None) -> Any:
        args = copy.deepcopy(args or {})
        self.calls.append((operation, args))
        if operation not in ALLOWED_OPERATIONS:
            raise BridgeError(f"Unknown operation '{operation}'.", code="unknown_operation")
        if operation in self.fail:
            raise self.fail[operation]
        return copy.deepcopy(getattr(self, f"op_{operation}")(args))

    def write_calls(self) -> list[tuple[str, dict[str, Any]]]:
        return [c for c in self.calls if c[0] in WRITE_OPS]

    def _get(self, store: dict, key: str, what: str) -> dict:
        if key not in store:
            raise BridgeError(f"{what} not found.", code="not_found")
        return store[key]

    # ---- operations ----
    def op_get_profile(self, a): return self.profile
    def op_get_latest_emails(self, a): return [e for e in self.emails.values() if "TRASH" not in e["labels"]][: a["max_results"]]
    def op_search_emails(self, a): return list(self.emails.values())[: a["max_results"]]
    def op_read_email(self, a): return self._get(self.emails, a["message_id"], "Email")
    def op_read_thread(self, a):
        msgs = [e for e in self.emails.values() if e["thread_id"] == a["thread_id"]]
        if not msgs:
            raise BridgeError("Thread not found.", code="not_found")
        return {"thread_id": a["thread_id"], "subject": msgs[0]["subject"], "messages": msgs}
    def op_list_drafts(self, a): return list(self.drafts.values())[: a["max_results"]]
    def op_get_draft(self, a): return self._get(self.drafts, a["draft_id"], "Draft")
    def op_list_gmail_labels(self, a): return self.labels
    def op_create_draft(self, a):
        did = f"d{next(self._ids)}"
        self.drafts[did] = {"id": did, "message_id": "x", **a}
        return {"draft_id": did}
    def op_update_draft(self, a):
        self._get(self.drafts, a["draft_id"], "Draft").update(a)
        return {"draft_id": a["draft_id"]}
    def op_delete_draft(self, a): self.drafts.pop(a["draft_id"]); return {"deleted": True}
    def op_send_draft(self, a): self.drafts.pop(a["draft_id"]); return {"message_id": "sent1"}
    def op_send_email(self, a): return {"message_id": "sent2", "thread_id": "ts2"}
    def op_update_email_labels(self, a):
        e = self._get(self.emails, a["message_id"], "Email")
        e["labels"] = [l for l in e["labels"] if l not in a["remove_labels"]] + a["add_labels"]
        return {"labels": e["labels"]}
    def op_trash_email(self, a): self._get(self.emails, a["message_id"], "Email")["labels"] = ["TRASH"]; return {"ok": True}
    def op_restore_email(self, a): self._get(self.emails, a["message_id"], "Email")["labels"] = ["INBOX"]; return {"ok": True}
    def op_list_calendar_events(self, a):
        from app.services.base import parse_aware_datetime
        s, e = parse_aware_datetime(a["start"]), parse_aware_datetime(a["end"])
        out = []
        for ev in self.events.values():
            if ev["all_day"]:
                out.append(ev)
                continue
            es, ee = parse_aware_datetime(ev["start"]), parse_aware_datetime(ev["end"])
            if es < e and s < ee:
                out.append(ev)
        return {"time_zone": "Asia/Kolkata", "events": out}
    def op_get_calendar_event(self, a): return self._get(self.events, a["event_id"], "Event")
    def op_check_availability(self, a): return {"time_zone": "Asia/Kolkata", "busy": self.busy}
    def op_create_calendar_event(self, a):
        eid = f"e{next(self._ids)}"
        self.events[eid] = {"id": eid, **a, "all_day": False, "attendees": [{"email": x} for x in a["attendees"]]}
        return {"id": eid, "html_link": f"https://calendar.google.com/event?eid={eid}"}
    def op_update_calendar_event(self, a):
        ev = self._get(self.events, a["event_id"], "Event")
        ev.update({k: v for k, v in a.items() if k not in ("event_id", "send_updates")})
        return {"id": a["event_id"]}
    def op_delete_calendar_event(self, a): self.events.pop(a["event_id"]); return {"deleted": True}
    def op_list_drive_files(self, a): return [f for f in self.files.values() if not f["trashed"]]
    def op_get_drive_file(self, a): return self._get(self.files, a["file_id"], "File")
    def op_save_attachments_to_drive(self, a):
        email = self._get(self.emails, a["message_id"], "Email")
        saved = []
        for att in email["attachments"]:
            if att["name"] in a["attachment_names"]:
                saved.append({"name": att["name"], "size": att["size"], "file_id": "nf", "url": "https://drive/nf"})
        return {"folder": {"name": a["folder_name"], "id": "fold1"}, "saved": saved, "skipped": []}
    def op_trash_drive_file(self, a): self._get(self.files, a["file_id"], "File")["trashed"] = True; return {"ok": True}


def tool_use(name: str, input: dict[str, Any], id: str | None = None) -> dict[str, Any]:
    return {"type": "tool_use", "id": id or f"tu_{name}_{abs(hash(str(input))) % 10000}", "name": name, "input": input}


def text(t: str) -> dict[str, Any]:
    return {"type": "text", "text": t}


class ScriptedLLM:
    """Returns queued responses. Each entry is a list of blocks or a callable(messages) -> blocks."""

    def __init__(self) -> None:
        self.script: list[list[dict[str, Any]] | Callable[[list], list[dict[str, Any]]]] = []
        self.requests: list[list[dict[str, Any]]] = []

    def then(self, *blocks: dict[str, Any]) -> "ScriptedLLM":
        self.script.append(list(blocks))
        return self

    async def create(self, system, messages, tools) -> LLMResponse:  # noqa: ANN001
        self.requests.append(copy.deepcopy(messages))
        if not self.script:
            return LLMResponse([text("(script exhausted)")], "end_turn")
        step = self.script.pop(0)
        blocks = step(messages) if callable(step) else step
        stop = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
        return LLMResponse(blocks, stop)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        groq_api_key="gsk_test-not-real-key-123",
        apps_script_url="https://script.google.com/macros/s/fake/exec",
        apps_script_secret="super-secret-value-1234567890",
    )


@pytest.fixture
def bridge() -> FakeBridge:
    return FakeBridge()


@pytest.fixture
def llm() -> ScriptedLLM:
    return ScriptedLLM()


@pytest.fixture
def client(settings, bridge, llm) -> TestClient:
    app = create_app(settings=settings, bridge=bridge, llm=llm)
    return TestClient(app)


@pytest.fixture
def ctx(settings, bridge):
    from app.services.base import ToolContext

    async def get_profile():
        return bridge.profile

    return ToolContext(bridge=bridge, settings=settings, get_profile=get_profile)
