"""API endpoints and read workflow (API-01..04, AT-01..06, AT-15..17, AT-28)."""

import json

from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient

from tests.conftest import FakeBridge, ScriptedLLM, text, tool_use


def tool_results(llm: ScriptedLLM, request_index: int) -> list[dict]:
    return [b for b in llm.requests[request_index][-1]["content"] if b.get("type") == "tool_result"]


# ---------- status ----------

def test_status_ready_without_exposing_secrets(client, settings):
    resp = client.get("/api/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "llm_configured": True,
        "llm_model": "openai/gpt-oss-120b",
        "bridge_configured": True,
        "gmail_connected": True,
        "gmail_address": "me@example.com",
        "time_zone": "Asia/Kolkata",
        "status": "ready",
        "detail": None,
    }
    raw = resp.text
    assert settings.groq_api_key not in raw
    assert settings.apps_script_secret not in raw


def test_status_not_configured():
    app = create_app(settings=Settings(), bridge=FakeBridge(), llm=ScriptedLLM())
    body = TestClient(app).get("/api/status").json()
    assert body["status"] == "not_configured"
    assert body["llm_configured"] is False and body["bridge_configured"] is False
    assert "GROQ_API_KEY" in body["detail"]


def test_status_reports_bridge_error(client, bridge):
    from app.errors import BridgeError

    bridge.fail["get_profile"] = BridgeError("Invalid secret.", code="unauthorized")
    body = client.get("/api/status").json()
    assert body["status"] == "bridge_error"
    assert body["gmail_connected"] is False
    assert body["detail"] == "Invalid secret."


# ---------- prompt / read flow ----------

def test_prompt_read_flow_runs_without_approval(client, llm, bridge):
    llm.then(tool_use("get_latest_emails", {"max_results": 2}, id="t1")).then(text("You have 2 new emails."))
    resp = client.post("/api/prompt", json={"prompt": "Find my latest emails"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["response"] == "You have 2 new emails."
    assert body["pending_actions"] == []
    assert body["session_id"].startswith("session_")
    assert ("get_latest_emails", {"max_results": 2}) in bridge.calls
    result = json.loads(tool_results(llm, 1)[0]["content"])
    assert [e["id"] for e in result["emails"]] == ["m1", "m2"]


def test_session_context_is_kept_between_prompts(client, llm):
    llm.then(text("first answer")).then(text("second answer"))
    sid = client.post("/api/prompt", json={"prompt": "hello"}).json()["session_id"]
    resp = client.post("/api/prompt", json={"prompt": "reply to the second email", "session_id": sid})
    assert resp.json()["session_id"] == sid
    second_request = llm.requests[1]
    assert [m["role"] for m in second_request] == ["user", "assistant", "user"]
    assert second_request[1]["content"] == [text("first answer")]


def test_unknown_session_id_starts_new_session(client, llm):
    llm.then(text("hi"))
    body = client.post("/api/prompt", json={"prompt": "hi", "session_id": "session_gone"}).json()
    assert body["session_id"] != "session_gone"


def test_search_by_sender_builds_gmail_query(client, llm, bridge):
    llm.then(tool_use("search_emails", {"sender": "Priya", "has_attachment": True}, id="t1")).then(text("found"))
    client.post("/api/prompt", json={"prompt": "emails from Priya with attachments"})
    assert ("search_emails", {"query": "from:Priya has:attachment", "max_results": 10}) in bridge.calls


def test_parallel_reads_all_return_results_in_order(client, llm, bridge):
    llm.then(
        tool_use("read_email", {"message_id": "m1"}, id="a"),
        tool_use("read_thread", {"thread_id": "t2"}, id="b"),
        tool_use("list_drafts", {}, id="c"),
        tool_use("list_gmail_labels", {}, id="d"),
        tool_use("list_calendar_events", {"start": "2026-11-05T00:00:00+05:30", "end": "2026-11-06T00:00:00+05:30"}, id="e"),
        tool_use("check_availability", {"start": "2026-11-05T09:00:00+05:30", "end": "2026-11-05T18:00:00+05:30"}, id="f"),
        tool_use("list_drive_files", {}, id="g"),
    ).then(text("done"))
    client.post("/api/prompt", json={"prompt": "overview"})
    results = tool_results(llm, 1)
    assert [r["tool_use_id"] for r in results] == list("abcdefg")
    assert not any(r.get("is_error") for r in results), results
    assert json.loads(results[0]["content"])["body"] == "Body of email 1"
    assert json.loads(results[1]["content"])["messages"][0]["id"] == "m2"
    assert json.loads(results[2]["content"])["drafts"][0]["draft_id"] == "d1"
    assert json.loads(results[5]["content"])["is_free"] is True
    assert json.loads(results[6]["content"])["files"][0]["name"] == "notes.txt"
    assert bridge.write_calls() == []


def test_blank_prompt_is_rejected_readably(client):
    resp = client.post("/api/prompt", json={"prompt": "   "})
    assert resp.status_code == 422
    assert "prompt" in resp.json()["detail"]


def test_extra_fields_rejected(client):
    resp = client.post("/api/prompt", json={"prompt": "hi", "auto_approve": True})
    assert resp.status_code == 422


def test_prompt_without_groq_key_returns_503(bridge, llm):
    app = create_app(settings=Settings(apps_script_url="u", apps_script_secret="s"), bridge=bridge, llm=llm)
    resp = TestClient(app).post("/api/prompt", json={"prompt": "hi"})
    assert resp.status_code == 503
    assert "GROQ_API_KEY" in resp.json()["detail"]


def test_llm_error_is_readable(client, llm):
    from app.errors import LLMError

    async def boom(*a, **k):
        raise LLMError("Groq is rate-limiting requests right now.", status_code=429)

    llm.create = boom
    resp = client.post("/api/prompt", json={"prompt": "hi"})
    assert resp.status_code == 429
    assert resp.json()["detail"].startswith("Groq is rate-limiting")


def test_unexpected_error_does_not_crash_server(client, llm):
    async def boom(*a, **k):
        raise RuntimeError("kaboom")

    llm.create = boom
    client = TestClient(client.app, raise_server_exceptions=False)
    resp = client.post("/api/prompt", json={"prompt": "hi"})
    assert resp.status_code == 500
    assert "kaboom" not in resp.text
    assert client.get("/api/status").status_code == 200


# ---------- reset / confirm errors ----------

def test_reset_clears_session(client, llm):
    llm.then(text("a")).then(text("b"))
    sid = client.post("/api/prompt", json={"prompt": "x"}).json()["session_id"]
    assert client.post("/api/reset", json={"session_id": sid}).json() == {"status": "reset", "message": "Session cleared."}
    new_sid = client.post("/api/prompt", json={"prompt": "y", "session_id": sid}).json()["session_id"]
    assert new_sid != sid
    assert len(llm.requests[1]) == 1  # no old history


def test_reset_without_body(client):
    assert client.post("/api/reset").status_code == 200


def test_confirm_unknown_action_404(client):
    resp = client.post("/api/confirm", json={"action_id": "action_nope", "approved": True})
    assert resp.status_code == 404


def test_busy_session_returns_409(client, llm):
    llm.then(text("ok"))
    sid = client.post("/api/prompt", json={"prompt": "x"}).json()["session_id"]
    session = client.app.state.store.get(sid)

    import asyncio

    async def hold():
        await session.lock.acquire()

    asyncio.run(hold())
    try:
        resp = client.post("/api/prompt", json={"prompt": "again", "session_id": sid})
        assert resp.status_code == 409
        assert resp.json()["code"] == "session_busy"
    finally:
        session.lock.release()


def test_context_line_includes_time_and_account(client, llm):
    llm.then(text("ok"))
    client.post("/api/prompt", json={"prompt": "x"})
    blocks = llm.requests[0][0]["content"]
    assert blocks[0]["text"].startswith("[Context] Current time:")
    assert "Asia/Kolkata" in blocks[0]["text"] and "me@example.com" in blocks[0]["text"]
    assert blocks[-1]["text"] == "x"


def test_agent_stops_after_max_steps(bridge):
    llm = ScriptedLLM()
    for i in range(20):
        llm.then(tool_use("list_gmail_labels", {}, id=f"t{i}"))
    s = Settings(groq_api_key="k", apps_script_url="u", apps_script_secret="s", agent_max_steps=3)
    client = TestClient(create_app(settings=s, bridge=bridge, llm=llm))
    body = client.post("/api/prompt", json={"prompt": "loop"}).json()
    assert "stopped after 3 steps" in body["response"]
    assert len(llm.requests) == 3
    # The next prompt still produces a valid alternating history.
    llm.script = [[text("ok")]]
    client.post("/api/prompt", json={"prompt": "next", "session_id": body["session_id"]})
    roles = [m["role"] for m in llm.requests[-1]]
    assert all(a != b for a, b in zip(roles, roles[1:]))
