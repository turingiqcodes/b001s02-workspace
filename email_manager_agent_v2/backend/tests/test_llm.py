"""GroqClient: request shape and format conversion, against a mocked HTTP transport (no network)."""

import asyncio
import json

import groq
import httpx
import pytest

from app.agent.llm import GroqClient, to_groq_messages
from app.agent.tools import TOOL_SCHEMAS
from app.config import Settings
from app.errors import LLMError


def make(handler, **settings):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sdk = groq.AsyncGroq(api_key="gsk_test", http_client=http, max_retries=0)
    return GroqClient(Settings(groq_api_key="gsk_test", **settings), client=sdk)


def completion(message, finish_reason="stop"):
    return {
        "id": "chatcmpl-1", "object": "chat.completion", "created": 1, "model": "openai/gpt-oss-120b",
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


HISTORY = [
    {"role": "user", "content": [{"type": "text", "text": "[Context] now"}, {"type": "text", "text": "latest emails"}]},
    {"role": "assistant", "content": [
        {"type": "text", "text": "Checking."},
        {"type": "tool_use", "id": "call_1", "name": "get_latest_emails", "input": {"max_results": 3}},
        {"type": "tool_use", "id": "call_2", "name": "list_drafts", "input": {}},
    ]},
    {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "call_1", "content": '{"emails": []}'},
        {"type": "tool_result", "tool_use_id": "call_2", "content": "Bridge down", "is_error": True},
    ]},
]


def test_history_conversion():
    msgs = to_groq_messages("SYS", HISTORY)
    assert msgs[0] == {"role": "system", "content": "SYS"}
    assert msgs[1] == {"role": "user", "content": "[Context] now\n\nlatest emails"}
    assert msgs[2]["role"] == "assistant" and msgs[2]["content"] == "Checking."
    assert [c["function"]["name"] for c in msgs[2]["tool_calls"]] == ["get_latest_emails", "list_drafts"]
    assert json.loads(msgs[2]["tool_calls"][0]["function"]["arguments"]) == {"max_results": 3}
    assert msgs[3] == {"role": "tool", "tool_call_id": "call_1", "content": '{"emails": []}'}
    assert msgs[4] == {"role": "tool", "tool_call_id": "call_2", "content": "ERROR: Bridge down"}


def test_merged_user_turn_puts_tool_results_before_text():
    msgs = to_groq_messages("S", [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "c", "content": "x"}, {"type": "text", "text": "next"},
    ]}])
    assert [m["role"] for m in msgs] == ["system", "tool", "user"]


def test_request_shape_and_tool_call_response():
    seen = {}

    def handler(req: httpx.Request):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=completion({
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "call_9", "type": "function",
                            "function": {"name": "read_email", "arguments": '{"message_id": "m1"}'}}],
        }, "tool_calls"))

    resp = asyncio.run(make(handler).create("sys", HISTORY, TOOL_SCHEMAS))
    body = seen["body"]
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["reasoning_effort"] == "medium"
    assert body["tool_choice"] == "auto"
    assert len(body["tools"]) == len(TOOL_SCHEMAS)
    assert body["tools"][0]["function"]["parameters"]["type"] == "object"
    assert resp.stop_reason == "tool_use"
    assert resp.content == [{"type": "tool_use", "id": "call_9", "name": "read_email", "input": {"message_id": "m1"}}]


def test_text_answer_and_no_reasoning_effort_when_empty():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=completion({"role": "assistant", "content": "You have 3 emails."}))

    resp = asyncio.run(make(handler, groq_model="llama-3.3-70b-versatile", groq_reasoning_effort="").create("s", HISTORY, []))
    assert "reasoning_effort" not in seen["body"] and "tools" not in seen["body"]
    assert resp.stop_reason == "end_turn"
    assert resp.content == [{"type": "text", "text": "You have 3 emails."}]


def test_bad_json_arguments_become_validation_error_input():
    def handler(req):
        return httpx.Response(200, json=completion({
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "c", "type": "function", "function": {"name": "read_email", "arguments": "{oops"}}],
        }, "tool_calls"))

    resp = asyncio.run(make(handler).create("s", HISTORY, TOOL_SCHEMAS))
    assert resp.content[0]["input"] == {"_unparseable_arguments": "{oops"}


def test_tool_use_failed_is_retried():
    calls = {"n": 0, "last": None}

    def handler(req):
        calls["n"] += 1
        calls["last"] = json.loads(req.content)["messages"][-1]
        if calls["n"] == 1:
            return httpx.Response(400, json={"error": {"message": "Failed to call a function", "type": "invalid_request_error", "code": "tool_use_failed"}})
        return httpx.Response(200, json=completion({"role": "assistant", "content": "ok"}))

    resp = asyncio.run(make(handler).create("s", HISTORY, TOOL_SCHEMAS))
    assert calls["n"] == 2 and resp.content[0]["text"] == "ok"
    assert calls["last"]["role"] == "system" and "previous tool call was rejected" in calls["last"]["content"]


def test_auth_error_is_readable():
    def handler(req):
        return httpx.Response(401, json={"error": {"message": "Invalid API Key", "type": "invalid_request_error", "code": "invalid_api_key"}})

    with pytest.raises(LLMError, match="GROQ_API_KEY"):
        asyncio.run(make(handler).create("s", HISTORY, []))


# ---------- staying under Groq's tokens-per-minute limit ----------

from app.agent.llm import compact_schema, fit_history  # noqa: E402


def test_compact_schema_unwraps_optional_and_drops_noise():
    schema = {"type": "object", "title": "X", "additionalProperties": False, "properties": {
        "q": {"anyOf": [{"type": "string", "maxLength": 5}, {"type": "null"}], "default": None, "description": "d", "title": "Q"},
        "n": {"type": "integer", "default": 5, "maximum": 50},
    }}
    assert compact_schema(schema) == {"type": "object", "properties": {
        "q": {"type": "string", "description": "d"},
        "n": {"type": "integer", "default": 5, "maximum": 50},
    }}


def test_fit_history_trims_old_tool_results_first():
    big = "x" * 10000
    msgs = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "a", "type": "function", "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "a", "content": big},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "new question"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "b", "type": "function", "function": {"name": "f", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "b", "content": "y" * 3000},
    ]
    out = fit_history(msgs, 6000)
    assert len(out[3]["content"]) < 1600  # old result trimmed
    assert out[7]["content"] == "y" * 3000  # current result kept
    assert out[0]["content"] == "SYS" and out[5]["content"] == "new question"


def test_too_large_request_retries_smaller_then_succeeds():
    sizes = []

    def handler(req):
        sizes.append(len(req.content))
        if len(sizes) == 1:
            return httpx.Response(413, json={"error": {"message": "Request too large", "type": "tokens", "code": "rate_limit_exceeded"}})
        return httpx.Response(200, json=completion({"role": "assistant", "content": "ok"}))

    big = HISTORY + [{"role": "assistant", "content": [{"type": "text", "text": "z" * 20000}]},
                     {"role": "user", "content": [{"type": "text", "text": "again"}]}]
    resp = asyncio.run(make(handler, history_char_budget=30000).create("s", big, []))
    assert resp.content[0]["text"] == "ok"
    assert sizes[1] < sizes[0] - 5000  # second attempt was smaller


def test_too_large_request_gives_clear_message():
    def handler(req):
        return httpx.Response(413, json={"error": {"message": "Request too large ... Limit 8000, Requested 9088", "type": "tokens", "code": "rate_limit_exceeded"}})

    with pytest.raises(LLMError, match="too large for your Groq plan") as exc:
        asyncio.run(make(handler).create("s", HISTORY, []))
    assert exc.value.status_code == 413


def test_no_format_constraints_sent_to_groq():
    from app.agent.llm import to_groq_tools

    assert '"format"' not in json.dumps(to_groq_tools(TOOL_SCHEMAS))
