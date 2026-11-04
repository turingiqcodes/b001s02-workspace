from __future__ import annotations

import json
from typing import Any, Callable

import httpx
import pytest

from app.errors import BridgeError
from app.services.bridge import NOT_JSON, AppsScriptBridge

URL = "https://script.google.com/macros/s/abc/exec"


def bridge(handler: Callable[[httpx.Request], httpx.Response]) -> AppsScriptBridge:
    return AppsScriptBridge(URL, "the-secret-value-123", timeout=30, transport=httpx.MockTransport(handler))


def test_sends_secret_action_params_and_returns_data() -> None:
    seen: list[dict[str, Any]] = []

    def h(req: httpx.Request) -> httpx.Response:
        seen.append(json.loads(req.content))
        return httpx.Response(200, json={"ok": True, "data": {"labels": []}})

    assert bridge(h).call("list_labels", {"x": 1}) == {"labels": []}
    assert seen == [{"secret": "the-secret-value-123", "action": "list_labels", "params": {"x": 1}}]


def test_follows_apps_script_redirect() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        if req.url.host == "script.google.com":
            return httpx.Response(302, headers={"Location": "https://script.googleusercontent.com/macros/echo?x=1"})
        assert req.method == "GET"
        return httpx.Response(200, json={"ok": True, "data": 42})

    assert bridge(h).call("ping") == 42


def test_non_json_response() -> None:
    with pytest.raises(BridgeError) as e:
        bridge(lambda r: httpx.Response(200, text="<html>Sign in</html>")).call("ping")
    assert e.value.message == NOT_JSON and e.value.code == "bridge_error" and e.value.status == 502


def test_unexpected_shape() -> None:
    with pytest.raises(BridgeError, match="unexpected response"):
        bridge(lambda r: httpx.Response(200, json=[1, 2])).call("ping")


@pytest.mark.parametrize("code,match", [
    ("unauthorized", "Invalid shared secret"),
    ("not_configured", "checkSetup"),
    ("permission_denied", "Re-authorize"),
    ("quota", "usage limit"),
    ("not_found", "Google reported an error: No such message"),
    ("invalid_params", "Google reported an error: No such message"),
])
def test_error_codes(code: str, match: str) -> None:
    body = {"ok": False, "error": {"code": code, "message": "No such message"}}
    with pytest.raises(BridgeError, match=match) as e:
        bridge(lambda r: httpx.Response(200, json=body)).call("read_email", {"message_id": "x"})
    assert e.value.bridge_code == code


def test_timeout_maps_to_bridge_timeout() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    with pytest.raises(BridgeError, match="did not answer within 30 seconds") as e:
        bridge(h).call("ping")
    assert e.value.code == "bridge_timeout" and e.value.status == 504


def test_unreachable() -> None:
    def h(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("dns")

    with pytest.raises(BridgeError, match="Could not reach") as e:
        bridge(h).call("ping")
    assert e.value.bridge_code == "unreachable"


@pytest.mark.parametrize("status,match", [(401, "Anyone"), (403, "Anyone"), (404, "ending in /exec"), (500, "HTTP 500")])
def test_http_errors(status: int, match: str) -> None:
    with pytest.raises(BridgeError, match=match):
        bridge(lambda r: httpx.Response(status, text="x")).call("ping")
