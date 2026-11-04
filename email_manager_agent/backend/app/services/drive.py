"""Google Drive: search, approval previews and result messages."""

from __future__ import annotations

from typing import Any

from ..errors import BridgeError
from .base import Context, Proposal, ProposalRejected, short

DEFAULT_FOLDER = "Email Attachments"


def _mb(size: Any) -> str:
    try:
        return f"{int(size) / (1024 * 1024):.1f} MB"
    except (TypeError, ValueError):
        return "unknown size"


def search_drive_files(ctx: Context, a: dict[str, Any]) -> Any:
    params = {"mime_type": a.get("mime_type", "any"), "max_results": a.get("max_results", 10)}
    for key in ("name_contains", "modified_after", "folder_id"):
        if a.get(key):
            params[key] = a[key]
    return ctx.bridge.call("search_drive_files", params)


READS = {"search_drive_files": search_drive_files}


def _file_name(ctx: Context, file_id: str) -> str:
    try:
        data = ctx.bridge.call("get_file", {"file_id": file_id})
    except BridgeError:
        return file_id
    return (data or {}).get("name") or file_id


def p_create_drive_folder(ctx: Context, a: dict[str, Any]) -> Proposal:
    parent = _file_name(ctx, a["parent_folder_id"]) if a.get("parent_folder_id") else "My Drive"
    return Proposal("Create Drive folder", f'Create folder "{short(a["name"], 60)}" in {parent}', a,
                    [{"label": "Name", "value": a["name"]}, {"label": "In", "value": parent}])


def p_move_drive_file(ctx: Context, a: dict[str, Any]) -> Proposal:
    name, folder = _file_name(ctx, a["file_id"]), _file_name(ctx, a["folder_id"])
    return Proposal("Move Drive file", f'Move "{short(name, 60)}" to {folder}', a,
                    [{"label": "File", "value": name}, {"label": "To folder", "value": folder}])


def p_save_attachments(ctx: Context, a: dict[str, Any]) -> Proposal:
    try:
        msg = ctx.bridge.call("read_email", {"message_id": a["message_id"]})
    except BridgeError as exc:
        raise ProposalRejected(f"Couldn't load that email: {exc.message}") from exc
    attachments = list((msg or {}).get("attachments") or [])
    if not attachments:
        raise ProposalRejected("That email has no attachments.")
    by_id = {str(x.get("attachment_id")): x for x in attachments}
    wanted = a.get("attachment_ids") or list(by_id)
    unknown = [i for i in wanted if i not in by_id]
    if unknown:
        raise ProposalRejected(f"Attachment id(s) not on that email: {', '.join(unknown)}.")
    limit = ctx.settings.max_attachment_mb * 1024 * 1024
    keep, skipped = [], []
    for att_id in wanted:
        att = by_id[att_id]
        size = att.get("size") or 0
        (skipped if isinstance(size, (int, float)) and size > limit else keep).append(att)
    if not keep:
        raise ProposalRejected(f"Every attachment is larger than {ctx.settings.max_attachment_mb} MB, so nothing can be saved.")
    params: dict[str, Any] = {"message_id": a["message_id"], "attachment_ids": [str(x["attachment_id"]) for x in keep]}
    if a.get("folder_id"):
        params["folder_id"] = a["folder_id"]
        folder = _file_name(ctx, a["folder_id"])
    else:
        params["folder_name"] = a.get("folder_name") or DEFAULT_FOLDER
        folder = f'{params["folder_name"]} (created if missing)'
    warnings = []
    if skipped:
        names = ", ".join(f'{x.get("filename")} ({_mb(x.get("size"))})' for x in skipped)
        warnings.append(f"Skipped (larger than {ctx.settings.max_attachment_mb} MB): {names}.")
    files = ", ".join(f'{x.get("filename")} ({_mb(x.get("size"))})' for x in keep)
    return Proposal("Save attachments to Drive",
                    f'Save {len(keep)} attachment{"s" if len(keep) != 1 else ""} from "{short((msg or {}).get("subject"), 50)}"',
                    params,
                    [{"label": "Email", "value": f'{(msg or {}).get("from", "?")} — "{short((msg or {}).get("subject"), 60)}"'},
                     {"label": "Files", "value": files}, {"label": "Folder", "value": folder}],
                    None, warnings)


PREVIEWS = {"create_drive_folder": p_create_drive_folder, "move_drive_file": p_move_drive_file,
            "save_attachments_to_drive": p_save_attachments}

DONE = {"create_drive_folder": "Folder created.", "move_drive_file": "File moved.",
        "save_attachments_to_drive": "Attachments saved to Drive."}
