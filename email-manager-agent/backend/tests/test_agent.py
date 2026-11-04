from __future__ import annotations

import anthropic
import httpx
import pytest

from app.agent import FALLBACK_BETA, REJECTED_RESULT, TurnConflict
from app.bridge import BridgeError
from tests.conftest import response, text, thinking, tool_use


def _user_messages_with_results(request):
    return [m for m in request["messages"] if m["role"] == "user" and isinstance(m["content"], list)
            and any(isinstance(c, dict) and c.get("type") == "tool_result" for c in m["content"])]


def test_plain_answer(agent, claude):
    claude.script(response(text("Hello! How can I help?")))
    s = agent.new_session()
    r = agent.send_message(s, "hi")
    assert r.reply == "Hello! How can I help?"
    assert r.pending == [] and r.error is None
    req = claude.requests[0]
    assert req["model"] == "claude-opus-5-5"
    assert req["thinking"] == {"type": "adaptive"}
    assert req["output_config"] == {"effort": "medium"}
    assert req["betas"] == [FALLBACK_BETA] and req["fallbacks"] == "default"
    assert "<context>" in req["messages"][0]["content"]


def test_read_tools_run_without_approval(agent, claude, bridge):
    claude.script(
        response(thinking(), tool_use("t1", "search_emails", {"query": "is:unread"})),
        response(text("You have 1 unread email from Priya about the Apollo launch.")),
    )
    s = agent.new_session()
    r = agent.send_message(s, "summarize unread")
    assert "Priya" in r.reply
    assert r.activity == [{"tool": "search_emails", "label": "Searched email", "ok": True}]
    assert ("search_emails", {"query": "is:unread"}) in bridge.calls
    # Thinking block is passed back unchanged in the assistant turn.
    assistant = claude.requests[1]["messages"][1]
    assert assistant["role"] == "assistant" and assistant["content"][0].type == "thinking"
    result = claude.requests[1]["messages"][2]["content"][0]
    assert result["tool_use_id"] == "t1" and "Apollo" in result["content"]


def test_write_tool_waits_for_approval_then_runs(agent, claude, bridge):
    send = {"to": ["priya@acme.example"], "subject": "Re: Apollo", "body": "Thursday 3pm works.",
            "reply_to_message_id": "m1"}
    claude.script(response(text("Here's the reply."), tool_use("t1", "send_email", send)))
    s = agent.new_session()
    r = agent.send_message(s, "reply to priya")

    assert bridge.sent == []  # nothing sent before approval
    assert len(r.pending) == 1
    card = r.pending[0]
    assert card["title"] == "Send email" and card["body"] == "Thursday 3pm works."
    assert {"label": "In reply to", "value": "Apollo launch timeline — Priya Shah <priya@acme.example>"} in card["fields"]
    assert card["warning"]

    with pytest.raises(TurnConflict):
        agent.send_message(s, "another thing")

    claude.script(response(text("Sent.")))
    r2 = agent.decide(s, {"t1": True})
    assert len(bridge.sent) == 1 and bridge.sent[0]["threadId"] == "t1"
    assert r2.executed[0]["status"] == "done"
    assert r2.reply == "Sent." and r2.pending == []


def test_rejected_action_is_not_executed(agent, claude, bridge):
    claude.script(response(tool_use("t1", "cancel_event", {"event_id": "e1"})))
    s = agent.new_session()
    r = agent.send_message(s, "cancel standup")
    assert r.pending[0]["title"] == "Cancel meeting"
    assert {"label": "Meeting", "value": "Team standup"} in r.pending[0]["fields"]

    claude.script(response(text("OK, I left it in place.")))
    r2 = agent.decide(s, {"t1": False})
    assert "e1" in bridge.events
    assert r2.executed[0]["status"] == "rejected"
    sent_result = claude.requests[-1]["messages"][-1]["content"][0]
    assert sent_result == {"type": "tool_result", "tool_use_id": "t1", "content": REJECTED_RESULT}


def test_mixed_read_and_write_results_returned_in_one_message(agent, claude, bridge):
    claude.script(response(
        tool_use("r1", "list_labels", {}),
        tool_use("w1", "modify_labels", {"message_ids": ["m2"], "add_labels": ["Receipts"]}),
        tool_use("w2", "save_attachment_to_drive", {"message_id": "m2", "attachment_id": "a2"}),
    ))
    s = agent.new_session()
    r = agent.send_message(s, "file the invoice")
    assert [p["action_id"] for p in r.pending] == ["w1", "w2"]

    with pytest.raises(TurnConflict):
        agent.decide(s, {"w1": True})  # must decide all

    claude.script(response(text("Labelled; the attachment was not saved.")))
    r2 = agent.decide(s, {"w1": True, "w2": False})
    assert "Label_1" in bridge.messages["m2"]["labelIds"]
    assert bridge.drive_files == []
    results = _user_messages_with_results(claude.requests[-1])
    assert len(results) == 1
    assert [c["tool_use_id"] for c in results[0]["content"]] == ["r1", "w1", "w2"]
    assert [e["status"] for e in r2.executed] == ["done", "rejected"]


def test_failed_action_reported_in_plain_language(agent, claude, bridge):
    claude.script(response(tool_use("t1", "send_draft", {"draft_id": "nope"})))
    s = agent.new_session()
    agent.send_message(s, "send my draft")
    claude.script(response(text("That draft no longer exists.")))
    r = agent.decide(s, {"t1": True})
    assert r.executed[0]["status"] == "failed"
    assert r.executed[0]["detail"] == "Draft nope was not found."
    assert claude.requests[-1]["messages"][-1]["content"][0]["is_error"] is True


def test_invalid_tool_input_is_returned_to_claude(agent, claude):
    claude.script(
        response(tool_use("t1", "create_event", {"summary": "x"})),
        response(text("I need a start and end time.")),
    )
    s = agent.new_session()
    r = agent.send_message(s, "make a meeting")
    assert r.pending == []
    res = claude.requests[1]["messages"][-1]["content"][0]
    assert res["is_error"] and "start" in res["content"]


def test_bridge_error_on_read_is_shown_to_claude(agent, claude, bridge, monkeypatch):
    def boom(action, params=None):
        if action == "get_context":
            return {"email": "me@example.com", "timezone": "UTC"}
        raise BridgeError("UNREACHABLE", "x")
    monkeypatch.setattr(bridge, "call", boom)
    claude.script(response(tool_use("t1", "search_emails", {"query": "x"})), response(text("Google is unreachable.")))
    s = agent.new_session()
    r = agent.send_message(s, "search")
    assert r.activity[0]["ok"] is False
    assert "Couldn't reach Google" in claude.requests[1]["messages"][-1]["content"][0]["content"]


def test_claude_api_error_is_friendly_and_turn_can_be_retried(agent, claude):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    claude.script(anthropic.APIConnectionError(request=req))
    s = agent.new_session()
    r = agent.send_message(s, "hello")
    assert r.error == "Couldn't connect to Claude. Check your internet connection."

    claude.script(response(text("Hi again")))
    r2 = agent.send_message(s, "hello?")
    assert r2.reply == "Hi again"
    roles = [m["role"] for m in claude.requests[-1]["messages"]]
    assert roles == ["user"]  # merged, no two user turns in a row
    assert len(claude.requests[-1]["messages"][0]["content"]) == 2


def test_refusal_is_reported(agent, claude):
    claude.script(response(stop_reason="refusal"))
    s = agent.new_session()
    r = agent.send_message(s, "something")
    assert r.error and "declined" in r.error
    assert s.messages[-1]["role"] == "assistant"


def test_step_limit(agent, claude, settings):
    claude.script(*[response(tool_use(f"t{i}", "list_labels", {})) for i in range(settings.max_tool_rounds)])
    s = agent.new_session()
    r = agent.send_message(s, "loop")
    assert "too many steps" in r.reply
    assert s.messages[-1]["role"] == "assistant"


def test_fallbacks_can_be_disabled(claude, bridge):
    from app.agent import EmailAgent
    from app.config import Settings
    a = EmailAgent(claude, bridge, Settings(anthropic_api_key="k", enable_fallbacks=False))
    claude.script(response(text("ok")))
    a.send_message(a.new_session(), "hi")
    assert "fallbacks" not in claude.requests[0] and "betas" not in claude.requests[0]


def test_missing_api_key_message():
    from app.errors import friendly_error
    err = TypeError('"Could not resolve authentication method. Expected one of api_key, auth_token..."')
    assert "ANTHROPIC_API_KEY" in friendly_error(err)
