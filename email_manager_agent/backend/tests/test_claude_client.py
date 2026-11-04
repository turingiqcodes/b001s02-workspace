from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest

from app.agent.claude_client import FALLBACK_BETA, REFUSAL_REPLY, ClaudeClient
from app.errors import ModelError

TOOLS = [{"name": "search_emails", "description": "d", "parameters": {"type": "object", "properties": {}}}]


class FakeAnthropic:
    def __init__(self, *responses: Any):
        self.queue = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def text(t: str) -> Any:
    return SimpleNamespace(type="text", text=t)


def tool_use(id_: str, name: str, input_: dict[str, Any]) -> Any:
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


def thinking() -> Any:
    return SimpleNamespace(type="thinking", thinking="", signature="sig")


def resp(*blocks: Any, stop: str = "end_turn") -> Any:
    return SimpleNamespace(content=list(blocks), stop_reason=stop)


def make(*responses: Any, **kw: Any) -> tuple[ClaudeClient, FakeAnthropic]:
    fake = FakeAnthropic(*responses)
    return ClaudeClient("claude-opus-5-5", client=fake, **kw), fake


def test_request_kwargs_with_fallbacks() -> None:
    c, fake = make(resp(text("hi")), effort="high")
    out = c.chat("SYS", [{"role": "user", "content": "hello"}], TOOLS)
    k = fake.requests[0]
    assert out.text == "hi" and out.stop_reason == "end"
    assert k["model"] == "claude-opus-5-5" and k["max_tokens"] == 16000
    assert k["thinking"] == {"type": "adaptive"} and k["output_config"] == {"effort": "high"}
    assert k["system"] == [{"type": "text", "text": "SYS", "cache_control": {"type": "ephemeral"}}]
    assert k["tools"] == [{"name": "search_emails", "description": "d", "input_schema": TOOLS[0]["parameters"]}]
    assert k["betas"] == [FALLBACK_BETA] and k["fallbacks"] == "default"
    assert "temperature" not in k and "tool_choice" not in k


def test_fallbacks_can_be_disabled() -> None:
    c, fake = make(resp(text("hi")), refusal_fallback=False)
    c.chat("s", [{"role": "user", "content": "x"}], TOOLS)
    assert "betas" not in fake.requests[0] and "fallbacks" not in fake.requests[0]


def test_tool_use_parsed_and_raw_kept() -> None:
    blocks = [thinking(), text("Looking."), tool_use("tu1", "search_emails", {"query": "x"})]
    c, _ = make(resp(*blocks, stop="tool_use"))
    out = c.chat("s", [{"role": "user", "content": "x"}], TOOLS)
    assert out.stop_reason == "tool_use" and out.text == "Looking."
    assert [(t.id, t.name, t.arguments) for t in out.tool_calls] == [("tu1", "search_emails", {"query": "x"})]
    assert out.raw == {"provider": "claude", "content": blocks}


def test_raw_blocks_replayed_unchanged_and_tool_results_grouped() -> None:
    blocks = [thinking(), tool_use("a", "search_emails", {}), tool_use("b", "list_labels", {})]
    history = [
        {"role": "user", "content": "x"},
        {"role": "assistant", "content": "", "tool_calls": [], "raw": {"provider": "claude", "content": blocks}},
        {"role": "tool", "tool_call_id": "a", "name": "search_emails", "content": "{}", "is_error": False},
        {"role": "tool", "tool_call_id": "b", "name": "list_labels", "content": "bad", "is_error": True},
        {"role": "user", "content": "next"},
    ]
    wire = ClaudeClient.to_wire(history)
    assert wire[1] == {"role": "assistant", "content": blocks}
    assert wire[2]["role"] == "user" and len(wire) == 3
    assert wire[2]["content"][0] == {"type": "tool_result", "tool_use_id": "a", "content": "{}"}
    assert wire[2]["content"][1]["is_error"] is True
    assert wire[2]["content"][2] == {"type": "text", "text": "next"}


def test_assistant_without_raw_is_rebuilt() -> None:
    history = [{"role": "user", "content": "x"},
               {"role": "assistant", "content": "hi", "tool_calls": [{"id": "c", "name": "n", "arguments": {"a": 1}}]},
               {"role": "assistant", "content": ""}]
    wire = ClaudeClient.to_wire(history)
    assert wire[1]["content"] == [{"type": "text", "text": "hi"}, {"type": "tool_use", "id": "c", "name": "n", "input": {"a": 1}}]
    assert wire[2]["content"] == [{"type": "text", "text": "(no response)"}]


def test_refusal() -> None:
    c, _ = make(resp(text("partial"), stop="refusal"))
    out = c.chat("s", [{"role": "user", "content": "x"}], TOOLS)
    assert out.stop_reason == "refusal" and out.text == REFUSAL_REPLY and out.raw is None and not out.tool_calls


def test_max_tokens_drops_tool_calls() -> None:
    c, _ = make(resp(text("long"), tool_use("t", "search_emails", {}), stop="max_tokens"))
    out = c.chat("s", [{"role": "user", "content": "x"}], TOOLS)
    assert out.stop_reason == "max_tokens" and out.tool_calls == []


def _status(cls: type, code: int, msg: str = "err") -> Exception:
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls(msg, response=httpx.Response(code, request=req), body=None)


@pytest.mark.parametrize("exc,match,status", [
    (lambda: _status(anthropic.AuthenticationError, 401), "Claude is not configured", 502),
    (lambda: _status(anthropic.PermissionDeniedError, 403), "doesn't have access", 502),
    (lambda: _status(anthropic.NotFoundError, 404), "Claude model 'claude-opus-5-5' was not found", 502),
    (lambda: _status(anthropic.RateLimitError, 429), "too many requests", 429),
    (lambda: _status(anthropic.BadRequestError, 400, "fallbacks not allowed"), "CLAUDE_REFUSAL_FALLBACK=false", 502),
    (lambda: _status(anthropic.BadRequestError, 400, "bad"), "couldn't process", 502),
    (lambda: _status(anthropic.InternalServerError, 500), "temporarily unavailable", 502),
    (lambda: anthropic.APITimeoutError(request=httpx.Request("POST", "https://x")), "too long", 504),
    (lambda: anthropic.APIConnectionError(request=httpx.Request("POST", "https://x")), "Couldn't connect", 502),
    (lambda: TypeError("Could not resolve authentication method"), "no API key", 502),
    (lambda: ValueError("weird"), "Something went wrong", 502),
])
def test_error_mapping(exc: Any, match: str, status: int) -> None:
    c, _ = make(exc())
    with pytest.raises(ModelError, match=match) as e:
        c.chat("s", [{"role": "user", "content": "x"}], TOOLS)
    assert e.value.status == status and e.value.code == "model_error"


def test_ping_is_not_checked() -> None:
    c, _ = make()
    assert c.ping() == (None, None)
