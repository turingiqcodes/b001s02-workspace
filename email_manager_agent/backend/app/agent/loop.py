"""The agent loop.

The backend owns the loop: the model only picks tools. Every call is
validated. Read tools run now; write tools become pending operations that the
user approves later through POST /api/confirm. The loop never executes a write.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .. import approvals
from ..errors import BridgeError
from ..services import calendar, drive, gmail
from ..services.base import Context, ProposalRejected
from ..sessions import Session
from .llm import LLMClient, ToolCall
from .prompts import system_prompt, time_context
from .tools import READ_TOOLS, TOOLS, WRITE_TOOLS, ToolValidationError, tool_definitions, validate_args

log = logging.getLogger(__name__)

READS = {**gmail.READS, **calendar.READS, **drive.READS}
STEP_LIMIT_REPLY = ("I stopped because this request needed too many steps. Anything waiting for approval is "
                    "listed below. Try a smaller, more specific request.")


def proposed_result(op: approvals.PendingOperation) -> str:
    return (f"Proposed as operation {op.operation_id}: {op.summary}. It will only happen if the user approves it "
            "in the app. Do not say it is done.")


class AgentLoop:
    def __init__(self, llm: LLMClient, ctx: Context):
        self.llm = llm
        self.ctx = ctx

    def _truncate(self, text: str) -> str:
        limit = self.ctx.settings.max_tool_result_chars
        return text if len(text) <= limit else text[:limit] + " …[truncated]"

    def run(self, session: Session, prompt: str) -> dict[str, Any]:
        settings = self.ctx.settings
        approvals.drop_expired(session, self.ctx.now())
        user_text = time_context(settings, self.ctx.now())
        if session.updates:
            user_text += "\n[Updates since your last turn]\n" + "\n".join(f"- {u}" for u in session.updates)
            session.updates.clear()
        session.history.append({"role": "user", "content": f"{user_text}\n\n{prompt}"})

        system = system_prompt(settings)
        tools = tool_definitions(settings)
        tool_calls: list[dict[str, Any]] = []
        replies: list[str] = []
        steps = 0
        stop_reason = "completed"

        while True:
            if steps >= settings.max_agent_steps:
                stop_reason = "max_steps"
                replies.append(STEP_LIMIT_REPLY)
                session.history.append({"role": "assistant", "content": "(Stopped: step limit reached.)"})
                break
            steps += 1
            try:
                response = self.llm.chat(system, session.history, tools)
            except Exception:
                # Keep history valid for the next attempt: close the turn.
                session.history.append({"role": "assistant", "content": "(The model call failed.)"})
                raise
            if response.text:
                replies.append(response.text)

            if response.stop_reason == "refusal":
                stop_reason = "refusal"
                session.history.append({"role": "assistant", "content": response.text})
                break
            if not response.tool_calls:
                stop_reason = "max_tokens" if response.stop_reason == "max_tokens" else "completed"
                if stop_reason == "max_tokens":
                    replies.append("(The answer was cut off because it was too long.)")
                session.history.append({"role": "assistant", "content": response.text or "", "raw": response.raw})
                break

            session.history.append({
                "role": "assistant",
                "content": response.text or "",
                "tool_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in response.tool_calls],
                "raw": response.raw,
            })
            for call in response.tool_calls:
                content, is_error, record = self._handle(session, call)
                tool_calls.append(record)
                session.history.append({"role": "tool", "tool_call_id": call.id, "name": call.name,
                                        "content": self._truncate(content), "is_error": is_error})

        reply = "\n\n".join(r for r in replies if r).strip()
        if not reply:
            reply = "I've prepared that for your approval." if session.pending else "Done."
        return {
            "session_id": session.id,
            "reply": reply,
            "steps": steps,
            "stop_reason": stop_reason,
            "tool_calls": tool_calls,
            "pending_operations": [op.to_public() for op in session.pending.values()],
        }

    def _handle(self, session: Session, call: ToolCall) -> tuple[str, bool, dict[str, Any]]:
        spec = TOOLS.get(call.name)
        label = spec.label if spec else call.name
        record: dict[str, Any] = {"name": call.name, "status": "ok", "summary": label, "operation_id": None}
        if isinstance(call.arguments, str):
            record.update(status="invalid", summary=f"{label}: arguments were not valid JSON")
            return "Invalid call: the arguments were not valid JSON. Send a JSON object.", True, record
        try:
            args = validate_args(call.name, call.arguments, self.ctx.settings)
        except ToolValidationError as exc:
            log.info("invalid tool call: %s", call.name)
            record.update(status="invalid", summary=f"{label}: {exc}")
            return f"Invalid call: {exc}", True, record

        if call.name in READ_TOOLS:
            log.info("read tool: %s", call.name)
            try:
                data = READS[call.name](self.ctx, args)
            except BridgeError as exc:
                record.update(status="error", summary=f"{label}: failed")
                return f"Error: {exc.message}", True, record
            return json.dumps(data, ensure_ascii=False, default=str), False, record

        assert call.name in WRITE_TOOLS
        try:
            op = approvals.propose(session, call.name, args, self.ctx)
        except ProposalRejected as exc:
            record.update(status="invalid", summary=f"{label}: {exc}")
            return f"Can't propose this: {exc}", True, record
        except BridgeError as exc:
            record.update(status="error", summary=f"{label}: failed")
            return f"Error: {exc.message}", True, record
        record.update(status="pending", summary=f"{op.title}: waiting for approval", operation_id=op.operation_id)
        return proposed_result(op), False, record
