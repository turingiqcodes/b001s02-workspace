"""Google Drive tools: listing, saving attachments, trashing files (FR-28..FR-32)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.errors import PreviewRejected
from app.services.base import MAX_ATTACHMENT_BYTES, Preview, PreparedWrite, ToolContext, human_size

ATTACHMENTS_FOLDER = "Email Attachments"


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListDriveFilesArgs(_Args):
    name_contains: str | None = Field(None, max_length=200, description="Only files whose name contains this text.")
    max_results: int = Field(20, ge=1, le=100)


class SaveAttachmentsArgs(_Args):
    message_id: str = Field(..., min_length=1, max_length=200, description="Email whose attachments to save.")
    attachment_names: list[str] | None = Field(
        None, max_length=50, description="Exact file names to save. Omit to save all attachments."
    )


class FileIdArgs(_Args):
    file_id: str = Field(..., min_length=1, max_length=200)


def parse_file(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": raw.get("id"),
        "name": raw.get("name"),
        "mime_type": raw.get("mime_type"),
        "size": raw.get("size"),
        "url": raw.get("url"),
        "created": raw.get("created"),
        "updated": raw.get("updated"),
    }


async def list_drive_files(ctx: ToolContext, args: ListDriveFilesArgs) -> Any:
    raw = await ctx.bridge.call("list_drive_files", args.model_dump(exclude_none=True))
    return {"files": [parse_file(f) for f in raw or []]}


async def prepare_save_attachments(ctx: ToolContext, args: SaveAttachmentsArgs) -> PreparedWrite:
    email = await ctx.bridge.call("read_email", {"message_id": args.message_id})
    attachments = email.get("attachments") or []
    if not attachments:
        raise PreviewRejected("That email has no attachments.")
    if args.attachment_names is not None:
        wanted = {n.lower() for n in args.attachment_names}
        chosen = [a for a in attachments if str(a.get("name", "")).lower() in wanted]
        missing = wanted - {str(a.get("name", "")).lower() for a in chosen}
        if missing:
            available = ", ".join(a.get("name", "?") for a in attachments)
            raise PreviewRejected(f"No attachment named {', '.join(sorted(missing))}. Available: {available}.")
    else:
        chosen = attachments
    to_save = [a for a in chosen if (a.get("size") or 0) <= MAX_ATTACHMENT_BYTES]
    skipped = [a for a in chosen if (a.get("size") or 0) > MAX_ATTACHMENT_BYTES]
    if not to_save:
        raise PreviewRejected(
            "Every selected attachment is larger than 25 MB, so nothing can be saved: "
            + ", ".join(f"{a.get('name')} ({human_size(a.get('size'))})" for a in skipped)
        )
    warnings = [f"Skipped (over 25 MB): {a.get('name')} ({human_size(a.get('size'))})" for a in skipped]
    return PreparedWrite(
        preview=Preview(
            title="Save attachments to Drive",
            summary=f"Save {len(to_save)} attachment(s) to the Drive folder '{ATTACHMENTS_FOLDER}'.",
            target={"email": email.get("subject") or "(no subject)", "from": email.get("from")},
            proposed={
                "folder": ATTACHMENTS_FOLDER,
                "files": ", ".join(f"{a.get('name')} ({human_size(a.get('size'))})" for a in to_save),
            },
            warnings=warnings,
        ),
        bridge_operation="save_attachments_to_drive",
        bridge_args={
            "message_id": args.message_id,
            "attachment_names": [a.get("name") for a in to_save],
            "folder_name": ATTACHMENTS_FOLDER,
        },
    )


async def prepare_trash_drive_file(ctx: ToolContext, args: FileIdArgs) -> PreparedWrite:
    existing = await ctx.bridge.call("get_drive_file", {"file_id": args.file_id})
    if existing.get("trashed"):
        raise PreviewRejected("That file is already in Drive Trash.")
    f = parse_file(existing)
    return PreparedWrite(
        preview=Preview(
            title="Move Drive file to Trash",
            summary=f"Move '{f['name']}' to Drive Trash. It can be restored from Drive for 30 days.",
            target={"file": f["name"], "url": f["url"]},
            existing={"name": f["name"], "type": f["mime_type"], "size": human_size(f["size"]), "updated": f["updated"]},
            proposed={"location": "Drive Trash"},
        ),
        bridge_operation="trash_drive_file",
        bridge_args={"file_id": args.file_id},
    )
