"""Claude (Anthropic API) client."""

from __future__ import annotations

import logging
from typing import Any

import anthropic

from ..errors import ModelError
from .llm import LLMResponse, ToolCall

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
REFUSAL_REPLY = "Claude declined this request. Try rephrasing it."


def _type(block: Any) -> str | None:
    return block.get("type") if isinstance(block, dict) else getattr(block, "type", None)


class ClaudeClient:
    provider = "claude"

    def __init__(self, model: str, *, api_key: str | None = None, effort: str = "medium",
                 refusal_fallback: bool = True, client: Any = None):
        self.model = model
        self.effort = effort
        self.refusal_fallback = refusal_fallback
        self._client = client if client is not None else anthropic.Anthropic(api_key=api_key, max_retries=2)

    @staticmethod
    def to_wire(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Neutral history → Messages API. Consecutive user-side entries merge into one user turn."""
        out: list[dict[str, Any]] = []

        def add_user(blocks: list[dict[str, Any]]) -> None:
            if out and out[-1]["role"] == "user":
                out[-1]["content"].extend(blocks)
            else:
                out.append({"role": "user", "content": blocks})

        for m in messages:
            if m["role"] == "user":
                add_user([{"type": "text", "text": m["content"]}])
            elif m["role"] == "tool":
                block: dict[str, Any] = {"type": "tool_result", "tool_use_id": m["tool_call_id"], "content": m["content"]}
                if m.get("is_error"):
                    block["is_error"] = True
                add_user([block])
            elif m["role"] == "assistant":
                raw = m.get("raw")
                if isinstance(raw, dict) and raw.get("provider") == "claude":
                    content = list(raw["content"])  # replay exactly as returned (thinking blocks included)
                else:
                    content = []
                    if m.get("content"):
                        content.append({"type": "text", "text": m["content"]})
                    for c in m.get("tool_calls") or []:
                        content.append({"type": "tool_use", "id": c["id"], "name": c["name"],
                                        "input": c["arguments"] if isinstance(c["arguments"], dict) else {}})
                    if not content:
                        content = [{"type": "text", "text": "(no response)"}]
                out.append({"role": "assistant", "content": content})
        return out

    def request_kwargs(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 16000,
            "system": [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            "tools": [{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools],
            "messages": self.to_wire(messages),
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": self.effort},
        }
        if self.refusal_fallback:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

    def chat(self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMResponse:
        try:
            response = self._client.beta.messages.create(**self.request_kwargs(system, messages, tools))
        except Exception as exc:  # noqa: BLE001 - mapped to a user-facing ModelError
            raise self.map_error(exc) from exc

        content = list(response.content or [])
        text = "\n".join(b.text for b in content if _type(b) == "text" and getattr(b, "text", ""))
        calls = [ToolCall(id=b.id, name=b.name, arguments=b.input) for b in content if _type(b) == "tool_use"]
        stop = response.stop_reason
        if stop == "refusal":
            # Don't replay a refused turn; the loop records a plain assistant message instead.
            return LLMResponse(text=REFUSAL_REPLY, stop_reason="refusal", raw=None)
        mapped = {"tool_use": "tool_use", "max_tokens": "max_tokens"}.get(stop or "", "end")
        if calls and mapped == "end":
            mapped = "tool_use"
        if mapped == "max_tokens":
            calls = []  # a tool call cut off mid-way is not safe to run
        return LLMResponse(text=text, tool_calls=calls, stop_reason=mapped,
                           raw={"provider": "claude", "content": content} if mapped != "max_tokens" else None)

    def map_error(self, exc: Exception) -> ModelError:
        if isinstance(exc, anthropic.AuthenticationError):
            return ModelError("Claude is not configured: the API key was rejected. Set ANTHROPIC_API_KEY and "
                              "restart the backend.", status=502)
        if isinstance(exc, anthropic.PermissionDeniedError):
            return ModelError("This Anthropic API key doesn't have access to the configured model.")
        if isinstance(exc, anthropic.NotFoundError):
            return ModelError(f"Claude model '{self.model}' was not found. Fix CLAUDE_MODEL.")
        if isinstance(exc, anthropic.RateLimitError):
            return ModelError("Claude is receiving too many requests right now. Wait a moment and try again.", status=429)
        if isinstance(exc, anthropic.BadRequestError):
            text = str(exc).lower()
            if "fallback" in text:
                return ModelError("Claude rejected the request because of the refusal fallbacks. "
                                  "Set CLAUDE_REFUSAL_FALLBACK=false.")
            log.warning("Claude rejected the request (bad request)")
            return ModelError("Claude couldn't process that request. Try rephrasing, or start a new conversation.")
        if isinstance(exc, anthropic.APITimeoutError):
            return ModelError("Claude took too long to respond. Please try again.", status=504)
        if isinstance(exc, anthropic.APIConnectionError):
            return ModelError("Couldn't connect to Claude. Check your internet connection.")
        if isinstance(exc, anthropic.APIStatusError):
            if exc.status_code == 529 or exc.status_code >= 500:
                return ModelError("Claude is temporarily unavailable. Please try again in a minute.")
            return ModelError(f"Claude returned an error (HTTP {exc.status_code}). Please try again.")
        if isinstance(exc, TypeError) and "authentication" in str(exc).lower():
            return ModelError("Claude is not configured: no API key found. Set ANTHROPIC_API_KEY and restart the backend.")
        log.error("Unexpected Claude client error: %s", type(exc).__name__)
        return ModelError("Something went wrong while calling Claude. Please try again.")

    def ping(self) -> tuple[bool | None, str | None]:
        return None, None
