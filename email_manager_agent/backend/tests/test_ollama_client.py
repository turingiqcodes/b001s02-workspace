from __future__ import annotations

import json
from typing import Any, Callable

import httpx
import pytest

from app.agent.ollama_client import OllamaClient, strip_thinking
from app.errors import ModelError

TOOLS = [{"name": "search_emails", "description": "d", "parameters": {"type": "object", "properties": {}}}]


def client(handler: Callable[[httpx.Request], httpx.Response], **kw: Any) -> OllamaClient:
    return OllamaClient("http://127.0.0.1:11434/", "qwen3:8b", transport=httpx.MockTransport(handler), **kw)


def reply(message: dict[str, Any], **extra: Any) -> Callable[[httpx.Request], httpx.Response]:
    return lambda req: httpx.Response(200, json={"message": message, "done": True, **extra})


def test_request_payload() -> None:
    seen: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/chat"
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "hi"}})

    history = [
        {"role": "user", "content": "find mail"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "name": "search_emails", "arguments": {"query": "x"}}]},
        {"role": "tool", "tool_call_id": "c1", "name": "search_emails", "content": "{}", "is_error": False},
    ]
    out = client(handler, num_ctx=8192, keep_alive="5m", think=False).chat("SYS", history, TOOLS)
    p = seen[0]
    assert out.text == "hi" and out.stop_reason == "end"
    assert p["model"] == "qwen3:8b" and p["stream"] is False and p["keep_alive"] == "5m"
    assert p["options"] == {"num_ctx": 8192} and p["think"] is False
    assert p["messages"][0] == {"role": "system", "content": "SYS"}
    assert p["messages"][2]["tool_calls"] == [{"function": {"name": "search_emails", "arguments": {"query": "x"}}}]
    assert p["messages"][3] == {"role": "tool", "tool_name": "search_emails", "content": "{}"}
    assert p["tools"][0] == {"type": "function", "function": TOOLS[0]}


def test_think_omitted_when_unset() -> None:
    seen: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"message": {"content": "ok"}})

    client(handler).chat("s", [], TOOLS)
    assert "think" not in seen[0]


def test_tool_calls_parsed_dict_and_string_args() -> None:
    msg = {"content": "", "tool_calls": [
        {"function": {"name": "search_emails", "arguments": {"query": "is:unread"}}},
        {"function": {"name": "read_email", "arguments": '{"message_id": "m1"}'}},
        {"function": {"name": "list_labels", "arguments": ""}},
        {"function": {"name": "read_email", "arguments": "{not json"}},
    ]}
    out = client(reply(msg)).chat("s", [], TOOLS)
    assert out.stop_reason == "tool_use"
    assert [c.arguments for c in out.tool_calls] == [{"query": "is:unread"}, {"message_id": "m1"}, {}, "{not json"]
    assert len({c.id for c in out.tool_calls}) == 4


def test_think_tags_stripped() -> None:
    assert strip_thinking("<think>plan...</think>\nHello") == "Hello"
    assert strip_thinking("<think>never closed") == ""
    out = client(reply({"content": "<think>x</think>Answer"})).chat("s", [], TOOLS)
    assert out.text == "Answer"


def test_length_stop_reason() -> None:
    out = client(reply({"content": "partial"}, done_reason="length")).chat("s", [], TOOLS)
    assert out.stop_reason == "max_tokens"


def test_connect_error() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(ModelError, match="Cannot connect to Ollama at http://127.0.0.1:11434") as e:
        client(handler).chat("s", [], TOOLS)
    assert e.value.status == 502


def test_timeout() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(ModelError, match="did not answer within 600 seconds") as e:
        client(handler).chat("s", [], TOOLS)
    assert e.value.status == 504


def test_model_not_installed() -> None:
    h = lambda req: httpx.Response(404, json={"error": "model 'qwen3:8b' not found"})
    with pytest.raises(ModelError, match="not installed. Run: ollama pull qwen3:8b"):
        client(h).chat("s", [], TOOLS)


def test_model_without_tool_support() -> None:
    h = lambda req: httpx.Response(400, json={"error": "registry.ollama.ai/library/gemma:2b does not support tools"})
    with pytest.raises(ModelError, match="does not support tool calling"):
        client(h).chat("s", [], TOOLS)


def test_model_without_thinking_support() -> None:
    h = lambda req: httpx.Response(400, json={"error": "\"llama3.2\" does not support thinking"})
    with pytest.raises(ModelError, match="Remove OLLAMA_THINK"):
        client(h).chat("s", [], TOOLS)


def test_other_http_error_and_non_json() -> None:
    with pytest.raises(ModelError, match="HTTP 500"):
        client(lambda req: httpx.Response(500, text="oops")).chat("s", [], TOOLS)
    with pytest.raises(ModelError, match="isn't JSON"):
        client(lambda req: httpx.Response(200, text="<html>")).chat("s", [], TOOLS)


def test_ping_model_installed() -> None:
    h = lambda req: httpx.Response(200, json={"models": [{"name": "qwen3:8b"}, {"name": "llama3.2:latest"}]})
    assert client(h).ping() == (True, None)
    bare = OllamaClient("http://x", "llama3.2", transport=httpx.MockTransport(h))
    assert bare.ping() == (True, None)


def test_ping_model_missing() -> None:
    h = lambda req: httpx.Response(200, json={"models": [{"name": "llama3.2:latest"}]})
    ok, detail = client(h).ping()
    assert ok is False and "ollama pull qwen3:8b" in (detail or "")


def test_ping_unreachable() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no")

    ok, detail = client(h).ping()
    assert ok is False and "Cannot connect" in (detail or "")
