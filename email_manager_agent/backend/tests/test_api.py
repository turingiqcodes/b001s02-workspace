"""HTTP API: endpoints, approval lifecycle and every error code."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from app.errors import BridgeError, ModelError
from tests.conftest import FakeBridge, FakeLLM, call, calls, make_settings, say


def propose(client: Any, llm: FakeLLM, *responses: Any, session_id: str | None = None) -> dict[str, Any]:
    llm.push(*responses, say("Waiting for your approval."))
    body: dict[str, Any] = {"prompt": "please"}
    if session_id:
        body["session_id"] = session_id
    r = client.post("/api/prompt", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def confirm(client: Any, sid: str, op: str, decision: str = "approve", ack: bool | None = None) -> Any:
    body: dict[str, Any] = {"session_id": sid, "operation_id": op, "decision": decision}
    if ack is not None:
        body["acknowledge_warnings"] = ack
    return client.post("/api/confirm", json=body)


def err(r: Any) -> tuple[int, str]:
    return r.status_code, r.json()["error"]["code"]


# ------------------------------------------------------------------ status
def test_status_configured(client: Any) -> None:
    body = client.get("/api/status").json()
    assert body["llm"] == {"provider": "ollama", "model": "qwen3:8b", "configured": True, "issues": [],
                           "reachable": None, "detail": None}
    assert body["bridge"]["configured"] is True and body["bridge"]["reachable"] is None
    assert body["timezone"] == "UTC" and body["limits"]["max_agent_steps"] == 8
    assert body["session_id"] is None and body["pending_operations"] == []


def test_status_reports_issues(make_client: Any) -> None:
    s = make_settings(ollama_model=None, apps_script_shared_secret=None)
    body = make_client(s, use_llm=False, use_bridge=False).get("/api/status").json()
    assert body["llm"]["configured"] is False and "OLLAMA_MODEL" in body["llm"]["issues"][0]
    assert body["bridge"]["configured"] is False and "APPS_SCRIPT_SHARED_SECRET" in body["bridge"]["issues"][0]


def test_status_check_bridge_pings(client: Any, llm: FakeLLM, bridge: FakeBridge) -> None:
    llm.ping_result = (False, "Ollama model 'fake' is not installed. Run: ollama pull fake")
    body = client.get("/api/status?check_bridge=true").json()
    assert body["llm"]["reachable"] is False and "ollama pull" in body["llm"]["detail"]
    assert body["bridge"]["reachable"] is True and ("ping", {}) in bridge.calls


def test_status_check_bridge_failure(client: Any, bridge: FakeBridge) -> None:
    bridge.fail("ping", "unauthorized", "Invalid shared secret.")
    body = client.get("/api/status?check_bridge=true").json()
    assert body["bridge"] == {"configured": True, "issues": [], "reachable": False, "detail": "Invalid shared secret."}


def test_status_check_when_not_configured(make_client: Any) -> None:
    body = make_client(make_settings(ollama_model=None, apps_script_url=None), use_llm=False, use_bridge=False) \
        .get("/api/status?check_bridge=true").json()
    assert body["llm"]["reachable"] is False and body["bridge"]["reachable"] is False


def test_status_lists_session_pending(client: Any, llm: FakeLLM) -> None:
    out = propose(client, llm, call("create_label", name="Bills"))
    body = client.get(f"/api/status?session_id={out['session_id']}").json()
    assert body["session_id"] == out["session_id"]
    assert [p["operation_id"] for p in body["pending_operations"]] == [out["pending_operations"][0]["operation_id"]]


# ------------------------------------------------------------------ prompt
def test_prompt_creates_session(client: Any, llm: FakeLLM) -> None:
    llm.push(say("Hi there"))
    body = client.post("/api/prompt", json={"prompt": "  hello  "}).json()
    assert body["reply"] == "Hi there" and body["session_id"] and body["steps"] == 1
    assert body["stop_reason"] == "completed" and body["tool_calls"] == [] and body["pending_operations"] == []
    assert llm.calls[0]["messages"][0]["content"].endswith("\n\nhello")


def test_prompt_continues_session(client: Any, llm: FakeLLM) -> None:
    llm.push(say("one"), say("two"))
    sid = client.post("/api/prompt", json={"prompt": "a"}).json()["session_id"]
    assert client.post("/api/prompt", json={"prompt": "b", "session_id": sid}).json()["session_id"] == sid
    assert len(llm.calls[1]["messages"]) == 3


@pytest.mark.parametrize("body", [{}, {"prompt": 5}, {"prompt": "   "}, {"prompt": "x" * 4001},
                                  {"prompt": "hi", "extra": 1}])
def test_prompt_validation_error(client: Any, body: dict[str, Any]) -> None:
    assert err(client.post("/api/prompt", json=body)) == (422, "validation_error")


def test_prompt_malformed_json(client: Any) -> None:
    r = client.post("/api/prompt", content=b"{not json", headers={"Content-Type": "application/json"})
    assert err(r) == (422, "validation_error")


def test_prompt_unknown_session(client: Any) -> None:
    r = client.post("/api/prompt", json={"prompt": "hi", "session_id": "gone"})
    assert err(r) == (404, "not_found") and "session" in r.json()["error"]["message"]


def test_prompt_configuration_error(make_client: Any) -> None:
    c = make_client(make_settings(ollama_model=None), use_llm=False)
    r = c.post("/api/prompt", json={"prompt": "hi"})
    assert err(r) == (503, "configuration_error") and "OLLAMA_MODEL" in r.json()["error"]["message"]
    c2 = make_client(make_settings(apps_script_url=None), use_bridge=False)
    assert err(c2.post("/api/prompt", json={"prompt": "hi"})) == (503, "configuration_error")


@pytest.mark.parametrize("status", [502, 429, 504])
def test_prompt_model_errors(client: Any, llm: FakeLLM, status: int) -> None:
    llm.push(ModelError("model trouble", status=status))
    r = client.post("/api/prompt", json={"prompt": "hi"})
    assert err(r) == (status, "model_error") and r.json()["error"]["message"] == "model trouble"


def test_prompt_session_busy(client: Any, llm: FakeLLM) -> None:
    llm.push(say("first"))
    sid = client.post("/api/prompt", json={"prompt": "a"}).json()["session_id"]
    session = client.app.state.deps.store.get(sid)
    session.lock.acquire()
    try:
        r = client.post("/api/prompt", json={"prompt": "b", "session_id": sid})
        assert err(r) == (409, "session_busy")
        assert err(confirm(client, sid, "op_x")) == (409, "session_busy")
    finally:
        session.lock.release()


# ----------------------------------------------------------------- confirm
def test_approve_runs_stored_params_exactly_once(client: Any, llm: FakeLLM, bridge: FakeBridge) -> None:
    out = propose(client, llm, call("send_email", to=["a@example.com"], subject="Hi", body="Hello"))
    sid, op = out["session_id"], out["pending_operations"][0]["operation_id"]
    r = confirm(client, sid, op)
    assert r.status_code == 200
    assert r.json() == {"status": "executed", "message": "Email sent.", "result": {"message_id": "sent1", "thread_id": "t9"},
                        "pending_operations": []}
    assert [c for c in bridge.calls if c[0] == "send_email"] == [
        ("send_email", {"to": ["a@example.com"], "subject": "Hi", "body": "Hello"})]
    again = confirm(client, sid, op)
    assert err(again) == (409, "approval_error")
    assert len([c for c in bridge.calls if c[0] == "send_email"]) == 1


def test_reject(client: Any, llm: FakeLLM, bridge: FakeBridge) -> None:
    out = propose(client, llm, call("create_label", name="Bills"))
    sid, op = out["session_id"], out["pending_operations"][0]["operation_id"]
    r = confirm(client, sid, op, "reject")
    assert r.json()["status"] == "rejected" and r.json()["message"] == "Cancelled. Nothing was changed."
    assert "create_label" not in bridge.actions()
    assert err(confirm(client, sid, op)) == (409, "approval_error")


def test_outcome_reported_to_model_next_turn(client: Any, llm: FakeLLM) -> None:
    out = propose(client, llm, call("create_label", name="Bills"))
    sid, op = out["session_id"], out["pending_operations"][0]["operation_id"]
    confirm(client, sid, op, "reject")
    llm.push(say("ok"))
    client.post("/api/prompt", json={"prompt": "next", "session_id": sid})
    last_user = [m for m in llm.calls[-1]["messages"] if m["role"] == "user"][-1]["content"]
    assert f"The user rejected operation {op}" in last_user


def test_confirm_unknown_session_and_operation(client: Any, llm: FakeLLM) -> None:
    assert err(confirm(client, "nope", "op_1")) == (404, "not_found")
    sid = propose(client, llm, call("create_label", name="X"))["session_id"]
    assert err(confirm(client, sid, "op_unknown")) == (404, "not_found")


def test_confirm_validation(client: Any) -> None:
    assert err(client.post("/api/confirm", json={"session_id": "s", "operation_id": "o", "decision": "maybe"})) == \
        (422, "validation_error")


def test_approval_expires(client: Any, llm: FakeLLM, clock: Any, bridge: FakeBridge) -> None:
    out = propose(client, llm, call("create_label", name="Bills"))
    sid, op = out["session_id"], out["pending_operations"][0]["operation_id"]
    clock.advance(601)
    assert err(confirm(client, sid, op)) == (410, "approval_expired")
    assert "create_label" not in bridge.actions()
    assert client.get(f"/api/status?session_id={sid}").json()["pending_operations"] == []
    assert err(confirm(client, sid, op)) == (409, "approval_error")


def test_expired_ops_dropped_from_status(client: Any, llm: FakeLLM, clock: Any) -> None:
    sid = propose(client, llm, call("create_label", name="Bills"))["session_id"]
    clock.advance(700)
    assert client.get(f"/api/status?session_id={sid}").json()["pending_operations"] == []


def test_approvals_are_session_bound(client: Any, llm: FakeLLM) -> None:
    out = propose(client, llm, call("create_label", name="Bills"))
    other = propose(client, llm, call("create_label", name="Other"))
    assert err(confirm(client, other["session_id"], out["pending_operations"][0]["operation_id"])) == (404, "not_found")


def test_warnings_require_acknowledgement(client: Any, llm: FakeLLM, bridge: FakeBridge) -> None:
    out = propose(client, llm, call("cancel_event", event_id="ev1"))
    op = out["pending_operations"][0]
    assert op["warnings"] == ["This cancels the event for everyone invited."] and op["requires_acknowledgement"]
    sid = out["session_id"]
    assert err(confirm(client, sid, op["operation_id"])) == (409, "acknowledgement_required")
    assert err(confirm(client, sid, op["operation_id"], ack=False)) == (409, "acknowledgement_required")
    r = confirm(client, sid, op["operation_id"], ack=True)
    assert r.json()["status"] == "executed" and r.json()["message"] == "Event cancelled."
    assert ("cancel_event", {"event_id": "ev1", "notify_attendees": True}) in bridge.calls


def test_bridge_failure_on_approve(client: Any, llm: FakeLLM, bridge: FakeBridge) -> None:
    out = propose(client, llm, call("create_label", name="Bills"))
    bridge.fail("create_label", "invalid_params", "Google reported an error: label exists")
    sid, op = out["session_id"], out["pending_operations"][0]["operation_id"]
    r = confirm(client, sid, op)
    assert r.status_code == 200 and r.json()["status"] == "failed" and "label exists" in r.json()["message"]
    assert err(confirm(client, sid, op)) == (409, "approval_error")


def test_bridge_timeout_on_approve_says_check_first(client: Any, llm: FakeLLM, bridge: FakeBridge) -> None:
    out = propose(client, llm, call("send_email", to=["a@example.com"], subject="s", body="b"))
    bridge.fail("send_email", "timeout", "Google did not answer within 30 seconds.")
    r = confirm(client, out["session_id"], out["pending_operations"][0]["operation_id"])
    assert r.json()["status"] == "failed" and "may have gone through" in r.json()["message"]


def test_create_event_conflict_and_working_hours(client: Any, llm: FakeLLM) -> None:
    out = propose(client, llm, call("create_event", title="Sync", start="2026-11-02T11:15:00Z", end="2026-11-02T11:45:00Z"))
    warnings = out["pending_operations"][0]["warnings"]
    assert any('Conflicts with "Standup"' in w for w in warnings)
    out2 = propose(client, llm, call("create_event", title="Late", start="2026-11-02T19:00:00Z", end="2026-11-02T19:30:00Z"))
    assert any("working hours" in w for w in out2["pending_operations"][0]["warnings"])
    out3 = propose(client, llm, call("create_event", title="Weekend", start="2026-11-07T10:00:00Z", end="2026-11-07T10:30:00Z"))
    assert any("Saturday" in w for w in out3["pending_operations"][0]["warnings"])


def test_conflict_rechecked_before_running(client: Any, llm: FakeLLM, bridge: FakeBridge) -> None:
    out = propose(client, llm, call("create_event", title="Sync", start="2026-11-02T14:00:00Z", end="2026-11-02T14:30:00Z",
                                    attendees=["bob@example.com"]))
    op = out["pending_operations"][0]
    assert op["warnings"] == []
    bridge.events.append({"id": "new", "title": "Surprise", "start": "2026-11-02T14:00:00+00:00",
                          "end": "2026-11-02T15:00:00+00:00", "all_day": False})
    r = confirm(client, out["session_id"], op["operation_id"])
    assert err(r) == (409, "acknowledgement_required") and "Surprise" in r.json()["error"]["message"]
    assert "create_event" not in bridge.actions()
    status = client.get(f"/api/status?session_id={out['session_id']}").json()
    assert any("Surprise" in w for w in status["pending_operations"][0]["warnings"])
    assert status["pending_operations"][0]["requires_acknowledgement"] is True
    r2 = confirm(client, out["session_id"], op["operation_id"], ack=True)
    assert r2.json()["status"] == "executed"


def test_move_event_keeps_duration(client: Any, llm: FakeLLM, bridge: FakeBridge) -> None:
    out = propose(client, llm, call("move_event", event_id="ev2", new_start="2026-11-04T16:00:00Z"))
    op = out["pending_operations"][0]
    assert any(d["label"] == "To" for d in op["details"])
    confirm(client, out["session_id"], op["operation_id"], ack=bool(op["warnings"]))
    assert ("move_event", {"event_id": "ev2", "new_start": "2026-11-04T16:00:00+00:00",
                           "new_end": "2026-11-04T17:00:00+00:00"}) in bridge.calls


def test_move_event_ignores_its_own_slot(client: Any, llm: FakeLLM) -> None:
    out = propose(client, llm, call("move_event", event_id="ev2", new_start="2026-11-03T15:30:00Z"))
    assert not any("Conflicts" in w for w in out["pending_operations"][0]["warnings"])


def test_move_unknown_event_is_invalid(client: Any, llm: FakeLLM) -> None:
    out = propose(client, llm, call("move_event", event_id="nope", new_start="2026-11-04T16:00:00Z"))
    assert out["pending_operations"] == [] and out["tool_calls"][0]["status"] == "invalid"


def test_repeat_warning(client: Any, llm: FakeLLM) -> None:
    args = {"to": ["a@example.com"], "subject": "Hi", "body": "Same"}
    out = propose(client, llm, call("send_email", **args))
    sid = out["session_id"]
    confirm(client, sid, out["pending_operations"][0]["operation_id"])
    again = propose(client, llm, call("send_email", **args), session_id=sid)
    assert "You already did exactly this earlier in this conversation." in again["pending_operations"][0]["warnings"]


def test_many_recipients_warning(client: Any, llm: FakeLLM) -> None:
    to = [f"p{i}@example.com" for i in range(6)]
    out = propose(client, llm, call("send_email", to=to, subject="s", body="b"))
    assert "This goes to 6 recipients." in out["pending_operations"][0]["warnings"]


def test_save_attachments_skips_large(client: Any, llm: FakeLLM, bridge: FakeBridge) -> None:
    out = propose(client, llm, call("save_attachments_to_drive", message_id="m1"))
    op = out["pending_operations"][0]
    assert any("huge.zip" in w and "20 MB" in w for w in op["warnings"])
    r = confirm(client, out["session_id"], op["operation_id"], ack=True)
    assert r.json()["status"] == "executed"
    assert ("save_attachments_to_drive", {"message_id": "m1", "attachment_ids": ["a1"],
                                          "folder_name": "Email Attachments"}) in bridge.calls


def test_save_attachments_none_left(client: Any, llm: FakeLLM) -> None:
    out = propose(client, llm, call("save_attachments_to_drive", message_id="m1", attachment_ids=["a2"]))
    assert out["pending_operations"] == [] and "larger than" in out["tool_calls"][0]["summary"]
    out = propose(client, llm, call("save_attachments_to_drive", message_id="m1", attachment_ids=["zz"]))
    assert out["pending_operations"] == [] and "not on that email" in out["tool_calls"][0]["summary"]


def test_forbidden_labels_rejected(client: Any, llm: FakeLLM) -> None:
    out = propose(client, llm, call("modify_labels", message_ids=["m1"], add_labels=["TRASH"]))
    assert out["pending_operations"] == [] and out["tool_calls"][0]["status"] == "invalid"
    ok = propose(client, llm, call("modify_labels", message_ids=["m1"], remove_labels=["INBOX", "UNREAD"]))
    assert ok["pending_operations"][0]["summary"] == "Remove INBOX, UNREAD on 1 email"


@pytest.mark.parametrize("tool,args,title", [
    ("reply_to_email", {"message_id": "m1", "body": "Thanks!"}, "Reply to email"),
    ("forward_email", {"message_id": "m1", "to": ["b@example.com"]}, "Forward email"),
    ("create_draft", {"to": ["b@example.com"], "subject": "s", "body": "b", "reply_to_message_id": "m1"}, "Create draft"),
    ("update_draft", {"draft_id": "d1", "subject": "New"}, "Update draft"),
    ("send_draft", {"draft_id": "d1"}, "Send draft"),
    ("create_drive_folder", {"name": "Invoices"}, "Create Drive folder"),
    ("move_drive_file", {"file_id": "f1", "folder_id": "fold"}, "Move Drive file"),
])
def test_every_write_tool_previews_and_executes(client: Any, llm: FakeLLM, bridge: FakeBridge, tool: str,
                                                args: dict[str, Any], title: str) -> None:
    out = propose(client, llm, call(tool, **args))
    op = out["pending_operations"][0]
    assert op["title"] == title and op["summary"] and op["details"]
    assert tool not in bridge.actions()
    r = confirm(client, out["session_id"], op["operation_id"], ack=bool(op["warnings"]))
    assert r.json()["status"] == "executed"
    assert bridge.actions().count(tool) == 1


def test_preview_lookup_failure_is_not_fatal(client: Any, llm: FakeLLM, bridge: FakeBridge) -> None:
    bridge.fail("read_email", "not_found")
    out = propose(client, llm, call("reply_to_email", message_id="m1", body="hi"))
    assert out["pending_operations"][0]["details"][0] == {"label": "Email", "value": "m1"}


def test_multiple_pending_ops(client: Any, llm: FakeLLM) -> None:
    out = propose(client, llm, calls(("create_label", {"name": "A"}), ("create_label", {"name": "B"})))
    sid = out["session_id"]
    ops = out["pending_operations"]
    assert len(ops) == 2
    r = confirm(client, sid, ops[0]["operation_id"])
    assert [p["operation_id"] for p in r.json()["pending_operations"]] == [ops[1]["operation_id"]]


# ------------------------------------------------------------------- reset
def test_reset_drops_session_and_pending(client: Any, llm: FakeLLM) -> None:
    out = propose(client, llm, call("create_label", name="X"))
    r = client.post("/api/reset", json={"session_id": out["session_id"]})
    assert r.status_code == 200 and r.json()["status"] == "reset" and r.json()["session_id"] != out["session_id"]
    assert err(confirm(client, out["session_id"], out["pending_operations"][0]["operation_id"])) == (404, "not_found")


def test_reset_without_body(client: Any) -> None:
    r = client.post("/api/reset")
    assert r.status_code == 200 and r.json()["status"] == "reset"


def test_concurrent_confirms_run_once(client: Any, llm: FakeLLM, bridge: FakeBridge) -> None:
    out = propose(client, llm, call("send_email", to=["a@example.com"], subject="s", body="b"))
    sid, op = out["session_id"], out["pending_operations"][0]["operation_id"]
    results: list[int] = []

    def go() -> None:
        results.append(confirm(client, sid, op).status_code)

    threads = [threading.Thread(target=go) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert bridge.actions().count("send_email") == 1
    assert results.count(200) == 1
