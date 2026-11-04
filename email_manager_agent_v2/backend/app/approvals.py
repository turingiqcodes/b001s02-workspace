"""Approval enforcement (FR-33..FR-39, NFR-05, NFR-15).

The only code path that runs a write against Google is `execute_action`, and it
is reachable only from POST /api/confirm with approved=true. The agent loop can
create pending actions but never execute them.
"""

from __future__ import annotations

import logging

from app.agent.tools import describe_result
from app.errors import ActionStateError, BridgeError
from app.services.bridge import BridgeClient
from app.sessions import PendingAction, Session

log = logging.getLogger(__name__)


def decline_action(session: Session, action: PendingAction, reason: str) -> None:
    if action.status != "pending":
        raise ActionStateError(f"This action is already {action.status}.")
    action.status = "declined"
    action.message = "The operation was cancelled."
    session.agent_notes.append(
        f"Action {action.action_id} ({action.operation}) was DECLINED: {reason}. It was not executed."
    )


def decline_all_pending(session: Session, reason: str) -> list[PendingAction]:
    declined = session.pending()
    for action in declined:
        decline_action(session, action, reason)
    return declined


async def execute_action(session: Session, action: PendingAction, bridge: BridgeClient) -> None:
    """Run exactly the stored bridge call. Caller must hold session.lock."""
    if action.status != "pending":
        # Prevents double execution (NFR-15): a second approve finds a non-pending status.
        raise ActionStateError(f"This action is already {action.status}.")
    action.status = "approved"
    try:
        result = await bridge.call(action.bridge_operation, action.arguments)
    except BridgeError as exc:
        action.status = "failed"
        action.message = f"The operation failed: {exc.message}"
        session.agent_notes.append(
            f"Action {action.action_id} ({action.operation}) was approved but FAILED: {exc.message}"
        )
        return
    except Exception:  # noqa: BLE001 - never crash the server (NFR-14)
        log.exception("unexpected error executing %s", action.operation)
        action.status = "failed"
        action.message = "The operation failed because of an unexpected error."
        session.agent_notes.append(f"Action {action.action_id} ({action.operation}) was approved but FAILED.")
        return
    action.status = "executed"
    action.result = result
    action.message = describe_result(action.bridge_operation, result)
    session.agent_notes.append(
        f"Action {action.action_id} ({action.operation}) was APPROVED and EXECUTED. Result: {action.message}"
    )
