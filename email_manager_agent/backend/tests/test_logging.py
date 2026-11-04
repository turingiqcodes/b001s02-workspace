"""Logs record tool and operation names, never email content or secrets."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.services.bridge import AppsScriptBridge
from tests.conftest import SECRET, FakeLLM, call, say


def test_flow_logs_no_content(client: Any, llm: FakeLLM, caplog: Any) -> None:
    caplog.set_level(logging.DEBUG)
    llm.push(call("read_email", message_id="m1"),
             call("send_email", to=["private@example.com"], subject="Top secret subject", body="BODY-NEVER-LOGGED"),
             say("Waiting."))
    out = client.post("/api/prompt", json={"prompt": "PROMPT-NEVER-LOGGED"}).json()
    client.post("/api/confirm", json={"session_id": out["session_id"], "decision": "approve",
                                      "operation_id": out["pending_operations"][0]["operation_id"]})
    text = caplog.text
    assert "read_email" in text and "send_email" in text
    for secret in ("SECRET-BODY-TEXT", "BODY-NEVER-LOGGED", "PROMPT-NEVER-LOGGED", "Top secret subject",
                   "private@example.com", SECRET):
        assert secret not in text


def test_bridge_client_never_logs_secret(caplog: Any) -> None:
    caplog.set_level(logging.DEBUG)
    b = AppsScriptBridge("https://script.google.com/macros/s/abc/exec", SECRET,
                         transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": False, "error": {
                             "code": "unauthorized", "message": "Invalid shared secret"}})))
    try:
        b.call("ping", {"q": "PARAM-NEVER-LOGGED"})
    except Exception:
        pass
    assert SECRET not in caplog.text and "PARAM-NEVER-LOGGED" not in caplog.text


def test_secret_not_in_status(client: Any) -> None:
    assert SECRET not in client.get("/api/status?check_bridge=true").text
