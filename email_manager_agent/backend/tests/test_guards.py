from __future__ import annotations

from typing import Any

import pytest

import run
from tests.conftest import make_settings


@pytest.mark.parametrize("base_url", ["http://localhost", "http://localhost:8000", "http://127.0.0.1:8000"])
def test_local_hosts_allowed(make_client: Any, base_url: str) -> None:
    assert make_client(base_url=base_url).get("/api/status").status_code == 200


@pytest.mark.parametrize("base_url", ["http://evil.example.com", "http://192.168.1.10:8000", "http://localhost.evil.com"])
def test_other_host_headers_rejected(make_client: Any, base_url: str) -> None:
    r = make_client(base_url=base_url).get("/api/status")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_host"
    assert "Invalid host header" in r.json()["error"]["message"]


def test_remote_client_rejected(make_client: Any) -> None:
    r = make_client(client_ip="10.0.0.7").post("/api/reset", json={})
    assert r.status_code == 403 and r.json()["error"]["code"] == "forbidden"


def test_cors_allows_only_local_frontend(client: Any) -> None:
    ok = client.options("/api/prompt", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("access-control-allow-origin") == "http://localhost:5173"
    bad = client.options("/api/prompt", headers={"Origin": "http://evil.com", "Access-Control-Request-Method": "POST"})
    assert "access-control-allow-origin" not in bad.headers


def test_unknown_route_uses_error_shape(client: Any) -> None:
    r = client.get("/api/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"


def test_unexpected_exception_is_internal_error(make_client: Any, llm: Any) -> None:
    llm.push(RuntimeError("kaboom"))
    c = make_client(raise_server_exceptions=False)
    r = c.post("/api/prompt", json={"prompt": "hi"})
    assert r.status_code == 500
    assert r.json() == {"error": {"code": "internal_error", "message": "Something went wrong on our side. Please try again."}}


def test_run_refuses_non_loopback_host(capsys: Any) -> None:
    served = []
    code = run.main(make_settings(backend_host="0.0.0.0"), serve=lambda *a, **k: served.append(k))
    assert code == 2 and not served
    assert "local only" in capsys.readouterr().err


def test_run_serves_on_loopback() -> None:
    served: list[dict[str, Any]] = []
    code = run.main(make_settings(backend_host="127.0.0.1", backend_port=8123), serve=lambda app, **k: served.append(k))
    assert code == 0 and served[0]["host"] == "127.0.0.1" and served[0]["port"] == 8123


def test_run_reports_config_errors(monkeypatch: Any, capsys: Any) -> None:
    monkeypatch.setenv("MAX_AGENT_STEPS", "lots")
    assert run.main(serve=lambda *a, **k: None) == 2
    assert "MAX_AGENT_STEPS" in capsys.readouterr().err
