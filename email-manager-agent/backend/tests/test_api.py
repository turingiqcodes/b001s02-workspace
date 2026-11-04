from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from app.bridge import AppsScriptBridge, BridgeError
from app.errors import friendly_error
from app.main import create_app
from tests.conftest import response, text, tool_use


@pytest.fixture
def api(agent, settings):
    return TestClient(create_app(settings=settings, agent=agent))


def test_health(api):
    body = api.get("/api/health").json()
    assert body["status"] == "ok" and body["bridge_mode"] == "mock"


def test_full_approval_flow_over_http(api, claude, bridge):
    sid = api.post("/api/sessions").json()["session_id"]
    claude.script(response(tool_use("t1", "create_label", {"name": "Travel"})))
    r = api.post(f"/api/sessions/{sid}/messages", json={"text": "make a Travel label"}).json()
    assert r["state"] == "awaiting_approval" and r["pending"][0]["action_id"] == "t1"

    assert api.post(f"/api/sessions/{sid}/messages", json={"text": "hi"}).status_code == 409

    claude.script(response(text("Created the Travel label.")))
    r2 = api.post(f"/api/sessions/{sid}/decisions", json={"decisions": [{"action_id": "t1", "approve": True}]}).json()
    assert r2["state"] == "idle" and r2["executed"][0]["status"] == "done"
    assert any(lbl["name"] == "Travel" for lbl in bridge.labels.values())


def test_unknown_session_and_bad_input(api):
    assert api.post("/api/sessions/nope/messages", json={"text": "x"}).status_code == 404
    sid = api.post("/api/sessions").json()["session_id"]
    r = api.post(f"/api/sessions/{sid}/messages", json={"text": ""})
    assert r.status_code == 422 and "malformed" in r.json()["detail"]
    assert api.post(f"/api/sessions/{sid}/decisions", json={"decisions": [{"action_id": "x", "approve": True}]}).status_code == 409


def _bridge(handler):
    return AppsScriptBridge("https://script.google.com/macros/s/x/exec", "secret", transport=httpx.MockTransport(handler))


def test_apps_script_bridge_success_and_token():
    seen = {}

    def handler(request):
        import json
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "data": {"labels": []}})

    assert _bridge(handler).call("list_labels") == {"labels": []}
    assert seen == {"token": "secret", "action": "list_labels", "params": {}}


def test_apps_script_bridge_errors():
    err = _bridge(lambda r: httpx.Response(200, json={"ok": False, "error": {"code": "UNAUTHORIZED", "message": "bad"}}))
    with pytest.raises(BridgeError) as exc:
        err.call("list_labels")
    assert exc.value.code == "UNAUTHORIZED"
    assert "BRIDGE_TOKEN" in friendly_error(exc.value)

    html = _bridge(lambda r: httpx.Response(200, text="<html>Sign in</html>"))
    with pytest.raises(BridgeError) as exc:
        html.call("list_labels")
    assert "Anyone" in exc.value.message

    def down(request):
        raise httpx.ConnectError("no route")
    with pytest.raises(BridgeError) as exc:
        _bridge(down).call("list_labels")
    assert exc.value.code == "UNREACHABLE"
