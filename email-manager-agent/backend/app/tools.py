"""Tool definitions for Claude, split into read tools (run immediately) and
write tools (need a preview and user approval before they run)."""

from __future__ import annotations

from typing import Any

from .bridge import Bridge, BridgeError

_STR = {"type": "string"}
_STR_LIST = {"type": "array", "items": {"type": "string"}}
_ISO = {"type": "string", "description": "ISO 8601 date-time with UTC offset, e.g. 2026-11-05T14:00:00+05:30"}


def _tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "input_schema": {"type": "object", "properties": properties, "required": required, "additionalProperties": False},
    }


READ_TOOLS: list[dict[str, Any]] = [
    _tool("search_emails",
          "Search Gmail. Accepts Gmail search syntax (from:, to:, subject:, is:unread, has:attachment, "
          "newer_than:7d, label:...). Returns message summaries (id, from, subject, date, snippet, labels).",
          {"query": _STR, "max_results": {"type": "integer", "minimum": 1, "maximum": 50}}, ["query"]),
    _tool("read_email",
          "Read the full body of one email plus its attachment list (attachmentId, filename, size). "
          "Use this before summarizing or replying.",
          {"message_id": _STR}, ["message_id"]),
    _tool("list_labels", "List the user's Gmail labels. Only labels with type 'user' can be added or removed.", {}, []),
    _tool("list_drafts", "List existing Gmail drafts (id, to, subject, body).",
          {"max_results": {"type": "integer", "minimum": 1, "maximum": 50}}, []),
    _tool("list_events", "List events on the user's primary Google Calendar between two times.",
          {"time_min": _ISO, "time_max": _ISO, "max_results": {"type": "integer", "minimum": 1, "maximum": 100}},
          ["time_min", "time_max"]),
    _tool("check_availability",
          "Check whether the user is free between two times. Returns busy blocks in that window.",
          {"time_min": _ISO, "time_max": _ISO}, ["time_min", "time_max"]),
]

WRITE_TOOLS: list[dict[str, Any]] = [
    _tool("create_draft",
          "Create a Gmail draft. Set reply_to_message_id to make it a reply in that email's thread. "
          "Requires user approval.",
          {"to": _STR_LIST, "cc": _STR_LIST, "subject": _STR, "body": {"type": "string", "description": "Plain-text body"},
           "reply_to_message_id": _STR}, ["to", "subject", "body"]),
    _tool("update_draft", "Change fields of an existing draft. Omitted fields stay as they are. Requires user approval.",
          {"draft_id": _STR, "to": _STR_LIST, "cc": _STR_LIST, "subject": _STR, "body": _STR}, ["draft_id"]),
    _tool("send_draft", "Send an existing draft. Requires user approval.", {"draft_id": _STR}, ["draft_id"]),
    _tool("send_email",
          "Compose and send an email immediately (no draft step). Set reply_to_message_id to reply in-thread. "
          "Requires user approval.",
          {"to": _STR_LIST, "cc": _STR_LIST, "subject": _STR, "body": _STR, "reply_to_message_id": _STR},
          ["to", "subject", "body"]),
    _tool("create_label", "Create a new Gmail label. Requires user approval.", {"name": _STR}, ["name"]),
    _tool("modify_labels",
          "Organize or restore emails by adding/removing user labels (by label name). To restore, reverse an "
          "earlier change. System labels (INBOX, UNREAD, etc.) are not allowed. Requires user approval.",
          {"message_ids": _STR_LIST, "add_labels": _STR_LIST, "remove_labels": _STR_LIST}, ["message_ids"]),
    _tool("create_event",
          "Create a meeting on the primary calendar and (by default) email invites to attendees. "
          "Check availability first. Requires user approval.",
          {"summary": _STR, "start": _ISO, "end": _ISO, "attendees": _STR_LIST, "description": _STR,
           "location": _STR, "send_updates": {"type": "boolean"}}, ["summary", "start", "end"]),
    _tool("update_event", "Change fields of an existing meeting. Omitted fields stay as they are. Requires user approval.",
          {"event_id": _STR, "summary": _STR, "start": _ISO, "end": _ISO, "attendees": _STR_LIST,
           "description": _STR, "location": _STR, "send_updates": {"type": "boolean"}}, ["event_id"]),
    _tool("cancel_event", "Cancel (delete) a meeting and notify attendees by default. Requires user approval.",
          {"event_id": _STR, "send_updates": {"type": "boolean"}}, ["event_id"]),
    _tool("save_attachment_to_drive",
          "Save one email attachment to Google Drive (default folder 'Email Attachments'). Get attachment_id "
          "from read_email. Requires user approval.",
          {"message_id": _STR, "attachment_id": _STR, "folder_name": _STR}, ["message_id", "attachment_id"]),
]

ALL_TOOLS = READ_TOOLS + WRITE_TOOLS
READ_TOOL_NAMES = {t["name"] for t in READ_TOOLS}
WRITE_TOOL_NAMES = {t["name"] for t in WRITE_TOOLS}
_SCHEMAS = {t["name"]: t["input_schema"] for t in ALL_TOOLS}

_TYPES = {"string": str, "integer": int, "boolean": bool, "array": list, "object": dict}


def validate_input(name: str, data: Any) -> str | None:
    """Return an error message if ``data`` doesn't match the tool schema, else None."""
    schema = _SCHEMAS.get(name)
    if schema is None:
        return f"Unknown tool '{name}'."
    if not isinstance(data, dict):
        return "Tool input must be a JSON object."
    props = schema["properties"]
    missing = [k for k in schema["required"] if k not in data]
    if missing:
        return f"Missing required field(s): {', '.join(missing)}."
    extra = [k for k in data if k not in props]
    if extra:
        return f"Unknown field(s): {', '.join(extra)}."
    for key, value in data.items():
        spec = props[key]
        expected = _TYPES[spec["type"]]
        if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
            return f"Field '{key}' must be of type {spec['type']}."
        if spec["type"] == "array" and not all(isinstance(v, str) for v in value):
            return f"Field '{key}' must be a list of strings."
    return None


# ---------------------------------------------------------------------------
# Previews (FR-08)
# ---------------------------------------------------------------------------

def _join(values: list[str] | None) -> str:
    return ", ".join(values) if values else "—"


def _try(bridge: Bridge, action: str, params: dict[str, Any]) -> dict[str, Any] | None:
    """Fetch context for a preview. Failures are non-fatal: the preview still shows the inputs."""
    try:
        return bridge.call(action, params)
    except BridgeError:
        return None


def build_preview(name: str, data: dict[str, Any], bridge: Bridge) -> dict[str, Any]:
    """Describe a pending write action for the approval card.

    Returns ``{"title", "fields": [{"label", "value"}], "body", "warning"}``.
    """
    fields: list[dict[str, str]] = []
    body: str | None = None
    warning: str | None = None

    def add(label: str, value: Any) -> None:
        fields.append({"label": label, "value": value if isinstance(value, str) else str(value)})

    if name in {"create_draft", "send_email"}:
        title = "Send email" if name == "send_email" else "Create draft"
        add("To", _join(data.get("to")))
        if data.get("cc"):
            add("Cc", _join(data["cc"]))
        add("Subject", data.get("subject", ""))
        if data.get("reply_to_message_id"):
            orig = _try(bridge, "read_email", {"message_id": data["reply_to_message_id"]})
            add("In reply to", f"{orig['subject']} — {orig['from']}" if orig else data["reply_to_message_id"])
        body = data.get("body", "")
        if name == "send_email":
            warning = "This email will be sent immediately and can't be unsent."

    elif name == "update_draft":
        title = "Update draft"
        current = _try(bridge, "get_draft", {"draft_id": data["draft_id"]}) or {}
        add("Draft", current.get("subject") or data["draft_id"])
        for key, label in (("to", "To"), ("cc", "Cc"), ("subject", "Subject")):
            if key in data:
                old = current.get(key)
                old_s = _join(old) if isinstance(old, list) else (old or "—")
                new_s = _join(data[key]) if isinstance(data[key], list) else data[key]
                add(label, f"{old_s} → {new_s}")
        if "body" in data:
            body = data["body"]

    elif name == "send_draft":
        title = "Send draft"
        draft = _try(bridge, "get_draft", {"draft_id": data["draft_id"]})
        if draft:
            add("To", _join(draft.get("to")))
            if draft.get("cc"):
                add("Cc", _join(draft["cc"]))
            add("Subject", draft.get("subject", ""))
            body = draft.get("body")
        else:
            add("Draft ID", data["draft_id"])
        warning = "This email will be sent immediately and can't be unsent."

    elif name == "create_label":
        title = "Create label"
        add("Label", data["name"])

    elif name == "modify_labels":
        title = "Change labels"
        subjects = []
        for mid in data["message_ids"][:10]:
            msg = _try(bridge, "read_email", {"message_id": mid})
            subjects.append(f"{msg['subject']} — {msg['from']}" if msg else mid)
        if len(data["message_ids"]) > 10:
            subjects.append(f"…and {len(data['message_ids']) - 10} more")
        add("Emails", "\n".join(subjects))
        if data.get("add_labels"):
            add("Add labels", _join(data["add_labels"]))
        if data.get("remove_labels"):
            add("Remove labels", _join(data["remove_labels"]))

    elif name == "create_event":
        title = "Schedule meeting"
        add("Title", data["summary"])
        add("Starts", data["start"])
        add("Ends", data["end"])
        add("Attendees", _join(data.get("attendees")))
        if data.get("location"):
            add("Location", data["location"])
        add("Send invites", "Yes" if data.get("send_updates", True) else "No")
        body = data.get("description") or None
        avail = _try(bridge, "check_availability", {"time_min": data["start"], "time_max": data["end"]})
        if avail and avail.get("busy"):
            clash = ", ".join(b.get("summary") or "busy" for b in avail["busy"])
            warning = f"This overlaps with: {clash}."

    elif name == "update_event":
        title = "Update meeting"
        current = _try(bridge, "get_event", {"event_id": data["event_id"]}) or {}
        add("Meeting", current.get("summary") or data["event_id"])
        for key, label in (("summary", "Title"), ("start", "Starts"), ("end", "Ends"),
                           ("attendees", "Attendees"), ("location", "Location")):
            if key in data:
                old = current.get(key)
                old_s = _join(old) if isinstance(old, list) else (old or "—")
                new_s = _join(data[key]) if isinstance(data[key], list) else data[key]
                add(label, f"{old_s} → {new_s}")
        add("Notify attendees", "Yes" if data.get("send_updates", True) else "No")
        if "description" in data:
            body = data["description"]

    elif name == "cancel_event":
        title = "Cancel meeting"
        event = _try(bridge, "get_event", {"event_id": data["event_id"]})
        if event:
            add("Meeting", event.get("summary", ""))
            add("When", f"{event.get('start')} → {event.get('end')}")
            add("Attendees", _join(event.get("attendees")))
        else:
            add("Event ID", data["event_id"])
        add("Notify attendees", "Yes" if data.get("send_updates", True) else "No")
        warning = "The meeting will be removed from your calendar."

    elif name == "save_attachment_to_drive":
        title = "Save attachment to Drive"
        msg = _try(bridge, "read_email", {"message_id": data["message_id"]})
        att = None
        if msg:
            att = next((a for a in msg.get("attachments", []) if a.get("attachmentId") == data["attachment_id"]), None)
            add("From email", f"{msg['subject']} — {msg['from']}")
        add("File", f"{att['filename']} ({att.get('size', '?')} bytes)" if att else data["attachment_id"])
        add("Drive folder", data.get("folder_name") or "Email Attachments")

    else:  # pragma: no cover - guarded by WRITE_TOOL_NAMES
        title = name
        for k, v in data.items():
            add(k, v)

    return {"title": title, "fields": fields, "body": body, "warning": warning}
