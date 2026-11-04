"""Gmail tools: argument models, result parsing, and write previews (FR-05..FR-19)."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.errors import PreviewRejected
from app.services.base import (
    coerce_date,
    Preview,
    PreparedWrite,
    ToolContext,
    truncate_text,
    validate_email,
    validate_header_text,
)


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _emails(values: list[str]) -> list[str]:
    return [validate_email(v) for v in values]


# ---------- argument models ----------

class GetLatestEmailsArgs(_Args):
    max_results: int = Field(5, ge=1, le=50, description="How many recent emails to return.")


class _SearchFilters(_Args):
    query: str | None = Field(None, max_length=500, description="Free text or Gmail search syntax, e.g. 'invoice is:unread'.")
    sender: str | None = Field(None, max_length=200, description="Sender name or address (from:).")
    recipient: str | None = Field(None, max_length=200, description="Recipient name or address (to:).")
    subject: str | None = Field(None, max_length=200, description="Words in the subject.")
    after: date | None = Field(None, description="Only emails on/after this date (YYYY-MM-DD).")
    before: date | None = Field(None, description="Only emails before this date (YYYY-MM-DD).")
    has_attachment: bool | None = None
    is_unread: bool | None = None
    label: str | None = Field(None, max_length=100, description="Gmail label name.")
    include_trash: bool = Field(False, description="Also search Trash (needed to find emails to restore).")

    @field_validator("after", "before", mode="before")
    @classmethod
    def _dates(cls, v: object) -> object:
        return coerce_date(v)

    @model_validator(mode="after")
    def _at_least_one(self) -> "_SearchFilters":
        if not any([self.query, self.sender, self.recipient, self.subject, self.after, self.before,
                    self.has_attachment, self.is_unread, self.label, self.include_trash]):
            raise ValueError("give at least one search filter")
        return self

    def gmail_query(self) -> str:
        def quote(v: str) -> str:
            v = v.replace('"', "")
            return f'"{v}"' if " " in v else v

        parts: list[str] = []
        if self.query:
            parts.append(self.query)
        if self.sender:
            parts.append(f"from:{quote(self.sender)}")
        if self.recipient:
            parts.append(f"to:{quote(self.recipient)}")
        if self.subject:
            parts.append(f"subject:({self.subject.replace('(', '').replace(')', '')})")
        if self.after:
            parts.append(f"after:{self.after:%Y/%m/%d}")
        if self.before:
            parts.append(f"before:{self.before:%Y/%m/%d}")
        if self.has_attachment:
            parts.append("has:attachment")
        if self.is_unread is True:
            parts.append("is:unread")
        elif self.is_unread is False:
            parts.append("is:read")
        if self.label:
            parts.append(f"label:{quote(self.label)}")
        if self.include_trash:
            parts.append("in:anywhere")
        return " ".join(parts)


class SearchEmailsArgs(_SearchFilters):
    max_results: int = Field(10, ge=1, le=50)


class CountEmailsArgs(_SearchFilters):
    """Same filters as search_emails; returns a count instead of messages."""


class MessageIdArgs(_Args):
    message_id: str = Field(..., min_length=1, max_length=200, description="Gmail message id.")


class ReadThreadArgs(_Args):
    thread_id: str = Field(..., min_length=1, max_length=200, description="Gmail thread id.")


class ListDraftsArgs(_Args):
    max_results: int = Field(10, ge=1, le=50)


class NoArgs(_Args):
    pass


class _ComposeFields(_Args):
    to: list[str] = Field(default_factory=list, max_length=50)
    cc: list[str] = Field(default_factory=list, max_length=50)
    bcc: list[str] = Field(default_factory=list, max_length=50)
    subject: str = Field("", max_length=500)
    body: str = Field("", max_length=50000, description="Plain-text body.")

    @field_validator("to", "cc", "bcc")
    @classmethod
    def _check_emails(cls, v: list[str]) -> list[str]:
        return _emails(v)

    @field_validator("subject")
    @classmethod
    def _check_subject(cls, v: str) -> str:
        return validate_header_text(v)


class CreateDraftArgs(_ComposeFields):
    pass


class SendEmailArgs(_ComposeFields):
    @model_validator(mode="after")
    def _needs_recipient(self) -> "SendEmailArgs":
        if not self.to:
            raise ValueError("'to' needs at least one recipient")
        if not self.subject.strip() and not self.body.strip():
            raise ValueError("an email needs a subject or a body")
        return self


class DraftReplyArgs(_Args):
    message_id: str = Field(..., min_length=1, max_length=200, description="Id of the email being replied to.")
    body: str = Field(..., min_length=1, max_length=50000, description="Plain-text reply body.")
    reply_all: bool = Field(False, description="Also reply to everyone in To/Cc of the original.")


class UpdateDraftArgs(_Args):
    draft_id: str = Field(..., min_length=1, max_length=200)
    to: list[str] | None = None
    cc: list[str] | None = None
    bcc: list[str] | None = None
    subject: str | None = Field(None, max_length=500)
    body: str | None = Field(None, max_length=50000)

    @field_validator("to", "cc", "bcc")
    @classmethod
    def _check_emails(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else _emails(v)

    @field_validator("subject")
    @classmethod
    def _check_subject(cls, v: str | None) -> str | None:
        return None if v is None else validate_header_text(v)

    @model_validator(mode="after")
    def _has_change(self) -> "UpdateDraftArgs":
        if all(getattr(self, f) is None for f in ("to", "cc", "bcc", "subject", "body")):
            raise ValueError("give at least one field to change")
        return self


class DraftIdArgs(_Args):
    draft_id: str = Field(..., min_length=1, max_length=200)


class UpdateEmailLabelsArgs(_Args):
    message_id: str = Field(..., min_length=1, max_length=200)
    add_labels: list[str] = Field(default_factory=list, max_length=20)
    remove_labels: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _has_change(self) -> "UpdateEmailLabelsArgs":
        if not self.add_labels and not self.remove_labels:
            raise ValueError("give labels to add or remove")
        return self


# ---------- parsing ----------

EMAIL_FIELDS = ("id", "thread_id", "subject", "from", "to", "cc", "date", "labels", "unread", "snippet")


def parse_email(raw: dict[str, Any], body_limit: int) -> dict[str, Any]:
    email = {k: raw.get(k) for k in EMAIL_FIELDS if k in raw}
    email["body"] = truncate_text(raw.get("body"), body_limit)
    email["attachments"] = [
        {"name": a.get("name"), "size": a.get("size"), "mime_type": a.get("mime_type")}
        for a in raw.get("attachments") or []
    ]
    return email


def parse_draft(raw: dict[str, Any], body_limit: int) -> dict[str, Any]:
    return {
        "draft_id": raw.get("id"),
        "message_id": raw.get("message_id"),
        "thread_id": raw.get("thread_id"),
        "to": raw.get("to") or [],
        "cc": raw.get("cc") or [],
        "bcc": raw.get("bcc") or [],
        "subject": raw.get("subject") or "",
        "body": truncate_text(raw.get("body"), body_limit),
    }


# ---------- read handlers ----------

async def get_latest_emails(ctx: ToolContext, args: GetLatestEmailsArgs) -> Any:
    raw = await ctx.bridge.call("get_latest_emails", {"max_results": args.max_results})
    return {"emails": [parse_email(e, ctx.settings.list_body_limit) for e in raw or []]}


async def search_emails(ctx: ToolContext, args: SearchEmailsArgs) -> Any:
    query = args.gmail_query()
    raw = await ctx.bridge.call("search_emails", {"query": query, "max_results": args.max_results})
    emails = [parse_email(e, ctx.settings.list_body_limit) for e in raw or []]
    result: dict[str, Any] = {"query": query, "emails": emails}
    if len(emails) >= args.max_results:
        result["note"] = "More matches may exist. Use count_emails to get the total."
    return result


async def count_emails(ctx: ToolContext, args: CountEmailsArgs) -> Any:
    query = args.gmail_query()
    raw = await ctx.bridge.call("count_emails", {"query": query}) or {}
    return {
        "query": query,
        "conversations": int(raw.get("conversations") or 0),
        "capped": bool(raw.get("capped")),
    }


async def read_email(ctx: ToolContext, args: MessageIdArgs) -> Any:
    raw = await ctx.bridge.call("read_email", {"message_id": args.message_id})
    return parse_email(raw, ctx.settings.email_body_limit)


async def read_thread(ctx: ToolContext, args: ReadThreadArgs) -> Any:
    raw = await ctx.bridge.call("read_thread", {"thread_id": args.thread_id})
    return {
        "thread_id": raw.get("thread_id"),
        "subject": raw.get("subject"),
        "messages": [parse_email(m, ctx.settings.email_body_limit) for m in raw.get("messages") or []],
    }


async def list_drafts(ctx: ToolContext, args: ListDraftsArgs) -> Any:
    raw = await ctx.bridge.call("list_drafts", {"max_results": args.max_results})
    return {"drafts": [parse_draft(d, ctx.settings.list_body_limit) for d in raw or []]}


async def list_gmail_labels(ctx: ToolContext, args: NoArgs) -> Any:
    raw = await ctx.bridge.call("list_gmail_labels", {})
    return {"labels": raw or []}


# ---------- write preparation (previews) ----------

def _email_target(email: dict[str, Any]) -> dict[str, Any]:
    return {
        "subject": email.get("subject") or "(no subject)",
        "from": email.get("from"),
        "date": email.get("date"),
        "message_id": email.get("id"),
    }


def _conversation_size(email: dict[str, Any]) -> int:
    return int(email.get("thread_message_count") or 1)


def _conversation_text(email: dict[str, Any]) -> str:
    n = _conversation_size(email)
    return "1 message" if n == 1 else f"{n} messages"


def _compose_view(f: dict[str, Any]) -> dict[str, Any]:
    view = {"to": ", ".join(f.get("to") or []) or "(none)", "subject": f.get("subject") or "(no subject)"}
    if f.get("cc"):
        view["cc"] = ", ".join(f["cc"])
    if f.get("bcc"):
        view["bcc"] = ", ".join(f["bcc"])
    view["body"] = f.get("body") or ""
    return view


_ADDR_RE = re.compile(r"<([^>]+)>")


def extract_address(value: str) -> str:
    m = _ADDR_RE.search(value or "")
    return (m.group(1) if m else (value or "")).strip()


def reply_subject(subject: str) -> str:
    subject = (subject or "").strip()
    return subject if re.match(r"^re:", subject, re.IGNORECASE) else f"Re: {subject}".strip()


async def prepare_create_draft(ctx: ToolContext, args: CreateDraftArgs) -> PreparedWrite:
    fields = args.model_dump()
    return PreparedWrite(
        preview=Preview(
            title="Create email draft",
            summary=f"Save a new draft '{args.subject or '(no subject)'}'. Nothing will be sent.",
            proposed=_compose_view(fields),
            recipients=args.to + args.cc + args.bcc,
        ),
        bridge_operation="create_draft",
        bridge_args=fields,
    )


async def prepare_draft_reply(ctx: ToolContext, args: DraftReplyArgs) -> PreparedWrite:
    original = await ctx.bridge.call("read_email", {"message_id": args.message_id})
    profile = await ctx.get_profile()
    me = (profile.get("email") or "").lower()

    reply_to = extract_address(original.get("reply_to") or original.get("from") or "")
    if not reply_to:
        raise PreviewRejected("The original email has no sender address to reply to.")
    to = [reply_to]
    cc: list[str] = []
    if args.reply_all:
        seen = {reply_to.lower(), me}
        for addr in (original.get("to") or []) + (original.get("cc") or []):
            a = extract_address(addr)
            if a and a.lower() not in seen:
                seen.add(a.lower())
                cc.append(a)

    msg_id_header = original.get("message_id_header") or ""
    references = " ".join(x for x in [original.get("references") or "", msg_id_header] if x).strip()
    fields = {
        "to": to,
        "cc": cc,
        "bcc": [],
        "subject": reply_subject(original.get("subject") or ""),
        "body": args.body,
        "thread_id": original.get("thread_id"),
        "in_reply_to": msg_id_header,
        "references": references,
        "reply_to_message_id": args.message_id,
    }
    return PreparedWrite(
        preview=Preview(
            title="Draft a reply",
            summary=f"Save a reply draft in the thread '{original.get('subject') or '(no subject)'}'. Nothing will be sent.",
            target=_email_target(original),
            proposed=_compose_view(fields),
            recipients=to + cc,
        ),
        bridge_operation="create_draft",
        bridge_args=fields,
    )


async def prepare_update_draft(ctx: ToolContext, args: UpdateDraftArgs) -> PreparedWrite:
    existing = await ctx.bridge.call("get_draft", {"draft_id": args.draft_id})
    merged = {
        "draft_id": args.draft_id,
        "to": existing.get("to") or [],
        "cc": existing.get("cc") or [],
        "bcc": existing.get("bcc") or [],
        "subject": existing.get("subject") or "",
        "body": existing.get("body") or "",
        "thread_id": existing.get("thread_id"),
        "in_reply_to": existing.get("in_reply_to") or "",
        "references": existing.get("references") or "",
    }
    changed_old: dict[str, Any] = {}
    changed_new: dict[str, Any] = {}
    for f in ("to", "cc", "bcc", "subject", "body"):
        new = getattr(args, f)
        if new is not None and new != merged[f]:
            old_view, new_view = merged[f], new
            if isinstance(new, list):
                old_view, new_view = ", ".join(merged[f]) or "(none)", ", ".join(new) or "(none)"
            changed_old[f], changed_new[f] = old_view, new_view
            merged[f] = new
    if not changed_new:
        raise PreviewRejected("The requested values are the same as the current draft; nothing to update.")
    return PreparedWrite(
        preview=Preview(
            title="Update email draft",
            summary=f"Change {', '.join(changed_new)} of the draft '{existing.get('subject') or '(no subject)'}'.",
            target={"draft_id": args.draft_id, "subject": existing.get("subject") or "(no subject)"},
            existing=changed_old,
            proposed=changed_new,
            recipients=merged["to"] + merged["cc"] + merged["bcc"],
        ),
        bridge_operation="update_draft",
        bridge_args=merged,
    )


async def prepare_delete_draft(ctx: ToolContext, args: DraftIdArgs) -> PreparedWrite:
    existing = await ctx.bridge.call("get_draft", {"draft_id": args.draft_id})
    return PreparedWrite(
        preview=Preview(
            title="Delete email draft",
            summary=f"Delete the draft '{existing.get('subject') or '(no subject)'}'.",
            target={"draft_id": args.draft_id},
            existing=_compose_view(existing),
            warnings=["Deleted drafts can't be recovered from this app."],
            irreversible=True,
        ),
        bridge_operation="delete_draft",
        bridge_args={"draft_id": args.draft_id},
    )


async def prepare_send_draft(ctx: ToolContext, args: DraftIdArgs) -> PreparedWrite:
    existing = await ctx.bridge.call("get_draft", {"draft_id": args.draft_id})
    recipients = (existing.get("to") or []) + (existing.get("cc") or []) + (existing.get("bcc") or [])
    if not recipients:
        raise PreviewRejected("This draft has no recipients. Add a 'to' address before sending.")
    return PreparedWrite(
        preview=Preview(
            title="Send email draft",
            summary=f"Send the draft '{existing.get('subject') or '(no subject)'}' to {', '.join(recipients)}.",
            target={"draft_id": args.draft_id},
            proposed=_compose_view(existing),
            recipients=recipients,
            warnings=["Sent emails can't be unsent."],
            irreversible=True,
        ),
        bridge_operation="send_draft",
        bridge_args={"draft_id": args.draft_id},
    )


async def prepare_send_email(ctx: ToolContext, args: SendEmailArgs) -> PreparedWrite:
    fields = args.model_dump()
    recipients = args.to + args.cc + args.bcc
    return PreparedWrite(
        preview=Preview(
            title="Send email",
            summary=f"Send '{args.subject or '(no subject)'}' to {', '.join(recipients)}.",
            proposed=_compose_view(fields),
            recipients=recipients,
            warnings=["Sent emails can't be unsent."],
            irreversible=True,
        ),
        bridge_operation="send_email",
        bridge_args=fields,
    )


async def prepare_update_email_labels(ctx: ToolContext, args: UpdateEmailLabelsArgs) -> PreparedWrite:
    email = await ctx.bridge.call("read_email", {"message_id": args.message_id})
    labels = await ctx.bridge.call("list_gmail_labels", {})
    by_lower = {str(lbl.get("name", "")).lower(): lbl.get("name") for lbl in labels or []}

    warnings: list[str] = []
    add: list[str] = []
    for name in args.add_labels:
        match = by_lower.get(name.strip().lower())  # FR-17 case-insensitive
        if match is None:
            warnings.append(f"Label '{name.strip()}' doesn't exist yet and will be created.")
            add.append(name.strip())
        else:
            add.append(match)
    remove: list[str] = []
    for name in args.remove_labels:
        match = by_lower.get(name.strip().lower())
        if match is None:
            raise PreviewRejected(f"There is no label called '{name}'. Use list_gmail_labels to see available labels.")
        remove.append(match)

    current = email.get("labels") or []
    new_labels = [lbl for lbl in current if lbl.lower() not in {r.lower() for r in remove}]
    for lbl in add:
        if lbl.lower() not in {c.lower() for c in new_labels}:
            new_labels.append(lbl)

    target = _email_target(email)
    target["conversation"] = _conversation_text(email)
    if _conversation_size(email) > 1:
        warnings.append(
            f"Labels apply to the whole conversation ({_conversation_text(email)}), as in the Gmail web app."
        )
    return PreparedWrite(
        preview=Preview(
            title="Update email labels",
            summary="Change labels on this conversation"
            + (f": add {', '.join(add)}" if add else "")
            + (f"{';' if add else ':'} remove {', '.join(remove)}" if remove else "")
            + ".",
            target=target,
            existing={"labels": ", ".join(current) or "(none)"},
            proposed={"labels": ", ".join(new_labels) or "(none)"},
            warnings=warnings,
        ),
        bridge_operation="update_email_labels",
        bridge_args={"message_id": args.message_id, "add_labels": add, "remove_labels": remove},
    )


async def prepare_trash_email(ctx: ToolContext, args: MessageIdArgs) -> PreparedWrite:
    email = await ctx.bridge.call("read_email", {"message_id": args.message_id})
    if "TRASH" in [lbl.upper() for lbl in email.get("labels") or []]:
        raise PreviewRejected("That email is already in Trash.")
    return PreparedWrite(
        preview=Preview(
            title="Move email to Trash",
            summary=f"Move '{email.get('subject') or '(no subject)'}' to Trash. It can be restored later.",
            target=_email_target(email),
            existing={"labels": ", ".join(email.get("labels") or []) or "(none)"},
            proposed={"location": "Trash"},
            warnings=["Gmail permanently deletes items in Trash after 30 days."],
        ),
        bridge_operation="trash_email",
        bridge_args={"message_id": args.message_id},
    )


async def prepare_restore_email(ctx: ToolContext, args: MessageIdArgs) -> PreparedWrite:
    email = await ctx.bridge.call("read_email", {"message_id": args.message_id})
    if "TRASH" not in [lbl.upper() for lbl in email.get("labels") or []]:
        raise PreviewRejected("That email isn't in Trash, so there's nothing to restore.")
    target = _email_target(email)
    target["conversation"] = _conversation_text(email)
    return PreparedWrite(
        preview=Preview(
            title="Restore conversation from Trash",
            summary=f"Move the conversation '{email.get('subject') or '(no subject)'}' "
            f"({_conversation_text(email)}) out of Trash and back to the inbox.",
            target=target,
            existing={"location": "Trash"},
            proposed={"location": "Inbox"},
            warnings=["Gmail restores the whole conversation, not just this one message."]
            if _conversation_size(email) > 1 else [],
        ),
        bridge_operation="restore_email",
        bridge_args={"message_id": args.message_id},
    )
