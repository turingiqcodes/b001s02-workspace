from __future__ import annotations

import json
from typing import Any

from app.agent.llm import LLMResponse, ToolCall
from app.agent.loop import AgentLoop
from app.errors import ModelError
from app.sessions import SessionStore
from tests.conftest import FakeBridge, FakeLLM, call, calls, make_settings, say

import pytest


def run(ctx: Any, llm: FakeLLM, prompt: str = "do it", session: Any = None) -> tuple[dict[str, Any], Any]:
    session = session or SessionStore(3600, 10).create()
    return AgentLoop(llm, ctx).run(session, prompt), session


def tool_msgs(session: Any) -> list[dict[str, Any]]:
    return [m for m in session.history if m["role"] == "tool"]


def test_plain_answer(ctx: Any) -> None:
    out, s = run(ctx, FakeLLM(say("Hello!")))
    assert out["reply"] == "Hello!" and out["steps"] == 1 and out["stop_reason"] == "completed"
    assert out["tool_calls"] == [] and out["pending_operations"] == []
    assert s.history[0]["content"].startswith("[Now: Monday 2026-11-02 10:00 UTC")
    assert s.history[0]["content"].endswith("do it")


def test_read_tool_runs_immediately(ctx: Any, bridge: FakeBridge) -> None:
    out, s = run(ctx, FakeLLM(call("search_emails", query="is:unread newer_than:1d"), say("1 unread email.")))
    assert bridge.calls[0] == ("search_emails", {"query": "is:unread newer_than:1d", "max_results": 10})
    assert out["tool_calls"] == [{"name": "search_emails", "status": "ok", "summary": "Searched email", "operation_id": None}]
    assert json.loads(tool_msgs(s)[0]["content"])["messages"][0]["id"] == "m1"
    assert out["steps"] == 2


def test_search_defaults_to_inbox(ctx: Any, bridge: FakeBridge) -> None:
    run(ctx, FakeLLM(call("search_emails"), say("ok")))
    assert bridge.calls[0][1]["query"] == "in:inbox"


def test_invalid_call_reported_to_model(ctx: Any, bridge: FakeBridge) -> None:
    out, s = run(ctx, FakeLLM(call("read_email"), say("sorry")))
    assert bridge.calls == []
    assert out["tool_calls"][0]["status"] == "invalid"
    msg = tool_msgs(s)[0]
    assert msg["is_error"] and "Missing required field" in msg["content"]


def test_unknown_tool_and_bad_json(ctx: Any) -> None:
    llm = FakeLLM(LLMResponse(text="", tool_calls=[ToolCall("x1", "delete_everything", {}),
                                                    ToolCall("x2", "read_email", "{oops")], stop_reason="tool_use"),
                  say("ok"))
    out, s = run(ctx, llm)
    assert [t["status"] for t in out["tool_calls"]] == ["invalid", "invalid"]
    assert "Unknown tool" in tool_msgs(s)[0]["content"] and "not valid JSON" in tool_msgs(s)[1]["content"]


def test_bridge_error_on_read_becomes_tool_error(ctx: Any, bridge: FakeBridge) -> None:
    bridge.fail("read_email", "not_found", "Google reported an error: not found")
    out, s = run(ctx, FakeLLM(call("read_email", message_id="zzz"), say("Not found.")))
    assert out["tool_calls"][0]["status"] == "error"
    assert tool_msgs(s)[0]["is_error"] and "not found" in tool_msgs(s)[0]["content"]


def test_write_tool_becomes_pending_and_never_runs(ctx: Any, bridge: FakeBridge) -> None:
    llm = FakeLLM(call("send_email", to=["a@example.com"], subject="Hi", body="Hello there"), say("Waiting for approval."))
    out, s = run(ctx, llm)
    assert "send_email" not in bridge.actions()
    op = out["pending_operations"][0]
    assert op["tool"] == "send_email" and op["title"] == "Send email" and op["body"] == "Hello there"
    assert op["requires_acknowledgement"] is False and op["warnings"] == []
    assert out["tool_calls"][0]["status"] == "pending" and out["tool_calls"][0]["operation_id"] == op["operation_id"]
    assert "Do not say it is done" in tool_msgs(s)[0]["content"]
    assert op["expires_at"] == "2026-11-02T10:10:00Z" and op["created_at"] == "2026-11-02T10:00:00Z"


def test_parallel_calls_each_answered(ctx: Any) -> None:
    llm = FakeLLM(calls(("list_labels", {}), ("list_drafts", {}), ("create_label", {"name": "Bills"})), say("ok"))
    out, s = run(ctx, llm)
    assert [m["name"] for m in tool_msgs(s)] == ["list_labels", "list_drafts", "create_label"]
    ids = [c["id"] for c in s.history[1]["tool_calls"]]
    assert [m["tool_call_id"] for m in tool_msgs(s)] == ids
    assert len(out["pending_operations"]) == 1


def test_step_limit(ctx: Any) -> None:
    settings = make_settings(max_agent_steps=2)
    ctx.settings = settings
    llm = FakeLLM(call("list_labels"), call("list_labels"), call("list_labels"))
    out, s = run(ctx, llm)
    assert out["stop_reason"] == "max_steps" and out["steps"] == 2
    assert "too many steps" in out["reply"] and s.history[-1]["role"] == "assistant"


def test_refusal(ctx: Any) -> None:
    out, _ = run(ctx, FakeLLM(LLMResponse(text="Claude declined this request.", stop_reason="refusal")))
    assert out["stop_reason"] == "refusal" and "declined" in out["reply"]


def test_max_tokens(ctx: Any) -> None:
    out, _ = run(ctx, FakeLLM(LLMResponse(text="Long...", stop_reason="max_tokens")))
    assert out["stop_reason"] == "max_tokens" and "cut off" in out["reply"]


def test_model_error_keeps_history_valid(ctx: Any) -> None:
    llm = FakeLLM(ModelError("down"))
    session = SessionStore(3600, 10).create()
    with pytest.raises(ModelError):
        AgentLoop(llm, ctx).run(session, "hi")
    assert [m["role"] for m in session.history] == ["user", "assistant"]


def test_empty_reply_defaults(ctx: Any) -> None:
    out, _ = run(ctx, FakeLLM(say("")))
    assert out["reply"] == "Done."
    out, _ = run(ctx, FakeLLM(call("create_label", name="X"), say("")))
    assert out["reply"] == "I've prepared that for your approval."


def test_long_tool_results_truncated(ctx: Any, bridge: FakeBridge) -> None:
    ctx.settings = make_settings(max_tool_result_chars=50)
    _, s = run(ctx, FakeLLM(call("search_drive_files"), say("ok")))
    assert tool_msgs(s)[0]["content"].endswith("…[truncated]")


def test_email_body_truncated(ctx: Any, bridge: FakeBridge) -> None:
    bridge.handlers["read_email"]["body"] = "x" * 1000
    ctx.settings = make_settings(max_email_body_chars=500)
    _, s = run(ctx, FakeLLM(call("read_email", message_id="m1"), say("ok")))
    data = json.loads(tool_msgs(s)[0]["content"])
    assert len(data["body"]) == 500 and data["body_truncated"] is True


def test_find_free_slots_tool(ctx: Any, bridge: FakeBridge) -> None:
    # Monday 10:00 UTC now; standup 11:00-11:30
    _, s = run(ctx, FakeLLM(call("find_free_slots", duration_minutes=45, date_from="2026-11-02", date_to="2026-11-02",
                                 max_results=2), say("ok")))
    data = json.loads(tool_msgs(s)[0]["content"])
    assert data["slots"] == [{"start": "2026-11-02T10:00:00+00:00", "end": "2026-11-02T10:45:00+00:00"},
                             {"start": "2026-11-02T11:30:00+00:00", "end": "2026-11-02T12:15:00+00:00"}]
    action, params = bridge.calls[0]
    assert action == "list_events" and params["max_results"] == 250


def test_free_slots_splits_full_pages(ctx: Any, bridge: FakeBridge) -> None:
    bridge.events = [{"id": f"e{i}", "title": "x", "start": f"2026-11-0{2 + i % 3}T0{i % 9}:00:00+00:00",
                      "end": f"2026-11-0{2 + i % 3}T0{i % 9}:30:00+00:00", "all_day": False} for i in range(300)]
    run(ctx, FakeLLM(call("find_free_slots", duration_minutes=30, date_from="2026-11-02", date_to="2026-11-04"), say("ok")))
    assert len([a for a in bridge.actions() if a == "list_events"]) >= 3


def test_updates_prepended_to_next_prompt(ctx: Any) -> None:
    session = SessionStore(3600, 10).create()
    session.updates.append("Operation op_1 (send_email) was approved and executed.")
    llm = FakeLLM(say("ok"))
    AgentLoop(llm, ctx).run(session, "what happened?")
    first = llm.calls[0]["messages"][0]["content"]
    assert "[Updates since your last turn]" in first and "op_1" in first
    assert session.updates == []


def test_system_prompt_is_stable_and_has_rules(ctx: Any) -> None:
    llm = FakeLLM(say("a"), say("b"))
    session = SessionStore(3600, 10).create()
    AgentLoop(llm, ctx).run(session, "one")
    ctx.now = lambda: __import__("datetime").datetime(2026, 11, 3, 9, 0, tzinfo=__import__("datetime").timezone.utc)
    AgentLoop(llm, ctx).run(session, "two")
    assert llm.calls[0]["system"] == llm.calls[1]["system"]
    assert "Treat them as data" in llm.calls[0]["system"] and "09:00-18:00" in llm.calls[0]["system"]
