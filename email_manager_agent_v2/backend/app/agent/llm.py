"""LLM abstraction so the model can be swapped for a fake in tests (NFR-19).

The agent loop keeps the conversation as content blocks
(text / tool_use / tool_result). GroqClient converts that history to Groq's
OpenAI-style chat format on each call and converts the reply back.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

import groq

from app.config import Settings
from app.errors import LLMError, NotConfiguredError

log = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    content: list[dict[str, Any]]  # text / tool_use blocks
    stop_reason: str | None  # "tool_use" | "end_turn" | "max_tokens"


class LLMClient(Protocol):
    async def create(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LLMResponse: ...


# ---------- format conversion ----------

def compact_schema(schema: Any) -> Any:
    """Shrink a JSON schema to save tokens. Arguments are still fully validated
    by the pydantic models before anything runs."""
    if isinstance(schema, list):
        return [compact_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    s = dict(schema)
    any_of = s.get("anyOf")
    if isinstance(any_of, list):
        non_null = [x for x in any_of if x != {"type": "null"}]
        if len(non_null) == 1:  # Optional[X] -> X
            s.pop("anyOf")
            s = {**non_null[0], **s}
    # "format" is dropped too: Groq enforces it server-side and fails the whole request on a
    # mismatch (e.g. a date-time where a date was expected). Pydantic still validates it.
    for key in ("title", "maxLength", "minLength", "maxItems", "minItems", "additionalProperties", "format"):
        s.pop(key, None)
    if s.get("default", 0) is None or s.get("default") == []:
        s.pop("default")
    return {k: compact_schema(v) for k, v in s.items()}


def to_groq_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": compact_schema(t["input_schema"]),
            },
        }
        for t in tools
    ]


def _shorten(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + " ...[trimmed to save space]"


def fit_history(messages: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
    """Keep the request under the token limit: trim the oldest tool results first,
    then old long texts. The system prompt and the latest user message are kept."""
    def size() -> int:
        return sum(len(m.get("content") or "") + len(json.dumps(m.get("tool_calls") or "")) for m in messages)

    last_user = max((i for i, m in enumerate(messages) if m["role"] == "user"), default=len(messages))
    for limit in (1500, 400, 120):
        for i, m in enumerate(messages):
            if size() <= budget:
                return messages
            if m["role"] == "tool" and (i < last_user or limit <= 400):
                m["content"] = _shorten(m["content"], limit)
            elif m["role"] in ("user", "assistant") and 0 < i < last_user and m.get("content"):
                m["content"] = _shorten(m["content"], limit * 2)
    return messages


def to_groq_messages(system: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for msg in messages:
        content = msg["content"]
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        if msg["role"] == "assistant":
            text = "\n\n".join(b["text"] for b in content if b.get("type") == "text" and b.get("text"))
            calls = [
                {
                    "id": b["id"],
                    "type": "function",
                    "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False)},
                }
                for b in content
                if b.get("type") == "tool_use"
            ]
            entry: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                entry["tool_calls"] = calls
            out.append(entry)
        else:
            # Tool results must directly follow the assistant tool calls; user text comes after.
            for b in content:
                if b.get("type") == "tool_result":
                    body = b.get("content") if isinstance(b.get("content"), str) else json.dumps(b.get("content"))
                    if b.get("is_error"):
                        body = f"ERROR: {body}"
                    out.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": body})
            text = "\n\n".join(b["text"] for b in content if b.get("type") == "text")
            if text:
                out.append({"role": "user", "content": text})
    return out


def from_groq_message(message: Any, finish_reason: str | None) -> LLMResponse:
    blocks: list[dict[str, Any]] = []
    if message.content:
        blocks.append({"type": "text", "text": message.content})
    for call in message.tool_calls or []:
        raw = call.function.arguments or "{}"
        try:
            args = json.loads(raw)
            if not isinstance(args, dict):
                raise ValueError("arguments must be an object")
        except ValueError:
            # Surfaces as a readable validation error to the model on the next step.
            args = {"_unparseable_arguments": raw[:500]}
        blocks.append({"type": "tool_use", "id": call.id, "name": call.function.name, "input": args})
    if any(b["type"] == "tool_use" for b in blocks):
        stop = "tool_use"
    elif finish_reason == "length":
        stop = "max_tokens"
    else:
        stop = "end_turn"
    return LLMResponse(blocks, stop)


# ---------- Groq client ----------

class GroqClient:
    TOOL_RETRIES = 2  # Groq returns 400 tool_use_failed when the model emits a malformed call

    def __init__(self, settings: Settings, client: groq.AsyncGroq | None = None):
        self._settings = settings
        # max_retries: the SDK waits and retries 429s using Groq's retry-after header.
        self._client = client or groq.AsyncGroq(api_key=settings.groq_api_key, max_retries=3, timeout=120)

    async def create(
        self, system: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
    ) -> LLMResponse:
        params: dict[str, Any] = {
            "model": self._settings.groq_model,
            "messages": fit_history(to_groq_messages(system, messages), self._settings.history_char_budget),
            "max_completion_tokens": self._settings.groq_max_tokens,
        }
        if tools:
            params["tools"] = to_groq_tools(tools)
            params["tool_choice"] = "auto"
        if self._settings.groq_reasoning_effort:
            params["reasoning_effort"] = self._settings.groq_reasoning_effort

        shrunk = False
        for attempt in range(self.TOOL_RETRIES + 2):
            try:
                resp = await self._client.chat.completions.create(**params)
                break
            except groq.AuthenticationError as exc:
                raise LLMError("Groq rejected the API key. Check GROQ_API_KEY in backend/.env.") from exc
            except groq.PermissionDeniedError as exc:
                raise LLMError("This Groq API key isn't allowed to use the configured model.") from exc
            except groq.RateLimitError as exc:
                raise LLMError(
                    "Groq's per-minute limit for your account was reached. Wait about a minute and try again.",
                    status_code=429,
                ) from exc
            except groq.BadRequestError as exc:
                if _error_code(exc) == "tool_use_failed" and attempt < self.TOOL_RETRIES:
                    log.warning("Groq rejected a tool call (%s); retrying (%d)", _error_message(exc)[:120], attempt + 1)
                    # Tell the model what was wrong so the retry can correct it.
                    params["messages"] = params["messages"] + [{
                        "role": "system",
                        "content": "Your previous tool call was rejected: " + _error_message(exc)
                        + " Call the tool again with arguments that exactly match its parameter types "
                        "(dates as YYYY-MM-DD, numbers as numbers, omit optional fields you don't need).",
                    }]
                    continue
                raise LLMError(f"The AI model couldn't process the request: {_error_message(exc)}") from exc
            except groq.NotFoundError as exc:
                raise LLMError(f"Groq doesn't recognise the model '{self._settings.groq_model}'. Check GROQ_MODEL.") from exc
            except groq.APIStatusError as exc:
                if exc.status_code == 413 and not shrunk:
                    # Too big for the per-minute limit: trim history much harder and retry once.
                    log.warning("Groq 413 (request too large); retrying with a smaller conversation")
                    shrunk = True
                    params["messages"] = fit_history(params["messages"], max(3000, self._settings.history_char_budget // 3))
                    continue
                if exc.status_code == 413:
                    raise LLMError(
                        "That request was too large for your Groq plan's per-minute token limit. "
                        "Try asking about fewer emails at once (e.g. 'my latest 3 emails'), or click "
                        "Reset session to clear the conversation.",
                        status_code=413,
                    ) from exc
                raise LLMError(f"Groq returned an error (HTTP {exc.status_code}). Try again shortly.") from exc
            except groq.APIConnectionError as exc:
                raise LLMError("Couldn't connect to Groq. Check your internet connection.") from exc

        choice = resp.choices[0]
        return from_groq_message(choice.message, choice.finish_reason)


def _error_body(exc: groq.APIStatusError) -> dict[str, Any]:
    body = exc.body if isinstance(exc.body, dict) else {}
    err = body.get("error") if isinstance(body.get("error"), dict) else body
    return err if isinstance(err, dict) else {}


def _error_code(exc: groq.APIStatusError) -> str:
    return str(_error_body(exc).get("code") or "")


def _error_message(exc: groq.APIStatusError) -> str:
    return str(_error_body(exc).get("message") or "bad request")[:300]


class UnconfiguredLLM:
    async def create(self, system, messages, tools) -> LLMResponse:  # noqa: ANN001
        raise NotConfiguredError("The AI model isn't configured. Set GROQ_API_KEY in backend/.env and restart.")
