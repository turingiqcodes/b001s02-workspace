"""Tool registry and argument validation.

The model only *picks* tools. Every call is validated here before anything
runs, and the validated (normalized) arguments are what gets executed or
stored for approval.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from ..config import Settings

EMAIL_RE = re.compile(r"^[^@\s<>(),;:\"\[\]]+@[^@\s<>(),;:\"\[\]]+\.[^@\s<>(),;:\"\[\]]{2,}$")
MIME_TYPES = ["any", "pdf", "document", "spreadsheet", "presentation", "image", "folder"]
MAX_TEXT = 100_000
MAX_ID = 256


class ToolValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ToolSpec:
    name: str
    kind: str  # "read" | "write"
    description: str
    properties: dict[str, dict[str, Any]]
    required: tuple[str, ...]
    label: str  # short past-tense label for read tools / title for write tools

    def schema(self, settings: Settings) -> dict[str, Any]:
        props: dict[str, Any] = {}
        for key, spec in self.properties.items():
            out = {k: v for k, v in spec.items() if not k.startswith("x-")}
            if key == "max_results":
                out["maximum"] = min(out.get("maximum", settings.max_search_results), settings.max_search_results)
            props[key] = out
        return {"type": "object", "properties": props, "required": list(self.required), "additionalProperties": False}


def _s(desc: str, **kw: Any) -> dict[str, Any]:
    return {"type": "string", "description": desc, **kw}


def _emails(desc: str, min_items: int = 0) -> dict[str, Any]:
    spec: dict[str, Any] = {"type": "array", "items": {"type": "string", "format": "email"}, "description": desc}
    if min_items:
        spec["minItems"] = min_items
    return spec


def _max_results(default: int = 10, maximum: int = 25) -> dict[str, Any]:
    return {"type": "integer", "minimum": 1, "maximum": maximum,
            "description": f"How many results to return (default {default})."}


_ID = {"minLength": 1, "maxLength": MAX_ID}
_DT = "ISO 8601 date-time, preferably with offset, e.g. 2026-11-05T15:00:00+05:30. Times without an offset use the user's timezone."

TOOL_SPECS: list[ToolSpec] = [
    # ---------------------------------------------------------------- read
    ToolSpec("search_emails", "read",
             "Search Gmail with Gmail search syntax (e.g. 'is:unread newer_than:1d', 'from:alice has:attachment'). "
             "Returns message ids, senders, subjects, dates and snippets.",
             {"query": _s("Gmail search query. Default: in:inbox", maxLength=500),
              "max_results": _max_results()}, (), "Searched email"),
    ToolSpec("read_email", "read", "Read one email: headers, plain-text body and attachment list.",
             {"message_id": _s("Message id from search_emails.", **_ID)}, ("message_id",), "Read an email"),
    ToolSpec("list_labels", "read", "List Gmail labels (system and user labels).", {}, (), "Listed labels"),
    ToolSpec("list_drafts", "read", "List Gmail drafts.", {"max_results": _max_results()}, (), "Listed drafts"),
    ToolSpec("list_events", "read", "List calendar events between two times (at most 62 days apart).",
             {"start": _s(_DT, format="date-time"), "end": _s(_DT, format="date-time"),
              "query": _s("Optional text to match in events.", maxLength=200),
              "max_results": _max_results(default=25)}, ("start", "end"), "Checked calendar"),
    ToolSpec("find_free_slots", "read",
             "Find free slots of a given length in the user's working hours between two dates (inclusive, at most 14 days).",
             {"duration_minutes": {"type": "integer", "minimum": 5, "maximum": 480, "description": "Meeting length in minutes."},
              "date_from": _s("First day, YYYY-MM-DD.", format="date"),
              "date_to": _s("Last day, YYYY-MM-DD.", format="date"),
              "max_results": {"type": "integer", "minimum": 1, "maximum": 20, "description": "How many slots (default 5)."}},
             ("duration_minutes", "date_from", "date_to"), "Found free slots"),
    ToolSpec("search_drive_files", "read",
             "Search Google Drive files by name, type, modified date or folder.",
             {"name_contains": _s("Text the file name contains.", maxLength=200),
              "mime_type": {"type": "string", "enum": MIME_TYPES, "description": "File type filter (default any)."},
              "modified_after": _s("Only files modified after this date, YYYY-MM-DD.", format="date"),
              "folder_id": _s("Only files directly in this folder.", **_ID),
              "max_results": _max_results()}, (), "Searched Drive"),
    # --------------------------------------------------------------- write
    ToolSpec("send_email", "write", "Send a new email. Needs the user's approval.",
             {"to": _emails("Recipients.", 1), "subject": _s("Subject.", maxLength=500),
              "body": _s("Plain-text body.", maxLength=MAX_TEXT), "cc": _emails("Cc."), "bcc": _emails("Bcc.")},
             ("to", "subject", "body"), "Send email"),
    ToolSpec("reply_to_email", "write", "Reply to an email (in the same thread). Needs approval.",
             {"message_id": _s("Message to reply to.", **_ID), "body": _s("Reply text.", minLength=1, maxLength=MAX_TEXT),
              "reply_all": {"type": "boolean", "description": "Reply to everyone (default false)."}},
             ("message_id", "body"), "Reply to email"),
    ToolSpec("forward_email", "write", "Forward an email (with its attachments). Needs approval.",
             {"message_id": _s("Message to forward.", **_ID), "to": _emails("Recipients.", 1),
              "note": _s("Optional note above the forwarded message.", maxLength=MAX_TEXT)},
             ("message_id", "to"), "Forward email"),
    ToolSpec("create_draft", "write", "Create a Gmail draft (optionally as a reply). Needs approval.",
             {"to": _emails("Recipients.", 1), "subject": _s("Subject.", maxLength=500),
              "body": _s("Plain-text body.", maxLength=MAX_TEXT), "cc": _emails("Cc."), "bcc": _emails("Bcc."),
              "reply_to_message_id": _s("Make the draft a reply to this message.", **_ID)},
             ("to", "subject", "body"), "Create draft"),
    ToolSpec("update_draft", "write", "Change an existing draft. Only the given fields change. Needs approval.",
             {"draft_id": _s("Draft id.", **_ID), "to": _emails("Recipients.", 1), "subject": _s("Subject.", maxLength=500),
              "body": _s("Body.", maxLength=MAX_TEXT), "cc": _emails("Cc."), "bcc": _emails("Bcc.")},
             ("draft_id",), "Update draft"),
    ToolSpec("send_draft", "write", "Send an existing draft. Needs approval.",
             {"draft_id": _s("Draft id.", **_ID)}, ("draft_id",), "Send draft"),
    ToolSpec("create_label", "write", "Create a Gmail label. Needs approval.",
             {"name": _s("Label name. Use / for nesting.", minLength=1, maxLength=225)}, ("name",), "Create label"),
    ToolSpec("modify_labels", "write",
             "Add or remove labels on emails (user labels, or INBOX, UNREAD, STARRED, IMPORTANT). "
             "Removing INBOX archives. Needs approval.",
             {"message_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 100,
                              "description": "Message ids."},
              "add_labels": {"type": "array", "items": {"type": "string"}, "description": "Label names to add."},
              "remove_labels": {"type": "array", "items": {"type": "string"}, "description": "Label names to remove."}},
             ("message_ids",), "Change labels"),
    ToolSpec("create_event", "write", "Create a calendar event and invite attendees. Needs approval.",
             {"title": _s("Event title.", minLength=1, maxLength=500), "start": _s(_DT, format="date-time"),
              "end": _s(_DT, format="date-time"), "attendees": _emails("Guest emails."),
              "description": _s("Description.", maxLength=8000), "location": _s("Location.", maxLength=500)},
             ("title", "start", "end"), "Create event"),
    ToolSpec("move_event", "write",
             "Move an event to a new time. If new_end is omitted the event keeps its duration. Needs approval.",
             {"event_id": _s("Event id from list_events.", **_ID), "new_start": _s(_DT, format="date-time"),
              "new_end": _s(_DT, format="date-time")}, ("event_id", "new_start"), "Move event"),
    ToolSpec("cancel_event", "write", "Cancel (delete) a calendar event. Needs approval and an extra acknowledgement.",
             {"event_id": _s("Event id.", **_ID),
              "notify_attendees": {"type": "boolean", "description": "Email guests about the cancellation (default true)."}},
             ("event_id",), "Cancel event"),
    ToolSpec("create_drive_folder", "write", "Create a Drive folder. Needs approval.",
             {"name": _s("Folder name.", minLength=1, maxLength=255), "parent_folder_id": _s("Parent folder id.", **_ID)},
             ("name",), "Create Drive folder"),
    ToolSpec("move_drive_file", "write", "Move a Drive file into a folder. Needs approval.",
             {"file_id": _s("File id.", **_ID), "folder_id": _s("Destination folder id.", **_ID)},
             ("file_id", "folder_id"), "Move Drive file"),
    ToolSpec("save_attachments_to_drive", "write",
             "Save an email's attachments to a Drive folder (all attachments unless attachment_ids is given). Needs approval.",
             {"message_id": _s("Email id.", **_ID),
              "attachment_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1,
                                 "description": "Only these attachments (ids from read_email)."},
              "folder_id": _s("Destination folder id.", **_ID),
              "folder_name": _s("Folder name to use or create (default 'Email Attachments').", minLength=1, maxLength=255)},
             ("message_id",), "Save attachments to Drive"),
]

TOOLS: dict[str, ToolSpec] = {t.name: t for t in TOOL_SPECS}
READ_TOOLS = {t.name for t in TOOL_SPECS if t.kind == "read"}
WRITE_TOOLS = {t.name for t in TOOL_SPECS if t.kind == "write"}
ALLOWED_SYSTEM_LABELS = {"INBOX", "UNREAD", "STARRED", "IMPORTANT"}


def tool_definitions(settings: Settings) -> list[dict[str, Any]]:
    """Provider-neutral list: [{name, description, parameters}]."""
    return [{"name": t.name, "description": t.description, "parameters": t.schema(settings)} for t in TOOL_SPECS]


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def parse_datetime(value: str, settings: Settings) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ToolValidationError(f"'{value}' is not an ISO 8601 date-time.") from exc
    if "T" not in text and " " not in text:
        raise ToolValidationError(f"'{value}' needs a time, e.g. {value}T09:00:00.")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=settings.tz)
    return dt


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ToolValidationError(f"'{value}' is not a date in YYYY-MM-DD format.") from exc


def _check_value(key: str, spec: dict[str, Any], value: Any, settings: Settings) -> Any:
    kind = spec["type"]
    if kind == "string":
        if not isinstance(value, str):
            raise ToolValidationError(f"Field '{key}' must be a string.")
        if len(value) < spec.get("minLength", 0) or (spec.get("minLength") and not value.strip()):
            raise ToolValidationError(f"Field '{key}' must not be empty.")
        if len(value) > spec.get("maxLength", MAX_TEXT):
            raise ToolValidationError(f"Field '{key}' is too long (max {spec.get('maxLength', MAX_TEXT)} characters).")
        if "enum" in spec and value not in spec["enum"]:
            raise ToolValidationError(f"Field '{key}' must be one of: {', '.join(spec['enum'])}.")
        fmt = spec.get("format")
        if fmt == "date-time":
            return parse_datetime(value, settings).isoformat()
        if fmt == "date":
            return parse_date(value).isoformat()
        return value
    if kind == "integer":
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ToolValidationError(f"Field '{key}' must be a whole number.")
        lo, hi = spec.get("minimum"), spec.get("maximum")
        if key == "max_results":
            hi = min(hi or settings.max_search_results, settings.max_search_results)
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            raise ToolValidationError(f"Field '{key}' must be between {lo} and {hi}.")
        return value
    if kind == "boolean":
        if not isinstance(value, bool):
            raise ToolValidationError(f"Field '{key}' must be true or false.")
        return value
    if kind == "array":
        if isinstance(value, str):  # small models often send one string instead of a list
            value = [value]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ToolValidationError(f"Field '{key}' must be a list of strings.")
        value = [v.strip() for v in value if v.strip()]
        if len(value) < spec.get("minItems", 0):
            raise ToolValidationError(f"Field '{key}' needs at least {spec['minItems']} item(s).")
        if "maxItems" in spec and len(value) > spec["maxItems"]:
            raise ToolValidationError(f"Field '{key}' allows at most {spec['maxItems']} items.")
        if spec["items"].get("format") == "email":
            bad = [v for v in value if not EMAIL_RE.match(v)]
            if bad:
                raise ToolValidationError(f"Not a valid email address: {', '.join(bad)}.")
        if len(set(value)) != len(value):
            value = list(dict.fromkeys(value))
        return value
    raise ToolValidationError(f"Field '{key}' has an unsupported type.")  # pragma: no cover


def validate_args(name: str, args: Any, settings: Settings) -> dict[str, Any]:
    """Return normalized arguments, or raise :class:`ToolValidationError`."""
    spec = TOOLS.get(name)
    if spec is None:
        raise ToolValidationError(f"Unknown tool '{name}'.")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ToolValidationError("Tool arguments must be a JSON object.")
    # Drop explicit nulls: models often send optional fields as null.
    args = {k: v for k, v in args.items() if v is not None}
    unknown = sorted(k for k in args if k not in spec.properties)
    if unknown:
        raise ToolValidationError(f"Unknown field(s): {', '.join(unknown)}.")
    missing = [k for k in spec.required if k not in args]
    if missing:
        raise ToolValidationError(f"Missing required field(s): {', '.join(missing)}.")
    out = {k: _check_value(k, spec.properties[k], v, settings) for k, v in args.items()}
    _extra_rules(name, out, settings)
    return out


def _extra_rules(name: str, a: dict[str, Any], settings: Settings) -> None:
    if name in ("send_email", "create_draft", "forward_email", "update_draft"):
        total = sum(len(a.get(k, [])) for k in ("to", "cc", "bcc"))
        if total > settings.max_recipients:
            raise ToolValidationError(f"Too many recipients ({total}); the limit is {settings.max_recipients}.")
    if name == "update_draft" and len(a) == 1:
        raise ToolValidationError("Give at least one field to change.")
    if name == "modify_labels":
        if not a.get("add_labels") and not a.get("remove_labels"):
            raise ToolValidationError("Give add_labels or remove_labels.")
        overlap = set(a.get("add_labels", [])) & set(a.get("remove_labels", []))
        if overlap:
            raise ToolValidationError(f"A label can't be both added and removed: {', '.join(sorted(overlap))}.")
    if name == "list_events":
        start, end = datetime.fromisoformat(a["start"]), datetime.fromisoformat(a["end"])
        if end <= start:
            raise ToolValidationError("end must be after start.")
        if end - start > timedelta(days=62):
            raise ToolValidationError("The time range can be at most 62 days.")
    if name == "find_free_slots":
        d1, d2 = date.fromisoformat(a["date_from"]), date.fromisoformat(a["date_to"])
        if d2 < d1:
            raise ToolValidationError("date_to must be on or after date_from.")
        if (d2 - d1).days > 13:
            raise ToolValidationError("Search at most 14 days at a time.")
    if name == "create_event":
        start, end = datetime.fromisoformat(a["start"]), datetime.fromisoformat(a["end"])
        if end <= start:
            raise ToolValidationError("The event must end after it starts.")
        if end - start > timedelta(days=14):
            raise ToolValidationError("An event can be at most 14 days long.")
    if name == "move_event" and "new_end" in a:
        if datetime.fromisoformat(a["new_end"]) <= datetime.fromisoformat(a["new_start"]):
            raise ToolValidationError("new_end must be after new_start.")
    if name == "save_attachments_to_drive" and "folder_id" in a and "folder_name" in a:
        raise ToolValidationError("Give folder_id or folder_name, not both.")
