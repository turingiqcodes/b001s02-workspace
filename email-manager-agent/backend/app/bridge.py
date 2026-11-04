"""Clients for the Google Apps Script bridge.

Every Google call (Gmail, Calendar, Drive) goes through one Apps Script web app.
The backend POSTs ``{"token", "action", "params"}`` and receives
``{"ok": true, "data": ...}`` or ``{"ok": false, "error": {"code", "message"}}``.

``MockBridge`` implements the same actions against in-memory data so the app
and the test suite run without a Google account.
"""

from __future__ import annotations

import copy
import itertools
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

import httpx


class BridgeError(Exception):
    """An error reported by (or while reaching) the Apps Script bridge."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class Bridge(Protocol):
    def call(self, action: str, params: dict[str, Any] | None = None) -> Any: ...


class AppsScriptBridge:
    def __init__(self, url: str, token: str, timeout: float = 60.0, transport: httpx.BaseTransport | None = None):
        self._url = url
        self._token = token
        # Apps Script answers POSTs with a 302 to script.googleusercontent.com; follow it.
        self._client = httpx.Client(timeout=timeout, follow_redirects=True, transport=transport)

    def call(self, action: str, params: dict[str, Any] | None = None) -> Any:
        payload = {"token": self._token, "action": action, "params": params or {}}
        try:
            resp = self._client.post(self._url, json=payload)
        except httpx.TimeoutException as exc:
            raise BridgeError("TIMEOUT", "Google took too long to respond.") from exc
        except httpx.HTTPError as exc:
            raise BridgeError("UNREACHABLE", f"Could not reach the Google Apps Script bridge ({exc.__class__.__name__}).") from exc

        if resp.status_code >= 400:
            raise BridgeError("HTTP_ERROR", f"The Apps Script bridge returned HTTP {resp.status_code}.")
        try:
            body = resp.json()
        except ValueError as exc:
            # A login/HTML page usually means the web app isn't deployed with "Anyone" access.
            raise BridgeError(
                "BAD_RESPONSE",
                "The Apps Script bridge did not return JSON. Check that the web app is deployed "
                "with access set to 'Anyone' and that APPS_SCRIPT_URL ends in /exec.",
            ) from exc

        if not isinstance(body, dict) or "ok" not in body:
            raise BridgeError("BAD_RESPONSE", "The Apps Script bridge returned an unexpected response.")
        if not body["ok"]:
            err = body.get("error") or {}
            raise BridgeError(str(err.get("code", "UNKNOWN")), str(err.get("message", "Unknown bridge error.")))
        return body.get("data")


# ---------------------------------------------------------------------------
# Mock bridge
# ---------------------------------------------------------------------------

USER_LABEL_TYPE = "user"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


class MockBridge:
    """In-memory stand-in for the Apps Script bridge with realistic sample data."""

    def __init__(self, now: datetime | None = None):
        self.now = now or datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        self._ids = itertools.count(100)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.labels: dict[str, dict[str, str]] = {
            "Label_1": {"id": "Label_1", "name": "Receipts", "type": USER_LABEL_TYPE},
            "Label_2": {"id": "Label_2", "name": "Projects/Apollo", "type": USER_LABEL_TYPE},
            "INBOX": {"id": "INBOX", "name": "INBOX", "type": "system"},
            "UNREAD": {"id": "UNREAD", "name": "UNREAD", "type": "system"},
        }
        d = self.now
        self.messages: dict[str, dict[str, Any]] = {
            "m1": {
                "id": "m1", "threadId": "t1", "from": "Priya Shah <priya@acme.example>", "to": "me@example.com",
                "subject": "Apollo launch timeline", "date": _iso(d - timedelta(hours=3)),
                "labelIds": ["INBOX", "UNREAD", "Label_2"],
                "body": "Hi,\n\nCan we meet this week to finalise the Apollo launch timeline? "
                        "I'm free Thursday afternoon.\n\nThanks,\nPriya",
                "attachments": [{"attachmentId": "a1", "filename": "apollo-plan.pdf", "mimeType": "application/pdf", "size": 48213}],
            },
            "m2": {
                "id": "m2", "threadId": "t2", "from": "Billing <billing@cloudhost.example>", "to": "me@example.com",
                "subject": "Your invoice for October", "date": _iso(d - timedelta(days=1)),
                "labelIds": ["INBOX"],
                "body": "Your invoice INV-2291 for $42.00 is attached.",
                "attachments": [{"attachmentId": "a2", "filename": "INV-2291.pdf", "mimeType": "application/pdf", "size": 20111}],
            },
            "m3": {
                "id": "m3", "threadId": "t3", "from": "Sam Lee <sam@partner.example>", "to": "me@example.com",
                "subject": "Quick question about the API", "date": _iso(d - timedelta(days=2)),
                "labelIds": ["INBOX"],
                "body": "Does the v2 API support pagination on the /orders endpoint?",
                "attachments": [],
            },
        }
        self.drafts: dict[str, dict[str, Any]] = {}
        self.sent: list[dict[str, Any]] = []
        self.events: dict[str, dict[str, Any]] = {
            "e1": {
                "id": "e1", "summary": "Team standup", "start": _iso(d + timedelta(days=1)),
                "end": _iso(d + timedelta(days=1, minutes=30)), "attendees": ["team@acme.example"],
                "description": "", "location": "", "htmlLink": "https://calendar.google.com/mock/e1",
            },
        }
        self.drive_files: list[dict[str, Any]] = []

    # -- dispatch ----------------------------------------------------------
    def call(self, action: str, params: dict[str, Any] | None = None) -> Any:
        params = params or {}
        self.calls.append((action, copy.deepcopy(params)))
        handler = getattr(self, f"_a_{action}", None)
        if handler is None:
            raise BridgeError("UNKNOWN_ACTION", f"Unknown action '{action}'.")
        return handler(**params)

    def _new_id(self, prefix: str) -> str:
        return f"{prefix}{next(self._ids)}"

    def _msg(self, message_id: str) -> dict[str, Any]:
        if message_id not in self.messages:
            raise BridgeError("NOT_FOUND", f"Email {message_id} was not found.")
        return self.messages[message_id]

    def _label_names(self, ids: list[str]) -> list[str]:
        return [self.labels[i]["name"] for i in ids if i in self.labels]

    def _summary(self, m: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": m["id"], "threadId": m["threadId"], "from": m["from"], "to": m["to"],
            "subject": m["subject"], "date": m["date"], "snippet": m["body"][:120],
            "labels": self._label_names(m["labelIds"]), "unread": "UNREAD" in m["labelIds"],
            "hasAttachments": bool(m["attachments"]),
        }

    # -- context -----------------------------------------------------------
    def _a_get_context(self) -> dict[str, Any]:
        return {"email": "me@example.com", "timezone": "UTC"}

    # -- gmail read --------------------------------------------------------
    def _a_search_emails(self, query: str = "", max_results: int = 10) -> dict[str, Any]:
        q = query.lower()
        terms = [t for t in q.split() if ":" not in t]
        hits = []
        for m in sorted(self.messages.values(), key=lambda x: x["date"], reverse=True):
            hay = f"{m['from']} {m['subject']} {m['body']}".lower()
            if "is:unread" in q and "UNREAD" not in m["labelIds"]:
                continue
            if "has:attachment" in q and not m["attachments"]:
                continue
            if all(t in hay for t in terms):
                hits.append(self._summary(m))
        return {"messages": hits[:max_results], "total": len(hits)}

    def _a_read_email(self, message_id: str) -> dict[str, Any]:
        m = self._msg(message_id)
        out = self._summary(m)
        out.update({"body": m["body"], "bodyTruncated": False, "attachments": copy.deepcopy(m["attachments"])})
        return out

    def _a_list_labels(self) -> dict[str, Any]:
        return {"labels": [dict(lbl) for lbl in self.labels.values()]}

    def _a_list_drafts(self, max_results: int = 10) -> dict[str, Any]:
        return {"drafts": [copy.deepcopy(d) for d in list(self.drafts.values())[:max_results]]}

    def _a_get_draft(self, draft_id: str) -> dict[str, Any]:
        if draft_id not in self.drafts:
            raise BridgeError("NOT_FOUND", f"Draft {draft_id} was not found.")
        return copy.deepcopy(self.drafts[draft_id])

    # -- gmail write -------------------------------------------------------
    def _a_create_draft(self, to: list[str], subject: str, body: str, cc: list[str] | None = None,
                        reply_to_message_id: str | None = None) -> dict[str, Any]:
        thread_id = self._msg(reply_to_message_id)["threadId"] if reply_to_message_id else None
        draft_id = self._new_id("d")
        self.drafts[draft_id] = {"id": draft_id, "to": to, "cc": cc or [], "subject": subject, "body": body, "threadId": thread_id}
        return copy.deepcopy(self.drafts[draft_id])

    def _a_update_draft(self, draft_id: str, **changes: Any) -> dict[str, Any]:
        draft = self.drafts.get(draft_id)
        if draft is None:
            raise BridgeError("NOT_FOUND", f"Draft {draft_id} was not found.")
        draft.update({k: v for k, v in changes.items() if v is not None})
        return copy.deepcopy(draft)

    def _a_send_draft(self, draft_id: str) -> dict[str, Any]:
        draft = self.drafts.pop(draft_id, None)
        if draft is None:
            raise BridgeError("NOT_FOUND", f"Draft {draft_id} was not found.")
        self.sent.append(draft)
        return {"messageId": self._new_id("s"), "threadId": draft.get("threadId")}

    def _a_send_email(self, to: list[str], subject: str, body: str, cc: list[str] | None = None,
                      reply_to_message_id: str | None = None) -> dict[str, Any]:
        thread_id = self._msg(reply_to_message_id)["threadId"] if reply_to_message_id else None
        self.sent.append({"to": to, "cc": cc or [], "subject": subject, "body": body, "threadId": thread_id})
        return {"messageId": self._new_id("s"), "threadId": thread_id}

    def _a_create_label(self, name: str) -> dict[str, Any]:
        if any(lbl["name"].lower() == name.lower() for lbl in self.labels.values()):
            raise BridgeError("ALREADY_EXISTS", f"A label named '{name}' already exists.")
        label_id = self._new_id("Label_")
        self.labels[label_id] = {"id": label_id, "name": name, "type": USER_LABEL_TYPE}
        return dict(self.labels[label_id])

    def _resolve_user_label(self, name: str) -> str:
        for lbl in self.labels.values():
            if lbl["name"].lower() == name.lower():
                if lbl["type"] != USER_LABEL_TYPE:
                    raise BridgeError("FORBIDDEN_LABEL", f"'{name}' is a system label and can't be changed here.")
                return lbl["id"]
        raise BridgeError("NOT_FOUND", f"Label '{name}' does not exist.")

    def _a_modify_labels(self, message_ids: list[str], add_labels: list[str] | None = None,
                         remove_labels: list[str] | None = None) -> dict[str, Any]:
        add_ids = [self._resolve_user_label(n) for n in add_labels or []]
        remove_ids = [self._resolve_user_label(n) for n in remove_labels or []]
        for mid in message_ids:
            m = self._msg(mid)
            m["labelIds"] = [i for i in m["labelIds"] if i not in remove_ids] + [i for i in add_ids if i not in m["labelIds"]]
        return {"modified": len(message_ids)}

    # -- calendar ----------------------------------------------------------
    def _events_between(self, time_min: str, time_max: str) -> list[dict[str, Any]]:
        lo, hi = _parse(time_min), _parse(time_max)
        return sorted(
            (copy.deepcopy(e) for e in self.events.values() if _parse(e["end"]) > lo and _parse(e["start"]) < hi),
            key=lambda e: e["start"],
        )

    def _a_list_events(self, time_min: str, time_max: str, max_results: int = 25) -> dict[str, Any]:
        return {"events": self._events_between(time_min, time_max)[:max_results]}

    def _a_check_availability(self, time_min: str, time_max: str) -> dict[str, Any]:
        busy = [{"start": e["start"], "end": e["end"], "summary": e["summary"]} for e in self._events_between(time_min, time_max)]
        return {"timeMin": time_min, "timeMax": time_max, "busy": busy, "isFree": not busy}

    def _a_get_event(self, event_id: str) -> dict[str, Any]:
        if event_id not in self.events:
            raise BridgeError("NOT_FOUND", f"Event {event_id} was not found.")
        return copy.deepcopy(self.events[event_id])

    def _a_create_event(self, summary: str, start: str, end: str, attendees: list[str] | None = None,
                        description: str = "", location: str = "", send_updates: bool = True) -> dict[str, Any]:
        if _parse(end) <= _parse(start):
            raise BridgeError("INVALID_ARGUMENT", "The meeting must end after it starts.")
        event_id = self._new_id("e")
        self.events[event_id] = {"id": event_id, "summary": summary, "start": start, "end": end,
                                 "attendees": attendees or [], "description": description, "location": location,
                                 "htmlLink": f"https://calendar.google.com/mock/{event_id}"}
        return copy.deepcopy(self.events[event_id])

    def _a_update_event(self, event_id: str, send_updates: bool = True, **changes: Any) -> dict[str, Any]:
        event = self.events.get(event_id)
        if event is None:
            raise BridgeError("NOT_FOUND", f"Event {event_id} was not found.")
        event.update({k: v for k, v in changes.items() if v is not None})
        return copy.deepcopy(event)

    def _a_cancel_event(self, event_id: str, send_updates: bool = True) -> dict[str, Any]:
        if self.events.pop(event_id, None) is None:
            raise BridgeError("NOT_FOUND", f"Event {event_id} was not found.")
        return {"cancelled": event_id}

    # -- drive -------------------------------------------------------------
    def _a_save_attachment_to_drive(self, message_id: str, attachment_id: str,
                                    folder_name: str = "Email Attachments") -> dict[str, Any]:
        m = self._msg(message_id)
        att = next((a for a in m["attachments"] if a["attachmentId"] == attachment_id), None)
        if att is None:
            raise BridgeError("NOT_FOUND", "That attachment was not found on the email.")
        file_id = self._new_id("f")
        record = {"id": file_id, "name": att["filename"], "folder": folder_name,
                  "webViewLink": f"https://drive.google.com/mock/{file_id}"}
        self.drive_files.append(record)
        return dict(record)


def build_bridge(settings) -> Bridge:
    if settings.bridge_mode == "mock":
        return MockBridge()
    if settings.bridge_mode == "apps_script":
        if not settings.apps_script_url or not settings.bridge_token:
            raise RuntimeError("BRIDGE_MODE=apps_script needs APPS_SCRIPT_URL and BRIDGE_TOKEN in backend/.env")
        return AppsScriptBridge(settings.apps_script_url, settings.bridge_token)
    raise RuntimeError(f"Unknown BRIDGE_MODE '{settings.bridge_mode}' (use 'mock' or 'apps_script')")
