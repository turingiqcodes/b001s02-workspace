"""Tool registry: what the model can call, how arguments are validated, and whether
a tool reads (runs immediately) or writes (becomes a pending action)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel

from app.services import calendar, drive, gmail
from app.services.base import PreparedWrite, ToolContext

Kind = Literal["read", "write"]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_model: type[BaseModel]
    kind: Kind
    handler: Callable[[ToolContext, Any], Awaitable[Any]]  # read -> result; write -> PreparedWrite

    def schema(self) -> dict[str, Any]:
        schema = self.args_model.model_json_schema()
        schema.pop("title", None)
        for prop in schema.get("properties", {}).values():
            prop.pop("title", None)
        return {"name": self.name, "description": self.description, "input_schema": schema}


_WRITE_NOTE = " Requires user approval: calling this only creates a preview card."

TOOLS: list[ToolSpec] = [
    # ----- Gmail: read -----
    ToolSpec("get_latest_emails", "Get the most recent emails in the inbox.", gmail.GetLatestEmailsArgs, "read", gmail.get_latest_emails),
    ToolSpec("search_emails", "Search Gmail by keywords, sender, recipient, subject, dates, label, unread or attachment filters.", gmail.SearchEmailsArgs, "read", gmail.search_emails),
    ToolSpec("read_email", "Read one email in full, including its attachment list.", gmail.MessageIdArgs, "read", gmail.read_email),
    ToolSpec("read_thread", "Read every message in an email conversation (use for summaries).", gmail.ReadThreadArgs, "read", gmail.read_thread),
    ToolSpec("list_drafts", "List existing Gmail drafts.", gmail.ListDraftsArgs, "read", gmail.list_drafts),
    ToolSpec("list_gmail_labels", "List the Gmail labels that exist.", gmail.NoArgs, "read", gmail.list_gmail_labels),
    # ----- Gmail: write -----
    ToolSpec("draft_reply", "Save a reply draft in the original thread (Re: subject, In-Reply-To and References headers set automatically)." + _WRITE_NOTE, gmail.DraftReplyArgs, "write", gmail.prepare_draft_reply),
    ToolSpec("create_draft", "Save a new email draft." + _WRITE_NOTE, gmail.CreateDraftArgs, "write", gmail.prepare_create_draft),
    ToolSpec("update_draft", "Change fields of an existing draft; omitted fields stay the same." + _WRITE_NOTE, gmail.UpdateDraftArgs, "write", gmail.prepare_update_draft),
    ToolSpec("delete_draft", "Delete a draft (cannot be undone)." + _WRITE_NOTE, gmail.DraftIdArgs, "write", gmail.prepare_delete_draft),
    ToolSpec("send_draft", "Send an existing draft." + _WRITE_NOTE, gmail.DraftIdArgs, "write", gmail.prepare_send_draft),
    ToolSpec("send_email", "Send a new email (no attachments)." + _WRITE_NOTE, gmail.SendEmailArgs, "write", gmail.prepare_send_email),
    ToolSpec("update_email_labels", "Add or remove Gmail labels on an email's conversation (INBOX, UNREAD, STARRED, IMPORTANT or user labels; case-insensitive)." + _WRITE_NOTE, gmail.UpdateEmailLabelsArgs, "write", gmail.prepare_update_email_labels),
    ToolSpec("trash_email", "Move an email to Trash (restorable)." + _WRITE_NOTE, gmail.MessageIdArgs, "write", gmail.prepare_trash_email),
    ToolSpec("restore_email", "Restore an email's whole conversation from Trash to the inbox. Find it first with search_emails include_trash=true." + _WRITE_NOTE, gmail.MessageIdArgs, "write", gmail.prepare_restore_email),
    # ----- Calendar: read -----
    ToolSpec("list_calendar_events", "List calendar events between two date-times.", calendar.ListCalendarEventsArgs, "read", calendar.list_calendar_events),
    ToolSpec("check_availability", "Get busy periods between two date-times.", calendar.CheckAvailabilityArgs, "read", calendar.check_availability),
    ToolSpec("find_free_slots", "Suggest up to 5 free meeting slots (weekdays, 09:00-18:00, 30-minute steps).", calendar.FindFreeSlotsArgs, "read", calendar.find_free_slots_tool),
    # ----- Calendar: write -----
    ToolSpec("create_calendar_event", "Create a calendar event. Conflicts are checked automatically." + _WRITE_NOTE, calendar.CreateCalendarEventArgs, "write", calendar.prepare_create_event),
    ToolSpec("update_calendar_event", "Change an event. Moving it with only a new start keeps its duration. Conflicts are checked automatically." + _WRITE_NOTE, calendar.UpdateCalendarEventArgs, "write", calendar.prepare_update_event),
    ToolSpec("delete_calendar_event", "Cancel a calendar event (cannot be undone)." + _WRITE_NOTE, calendar.DeleteCalendarEventArgs, "write", calendar.prepare_delete_event),
    # ----- Drive -----
    ToolSpec("list_drive_files", "List Google Drive files, newest first.", drive.ListDriveFilesArgs, "read", drive.list_drive_files),
    ToolSpec("save_attachments_to_drive", "Save an email's attachments to the Drive folder 'Email Attachments'. Files over 25 MB are skipped." + _WRITE_NOTE, drive.SaveAttachmentsArgs, "write", drive.prepare_save_attachments),
    ToolSpec("trash_drive_file", "Move a Drive file to Drive Trash (restorable)." + _WRITE_NOTE, drive.FileIdArgs, "write", drive.prepare_trash_drive_file),
]

TOOLS_BY_NAME: dict[str, ToolSpec] = {t.name: t for t in TOOLS}
TOOL_SCHEMAS: list[dict[str, Any]] = [t.schema() for t in TOOLS]


def describe_result(operation: str, result: Any) -> str:
    """Plain-language outcome of an executed write (shown to the user)."""
    r = result if isinstance(result, dict) else {}
    match operation:
        case "create_draft":
            return "Draft saved in Gmail."
        case "update_draft":
            return "Draft updated."
        case "delete_draft":
            return "Draft deleted."
        case "send_draft" | "send_email":
            return "Email sent successfully."
        case "update_email_labels":
            return "Labels updated." + (f" Current labels: {', '.join(r['labels'])}." if r.get("labels") else "")
        case "trash_email":
            return "Email moved to Trash."
        case "restore_email":
            return "Conversation restored from Trash to the inbox."
        case "create_calendar_event":
            return "Event created." + (f" Link: {r['html_link']}" if r.get("html_link") else "")
        case "update_calendar_event":
            return "Event updated." + (f" Emailed {r['notified']} attendee(s)." if r.get("notified") else "")
        case "delete_calendar_event":
            return "Event cancelled." + (f" Emailed {r['notified']} attendee(s)." if r.get("notified") else "")
        case "save_attachments_to_drive":
            saved = r.get("saved") or []
            skipped = r.get("skipped") or []
            parts = [f"Saved {len(saved)} file(s) to '{(r.get('folder') or {}).get('name', 'Email Attachments')}'."]
            parts += [f"- {f.get('name')}: {f.get('url')}" for f in saved]
            if skipped:
                parts.append("Skipped: " + ", ".join(f"{f.get('name')} ({f.get('reason')})" for f in skipped))
            return "\n".join(parts)
        case "trash_drive_file":
            return "File moved to Drive Trash."
    return "Done."
