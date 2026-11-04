"""Pending-operation lifecycle: propose → (approve | reject | expire), each at most once.

Write tools never run inside the agent loop. They become a PendingOperation
holding the exact validated parameters; ``confirm`` runs those parameters once.
"""

from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .agent.tools import TOOLS
from .errors import AcknowledgementRequired, ApprovalError, ApprovalExpired, BridgeError, NotFoundError
from .services import calendar, drive, gmail
from .services.base import Context, Proposal
from .sessions import Session

log = logging.getLogger(__name__)

PREVIEWS = {**gmail.PREVIEWS, **calendar.PREVIEWS, **drive.PREVIEWS}
DONE = {**gmail.DONE, **calendar.DONE, **drive.DONE}
REPEAT_WARNING = "You already did exactly this earlier in this conversation."
MAX_BODY_PREVIEW = 20000


def signature(tool: str, params: dict[str, Any]) -> str:
    return tool + ":" + json.dumps(params, sort_keys=True, ensure_ascii=False)


@dataclass
class PendingOperation:
    operation_id: str
    tool: str
    params: dict[str, Any]
    title: str
    summary: str
    details: list[dict[str, str]]
    body: str | None
    warnings: list[str]
    created_at: datetime
    expires_at: datetime
    meta: dict[str, Any] = field(default_factory=dict)

    def to_public(self) -> dict[str, Any]:
        body = self.body
        if body is not None and len(body) > MAX_BODY_PREVIEW:
            body = body[:MAX_BODY_PREVIEW] + "\n…"
        return {
            "operation_id": self.operation_id,
            "tool": self.tool,
            "title": self.title,
            "summary": self.summary,
            "details": [dict(d) for d in self.details],
            "body": body,
            "warnings": list(self.warnings),
            "requires_acknowledgement": bool(self.warnings),
            "created_at": _utc(self.created_at),
            "expires_at": _utc(self.expires_at),
        }


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def done_message(tool: str, result: Any) -> str:
    message = DONE.get(tool, "Done.")
    if isinstance(result, dict):
        skipped = result.get("skipped_attachments") or result.get("skipped") or []
        if isinstance(skipped, list) and skipped:
            names = [str(x.get("filename") or x.get("name") or "?") if isinstance(x, dict) else str(x) for x in skipped]
            message += f" Skipped attachments: {', '.join(names)}."
    return message


def propose(session: Session, tool: str, args: dict[str, Any], ctx: Context) -> PendingOperation:
    """Build and store a pending operation. Raises ProposalRejected if it can't be proposed."""
    proposal: Proposal = PREVIEWS[tool](ctx, args)
    warnings = list(proposal.warnings)
    if signature(tool, proposal.params) in session.executed:
        warnings.append(REPEAT_WARNING)
    now = ctx.now()
    op = PendingOperation(
        operation_id="op_" + secrets.token_hex(12),
        tool=tool,
        params=proposal.params,
        title=proposal.title or TOOLS[tool].label,
        summary=proposal.summary,
        details=proposal.details,
        body=proposal.body,
        warnings=warnings,
        created_at=now,
        expires_at=now + timedelta(seconds=ctx.settings.approval_ttl_seconds),
    )
    session.pending[op.operation_id] = op
    log.info("proposed operation %s (%s)", op.operation_id, tool)
    return op


def drop_expired(session: Session, now: datetime) -> None:
    for op_id in [i for i, op in session.pending.items() if op.expires_at <= now]:
        op = session.pending.pop(op_id)
        session.used_operations.add(op_id)
        session.updates.append(f"Operation {op_id} ({op.tool}) expired before the user approved it, so it was NOT performed.")


def confirm(session: Session, operation_id: str, decision: str, acknowledge_warnings: bool,
            ctx: Context) -> dict[str, Any]:
    if operation_id in session.used_operations and operation_id not in session.pending:
        raise ApprovalError("This approval was already used.")
    op = session.pending.get(operation_id)
    if op is None:
        raise NotFoundError("That approval was not found. It may belong to another conversation.")

    now = ctx.now()
    if op.expires_at <= now:
        session.pending.pop(operation_id)
        session.used_operations.add(operation_id)
        session.updates.append(f"Operation {operation_id} ({op.tool}) expired before the user approved it, so it was NOT performed.")
        raise ApprovalExpired("This approval expired. Ask again to get a new one.")

    if decision == "reject":
        session.pending.pop(operation_id)
        session.used_operations.add(operation_id)
        session.updates.append(f"The user rejected operation {operation_id} ({op.tool}: {op.summary}). It was NOT performed.")
        log.info("rejected operation %s (%s)", operation_id, op.tool)
        return {"status": "rejected", "message": "Cancelled. Nothing was changed.", "result": None}

    if op.warnings and not acknowledge_warnings:
        raise AcknowledgementRequired("Tick “I have reviewed these warnings” before approving.")

    new = [w for w in calendar.recheck_conflicts(ctx, op.tool, op.params) if w not in op.warnings]
    if new:
        op.warnings.extend(new)
        raise AcknowledgementRequired("Your calendar changed since this was proposed: " + " ".join(new)
                                      + " Review the new warning and approve again.")

    # Consume the approval before running so it can never run twice.
    session.pending.pop(operation_id)
    session.used_operations.add(operation_id)
    log.info("executing operation %s (%s)", operation_id, op.tool)
    try:
        result = ctx.bridge.call(op.tool, op.params)
    except BridgeError as exc:
        message = exc.message
        if exc.bridge_code in ("timeout", "unreachable", "http_error", "bad_response"):
            message += " Check Gmail/Calendar/Drive before retrying, because it may have gone through."
        session.updates.append(f"Operation {operation_id} ({op.tool}) was approved but FAILED: {message}")
        log.info("operation %s failed (%s)", operation_id, exc.bridge_code)
        return {"status": "failed", "message": message, "result": None}

    session.executed.add(signature(op.tool, op.params))
    done = done_message(op.tool, result)
    session.updates.append(f"Operation {operation_id} ({op.tool}: {op.summary}) was approved and executed. "
                           f"Result: {json.dumps(result, ensure_ascii=False)[:1000]}")
    return {"status": "executed", "message": done, "result": result}
