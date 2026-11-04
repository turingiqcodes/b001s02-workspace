"""Gmail: read tools, approval previews and result messages."""

from __future__ import annotations

from typing import Any

from ..errors import BridgeError
from .base import Context, Proposal, ProposalRejected, join, short

MANY_RECIPIENTS = 5
# System labels that must never be added or removed (removing/adding these would hide or "delete" mail).
FORBIDDEN_LABELS = {"TRASH", "SPAM", "SENT", "DRAFT", "CHAT"}
ALLOWED_SYSTEM = {"INBOX", "UNREAD", "STARRED", "IMPORTANT"}


# ------------------------------------------------------------------- reads
def search_emails(ctx: Context, a: dict[str, Any]) -> Any:
    params = {"query": a.get("query") or "in:inbox", "max_results": a.get("max_results", 10)}
    return ctx.bridge.call("search_emails", params)


def read_email(ctx: Context, a: dict[str, Any]) -> Any:
    data = ctx.bridge.call("read_email", {"message_id": a["message_id"]})
    limit = ctx.settings.max_email_body_chars
    if isinstance(data, dict) and isinstance(data.get("body"), str) and len(data["body"]) > limit:
        data = {**data, "body": data["body"][:limit], "body_truncated": True}
    return data


def list_labels(ctx: Context, a: dict[str, Any]) -> Any:
    return ctx.bridge.call("list_labels", {})


def list_drafts(ctx: Context, a: dict[str, Any]) -> Any:
    return ctx.bridge.call("list_drafts", {"max_results": a.get("max_results", 10)})


READS = {"search_emails": search_emails, "read_email": read_email,
         "list_labels": list_labels, "list_drafts": list_drafts}


# ---------------------------------------------------------------- previews
def _lookup(ctx: Context, action: str, params: dict[str, Any]) -> dict[str, Any] | None:
    """Context for a preview. Failures are non-fatal: the card still shows the inputs."""
    try:
        data = ctx.bridge.call(action, params)
    except BridgeError:
        return None
    return data if isinstance(data, dict) else None


def _recipient_warning(count: int) -> list[str]:
    return [f"This goes to {count} recipients."] if count > MANY_RECIPIENTS else []


def _addr_details(a: dict[str, Any]) -> list[dict[str, str]]:
    details = [{"label": "To", "value": join(a.get("to"))}]
    if a.get("cc"):
        details.append({"label": "Cc", "value": join(a["cc"])})
    if a.get("bcc"):
        details.append({"label": "Bcc", "value": join(a["bcc"])})
    return details


def p_send_email(ctx: Context, a: dict[str, Any]) -> Proposal:
    count = sum(len(a.get(k, [])) for k in ("to", "cc", "bcc"))
    return Proposal("Send email", f'To {join(a["to"])} — "{short(a["subject"], 60)}"', a,
                    _addr_details(a) + [{"label": "Subject", "value": a["subject"] or "(no subject)"}],
                    a["body"], _recipient_warning(count))


def _original(ctx: Context, message_id: str) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    msg = _lookup(ctx, "read_email", {"message_id": message_id})
    if not msg:
        return None, [{"label": "Email", "value": message_id}]
    return msg, [{"label": "Original", "value": f'{msg.get("from", "?")} — "{short(msg.get("subject"), 60)}"'}]


def p_reply_to_email(ctx: Context, a: dict[str, Any]) -> Proposal:
    msg, details = _original(ctx, a["message_id"])
    reply_all = a.get("reply_all", False)
    details.append({"label": "Reply to", "value": "Everyone on the email" if reply_all else "Sender only"})
    warnings: list[str] = []
    if msg and reply_all:
        count = len([x for x in (str(msg.get("to") or "") + "," + str(msg.get("cc") or "")).split(",") if x.strip()])
        warnings = _recipient_warning(count)
    who = msg.get("from", "the sender") if msg else "the email"
    return Proposal("Reply to email", f"Reply to {who}", a, details, a["body"], warnings)


def p_forward_email(ctx: Context, a: dict[str, Any]) -> Proposal:
    msg, details = _original(ctx, a["message_id"])
    if msg and msg.get("attachments"):
        details.append({"label": "Attachments", "value": str(len(msg["attachments"]))})
    subject = short(msg.get("subject"), 60) if msg else a["message_id"]
    return Proposal("Forward email", f'Forward "{subject}" to {join(a["to"])}', a,
                    [{"label": "To", "value": join(a["to"])}] + details, a.get("note"), _recipient_warning(len(a["to"])))


def p_create_draft(ctx: Context, a: dict[str, Any]) -> Proposal:
    details = _addr_details(a) + [{"label": "Subject", "value": a["subject"] or "(no subject)"}]
    if a.get("reply_to_message_id"):
        _, orig = _original(ctx, a["reply_to_message_id"])
        details += orig
    return Proposal("Create draft", f'Draft to {join(a["to"])} — "{short(a["subject"], 60)}"', a, details, a["body"])


def _addrs(value: Any) -> str:
    if isinstance(value, list):
        return join(value)
    return str(value) if value else "—"


def p_update_draft(ctx: Context, a: dict[str, Any]) -> Proposal:
    current = _lookup(ctx, "get_draft", {"draft_id": a["draft_id"]}) or {}
    details = []
    for key, label in (("to", "To"), ("cc", "Cc"), ("bcc", "Bcc"), ("subject", "Subject")):
        if key in a:
            new = _addrs(a[key])
            details.append({"label": label, "value": f"{new}  (was: {_addrs(current.get(key))})" if current else new})
    subject = a.get("subject") or current.get("subject") or a["draft_id"]
    return Proposal("Update draft", f'Change draft "{short(subject, 60)}"', a, details, a.get("body"))


def p_send_draft(ctx: Context, a: dict[str, Any]) -> Proposal:
    draft = _lookup(ctx, "get_draft", {"draft_id": a["draft_id"]})
    if not draft:
        return Proposal("Send draft", f"Send draft {a['draft_id']}", a, [{"label": "Draft", "value": a["draft_id"]}])
    to = _addrs(draft.get("to"))
    details = [{"label": "To", "value": to}]
    if draft.get("cc"):
        details.append({"label": "Cc", "value": _addrs(draft["cc"])})
    if draft.get("bcc"):
        details.append({"label": "Bcc", "value": _addrs(draft["bcc"])})
    details.append({"label": "Subject", "value": draft.get("subject") or "(no subject)"})
    warnings = [] if draft.get("to") else ["This draft has no recipients yet."]
    return Proposal("Send draft", f'Send "{short(draft.get("subject"), 60)}" to {to}', a,
                    details, draft.get("body"), warnings)


def p_create_label(ctx: Context, a: dict[str, Any]) -> Proposal:
    if a["name"].strip().upper() in FORBIDDEN_LABELS | ALLOWED_SYSTEM:
        raise ProposalRejected(f"'{a['name']}' is a system label name and can't be created.")
    return Proposal("Create label", f'Create label "{a["name"]}"', a, [{"label": "Name", "value": a["name"]}])


def p_modify_labels(ctx: Context, a: dict[str, Any]) -> Proposal:
    for name in a.get("add_labels", []) + a.get("remove_labels", []):
        upper = name.upper()
        if upper in FORBIDDEN_LABELS or upper.startswith("CATEGORY_"):
            raise ProposalRejected(f"The '{name}' label can't be changed here (this app never deletes or hides mail that way).")
    n = len(a["message_ids"])
    details = [{"label": "Emails", "value": str(n)}]
    if a.get("add_labels"):
        details.append({"label": "Add", "value": join(a["add_labels"])})
    if a.get("remove_labels"):
        details.append({"label": "Remove", "value": join(a["remove_labels"])})
    parts = []
    if a.get("add_labels"):
        parts.append("add " + join(a["add_labels"]))
    if a.get("remove_labels"):
        parts.append("remove " + join(a["remove_labels"]))
    warnings = [f"This changes {n} emails."] if n > 20 else []
    text = " and ".join(parts)
    return Proposal("Change labels", f"{text[:1].upper()}{text[1:]} on {n} email{'s' if n != 1 else ''}",
                    a, details, None, warnings)


PREVIEWS = {"send_email": p_send_email, "reply_to_email": p_reply_to_email, "forward_email": p_forward_email,
            "create_draft": p_create_draft, "update_draft": p_update_draft, "send_draft": p_send_draft,
            "create_label": p_create_label, "modify_labels": p_modify_labels}

DONE = {"send_email": "Email sent.", "reply_to_email": "Reply sent.", "forward_email": "Email forwarded.",
        "create_draft": "Draft saved.", "update_draft": "Draft updated.", "send_draft": "Draft sent.",
        "create_label": "Label created.", "modify_labels": "Labels updated."}
