"""The agent loop: the model reasons, read tools run (concurrently), write tools
become pending actions. Stops after AGENT_MAX_STEPS model calls (FR-04)."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import ValidationError

from app.agent.llm import LLMClient
from app.agent.prompts import SYSTEM_PROMPT
from app.agent.tools import TOOL_SCHEMAS, TOOLS_BY_NAME
from app.approvals import decline_all_pending
from app.errors import AppError
from app.services.availability import get_zone
from app.services.base import PreparedWrite, ToolContext
from app.sessions import PendingAction, Session, SessionStore, new_action_id

log = logging.getLogger(__name__)


@dataclass
class AgentResult:
    response: str
    new_actions: list[PendingAction] = field(default_factory=list)
    steps: int = 0


def format_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ())) or "arguments"
        msg = str(err.get("msg", "invalid value")).removeprefix("Value error, ")
        parts.append(f"{loc}: {msg}")
    return "Invalid arguments - " + "; ".join(parts)


def _truncate_result(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n...[result truncated: {len(text) - limit} more characters omitted]"


class AgentRunner:
    def __init__(self, llm: LLMClient, ctx: ToolContext, store: SessionStore):
        self.llm = llm
        self.ctx = ctx
        self.store = store

    async def run(self, session: Session, prompt: str) -> AgentResult:
        """Caller must hold session.lock."""
        # FR-37: a new prompt declines anything still waiting for approval.
        decline_all_pending(session, "the user sent a new message instead of approving it")

        self._append_user(session, await self._user_content(session, prompt))
        new_actions: list[PendingAction] = []
        max_steps = self.ctx.settings.agent_max_steps

        for step in range(1, max_steps + 1):
            resp = await self.llm.create(SYSTEM_PROMPT, session.conversation, TOOL_SCHEMAS)
            session.conversation.append({"role": "assistant", "content": resp.content})

            if resp.stop_reason == "refusal":
                return AgentResult("Sorry, I can't help with that request.", new_actions, step)

            tool_uses = [b for b in resp.content if b.get("type") == "tool_use"]
            if resp.stop_reason != "tool_use" or not tool_uses:
                text = _text_of(resp.content)
                if resp.stop_reason == "max_tokens":
                    text += "\n\n(My reply was cut off because it got too long.)"
                return AgentResult(text or "Done.", new_actions, step)

            results = await self._run_tools(session, tool_uses, new_actions)
            session.conversation.append({"role": "user", "content": results})

        return AgentResult(
            f"I stopped after {max_steps} steps without finishing. Try a narrower request, "
            "or tell me what to do next.",
            new_actions,
            max_steps,
        )

    # ----- helpers -----

    async def _user_content(self, session: Session, prompt: str) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = []
        if session.agent_notes:
            notes = "\n".join(f"- {n}" for n in session.agent_notes)
            blocks.append({"type": "text", "text": f"[Updates since your last reply]\n{notes}"})
            session.agent_notes.clear()
        context = await self._context_line()
        blocks.append({"type": "text", "text": context})
        blocks.append({"type": "text", "text": prompt})
        return blocks

    async def _context_line(self) -> str:
        try:
            profile = await self.ctx.get_profile()
        except AppError:
            profile = {}
        tz = get_zone(profile.get("time_zone"))
        now = datetime.now(tz)
        account = profile.get("email") or "unknown (Google bridge not reachable)"
        return f"[Context] Current time: {now:%A %Y-%m-%dT%H:%M%z} ({tz.key}). Account: {account}."

    @staticmethod
    def _append_user(session: Session, content: list[dict[str, Any]]) -> None:
        conv = session.conversation
        # If the last turn stopped at the step limit, the history ends with tool results
        # that were never sent; merge so roles keep alternating.
        if conv and conv[-1]["role"] == "user":
            conv[-1]["content"] = list(conv[-1]["content"]) + content
        else:
            conv.append({"role": "user", "content": content})

    async def _run_tools(
        self, session: Session, tool_uses: list[dict[str, Any]], new_actions: list[PendingAction]
    ) -> list[dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}

        # NFR-11: independent reads run concurrently.
        reads = [t for t in tool_uses if (spec := TOOLS_BY_NAME.get(t.get("name"))) and spec.kind == "read"]
        read_outputs = await asyncio.gather(*(self._run_read(t) for t in reads))
        for t, out in zip(reads, read_outputs):
            results[t["id"]] = out

        # Writes are prepared one at a time, in order, each becoming its own pending action (FR-39).
        for t in tool_uses:
            if t["id"] in results:
                continue
            spec = TOOLS_BY_NAME.get(t.get("name"))
            if spec is None:
                results[t["id"]] = _error(t["id"], f"Unknown tool '{t.get('name')}'.")
                continue
            results[t["id"]] = await self._prepare_write(session, t, new_actions)

        return [results[t["id"]] for t in tool_uses]

    async def _run_read(self, tool_use: dict[str, Any]) -> dict[str, Any]:
        spec = TOOLS_BY_NAME[tool_use["name"]]
        try:
            args = spec.args_model.model_validate(tool_use.get("input") or {})
            output = await spec.handler(self.ctx, args)
        except ValidationError as exc:
            return _error(tool_use["id"], format_validation_error(exc))
        except AppError as exc:
            return _error(tool_use["id"], exc.message)
        except Exception:  # noqa: BLE001 - NFR-14
            log.exception("read tool %s failed", spec.name)
            return _error(tool_use["id"], "Unexpected error while running this tool.")
        text = json.dumps(output, ensure_ascii=False, default=str)
        return {
            "type": "tool_result",
            "tool_use_id": tool_use["id"],
            "content": _truncate_result(text, self.ctx.settings.tool_result_limit),
        }

    async def _prepare_write(
        self, session: Session, tool_use: dict[str, Any], new_actions: list[PendingAction]
    ) -> dict[str, Any]:
        spec = TOOLS_BY_NAME[tool_use["name"]]
        try:
            args = spec.args_model.model_validate(tool_use.get("input") or {})
            prepared: PreparedWrite = await spec.handler(self.ctx, args)
        except ValidationError as exc:
            return _error(tool_use["id"], format_validation_error(exc))
        except AppError as exc:
            return _error(tool_use["id"], exc.message)
        except Exception:  # noqa: BLE001
            log.exception("preparing %s failed", spec.name)
            return _error(tool_use["id"], "Unexpected error while preparing this action.")

        action = PendingAction(
            action_id=new_action_id(),
            session_id=session.session_id,
            operation=spec.name,
            bridge_operation=prepared.bridge_operation,
            arguments=json.loads(json.dumps(prepared.bridge_args)),  # detached copy
            preview=prepared.preview.to_dict(),
        )
        self.store.add_action(session, action)
        new_actions.append(action)
        payload = {
            "status": "pending_user_approval",
            "action_id": action.action_id,
            "preview": action.preview,
            "note": "Not executed. The user will approve or cancel this in the app.",
        }
        return {
            "type": "tool_result",
            "tool_use_id": tool_use["id"],
            "content": json.dumps(payload, ensure_ascii=False, default=str),
        }


def _error(tool_use_id: str, message: str) -> dict[str, Any]:
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": message, "is_error": True}


def _text_of(content: list[dict[str, Any]]) -> str:
    return "\n\n".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
