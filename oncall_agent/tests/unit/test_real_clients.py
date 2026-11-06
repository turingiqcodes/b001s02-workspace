"""Validate the real API clients' request/response handling with httpx.MockTransport
(no network). These use the same interfaces the mocks implement."""
import json
from datetime import datetime, timezone

import httpx
import httpx2
import pytest

from oncall_agent.integrations import (AirflowUnavailable, ClaudeUnavailable, IntegrationError, LinearUnavailable,
                                      LLMUnavailable)
from oncall_agent.integrations.airflow import RestAirflowClient
from oncall_agent.integrations.claude import AnthropicClaudeClient
from oncall_agent.integrations.github import RestGitHubClient
from oncall_agent.integrations.groq import GroqLLMClient
from oncall_agent.integrations.linear import GraphQLLinearClient
from oncall_agent.models import ANALYSIS_JSON_SCHEMA

from tests.conftest import RETRIABLE

T0 = datetime(2026, 11, 6, tzinfo=timezone.utc)


# ---- Airflow ----------------------------------------------------------------------------
def airflow_client(handler):
    return RestAirflowClient("http://af:8080", "op_user", "pw", transport=httpx.MockTransport(handler))


def test_airflow_auth_and_failed_task_listing():
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        if req.url.path == "/auth/token":
            assert json.loads(req.content) == {"username": "op_user", "password": "pw"}
            return httpx.Response(201, json={"access_token": "jwt-1"})
        assert req.headers["Authorization"] == "Bearer jwt-1"
        assert req.url.path == "/api/v2/dags/~/dagRuns/~/taskInstances"
        assert req.url.params["state"] == "failed" and req.url.params["end_date_gte"].startswith("2026-11-06")
        return httpx.Response(200, json={"total_entries": 1, "task_instances": [{
            "dag_id": "d", "dag_run_id": "r", "task_id": "t", "state": "failed", "try_number": 2, "map_index": -1,
            "end_date": "2026-11-06T01:00:00Z"}]})

    tis = airflow_client(handler).list_failed_task_instances(T0)
    assert tis[0].task_id == "t" and tis[0].try_number == 2 and tis[0].run_id == "r"


def test_airflow_token_refresh_on_401_and_5xx_is_unavailable():
    tokens = iter(["old", "new"])
    calls = {"n": 0}

    def handler(req):
        if req.url.path == "/auth/token":
            return httpx.Response(201, json={"access_token": next(tokens)})
        calls["n"] += 1
        if req.headers["Authorization"] == "Bearer old":
            return httpx.Response(401)
        return httpx.Response(200, json={"import_errors": [{"import_error_id": 1, "filename": "f.py",
                                                            "stack_trace": "SyntaxError", "timestamp": None}],
                                         "total_entries": 1})

    assert airflow_client(handler).list_import_errors()[0].filename == "f.py"
    assert calls["n"] == 2

    def down(req):
        if req.url.path == "/auth/token":
            return httpx.Response(201, json={"access_token": "x"})
        return httpx.Response(503)

    with pytest.raises(AirflowUnavailable):
        airflow_client(down).list_failed_dag_runs(T0)


def test_airflow_clear_request_is_failed_only_with_downstream():
    bodies = []

    def handler(req):
        if req.url.path == "/auth/token":
            return httpx.Response(201, json={"access_token": "x"})
        assert req.url.path == "/api/v2/dags/sales_etl/clearTaskInstances" and req.method == "POST"
        bodies.append(json.loads(req.content))
        return httpx.Response(200, json={"task_instances": [{"dag_id": "sales_etl", "dag_run_id": "r",
                                                             "task_id": "load", "state": "failed"}]})

    out = airflow_client(handler).clear_task_instances("sales_etl", "r", ["load"], dry_run=True)
    body = bodies[0]
    assert body["only_failed"] is True and body["include_downstream"] is True and body["include_upstream"] is False
    assert body["dry_run"] is True and body["dag_run_id"] == "r" and body["task_ids"] == ["load"]
    assert out[0].state == "failed"


def test_airflow_structured_logs_are_flattened():
    def handler(req):
        if req.url.path == "/auth/token":
            return httpx.Response(201, json={"access_token": "x"})
        assert req.url.path.endswith("/taskInstances/t/logs/1")
        return httpx.Response(200, json={"content": [{"timestamp": "2026-11-06T00:00:00Z", "level": "error",
                                                      "event": "Boom: connection timed out"}]})

    assert "Boom: connection timed out" in airflow_client(handler).get_task_log("d", "r", "t", 1)


# ---- GitHub -----------------------------------------------------------------------------
def test_github_commit_parsing():
    def handler(req):
        assert req.headers["Authorization"] == "Bearer ghtoken"
        if req.url.path == "/repos/o/r/commits":
            assert req.url.params["path"] == "dags/x.py"
            return httpx.Response(200, json=[{"sha": "abc", "author": {"login": "dev1"},
                                              "commit": {"message": "fix", "author": {"name": "Dev", "date": "2026-11-05T00:00:00Z"}}}])
        return httpx.Response(200, json={"sha": "abc", "author": {"login": "dev1"},
                                         "commit": {"message": "fix", "author": {"name": "Dev", "date": "2026-11-05T00:00:00Z"}},
                                         "files": [{"filename": "dags/x.py", "patch": "+new line"}]})

    gh = RestGitHubClient("ghtoken", transport=httpx.MockTransport(handler))
    c = gh.list_commits("o/r", "dags/x.py")[0]
    assert c.author_login == "dev1" and c.timestamp.year == 2026
    full = gh.get_commit("o/r", "abc")
    assert full.files == ["dags/x.py"] and "+new line" in full.patch


# ---- Linear -----------------------------------------------------------------------------
def linear_client(handler):
    return GraphQLLinearClient("lin_api_key", "team-1", transport=httpx.MockTransport(handler))


def test_linear_create_issue_and_label_history():
    def handler(req):
        assert req.headers["Authorization"] == "lin_api_key"
        q = json.loads(req.content)
        if "issueCreate" in q["query"]:
            inp = q["variables"]["input"]
            assert inp["teamId"] == "team-1" and inp["labelIds"] == ["L1"] and inp["priority"] == 2
            return httpx.Response(200, json={"data": {"issueCreate": {"success": True, "issue": {
                "id": "i1", "identifier": "ENG-1", "title": inp["title"], "description": inp["description"],
                "url": "u", "priority": 2, "state": {"type": "triage"}, "labels": {"nodes": [{"name": "oncall:needs-human"}]},
                "assignee": {"id": inp["assigneeId"]}}}}})
        return httpx.Response(200, json={"data": {"issue": {"history": {"nodes": [
            {"id": "h1", "createdAt": "2026-11-06T00:00:00Z", "actor": {"id": "u-approver"},
             "addedLabels": [{"name": "oncall:approved"}]}]}}}})

    client = linear_client(handler)
    issue = client.create_issue("t", "d", label_ids=["L1"], assignee_id="u1", priority=2)
    assert issue.identifier == "ENG-1" and issue.assignee_id == "u1" and issue.is_open
    ev = client.label_events("i1")[0]
    assert (ev.label_name, ev.actor_id) == ("oncall:approved", "u-approver")


def test_linear_errors():
    with pytest.raises(LinearUnavailable):
        linear_client(lambda req: httpx.Response(503)).get_issue("x")
    with pytest.raises(IntegrationError):
        linear_client(lambda req: httpx.Response(200, json={"errors": [{"message": "bad"}]})).get_issue("x")


def test_linear_team_and_user_validation():
    def handler(req):
        q = json.loads(req.content)
        if "team(id" in q["query"]:
            team = {"key": "BAT", "name": "Batch-001"} if q["variables"]["id"] == "team-1" else None
            return httpx.Response(200, json={"data": {"team": team}})
        if q["variables"]["id"] == "u-ok":
            return httpx.Response(200, json={"data": {"user": {"id": "u-ok"}}})
        return httpx.Response(200, json={"errors": [{"message": "Entity not found: User"}], "data": None})

    assert linear_client(handler).team_name() == "BAT (Batch-001)"
    with pytest.raises(IntegrationError):
        GraphQLLinearClient("k", "wrong-team", transport=httpx.MockTransport(handler)).team_name()
    assert linear_client(handler).missing_users(["u-ok", "u-bad", "u-ok"]) == ["u-bad"]


def test_linear_label_lookup_ignores_other_teams():
    def handler(req):
        return httpx.Response(200, json={"data": {"issueLabels": {"nodes": [
            {"id": "a", "name": "oncall:needs-human", "team": {"id": "other-team"}},
            {"id": "b", "name": "oncall:needs-human", "team": {"id": "team-1"}},
            {"id": "c", "name": "oncall:non-retriable", "team": None}]}}})

    assert linear_client(handler).get_label_ids(["oncall:needs-human", "oncall:non-retriable"]) == {
        "oncall:needs-human": "b", "oncall:non-retriable": "c"}


# ---- Groq (default LLM) -------------------------------------------------------------------
def groq_client(handler, **kw):
    return GroqLLMClient("gsk_test", http_client=httpx.Client(transport=httpx.MockTransport(handler)), **kw)


def _completion(content, finish_reason="stop"):
    return {"id": "chatcmpl-1", "object": "chat.completion", "created": 1, "model": "openai/gpt-oss-120b",
            "choices": [{"index": 0, "finish_reason": finish_reason,
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}}


def test_groq_request_uses_json_mode_and_returns_content():
    seen = {}

    def handler(req: httpx.Request):
        seen["path"] = req.url.path
        seen["auth"] = req.headers["Authorization"]
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=_completion(json.dumps(RETRIABLE)))

    out = groq_client(handler).analyze("system prompt with JSON schema", "user", ANALYSIS_JSON_SCHEMA)
    assert json.loads(out)["classification"] == "retriable"
    body = seen["body"]
    assert seen["path"] == "/openai/v1/chat/completions" and seen["auth"] == "Bearer gsk_test"
    assert body["model"] == "openai/gpt-oss-120b" and body["response_format"] == {"type": "json_object"}
    assert body["reasoning_effort"] == "medium" and body["temperature"] == 0
    assert [m["role"] for m in body["messages"]] == ["system", "user"]


def test_groq_reasoning_effort_can_be_disabled_for_non_reasoning_models():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx.Response(200, json=_completion("{}"))

    groq_client(handler, model="llama-3.3-70b-versatile", reasoning_effort="").analyze("s JSON", "u", {})
    assert "reasoning_effort" not in seen["body"] and seen["body"]["model"] == "llama-3.3-70b-versatile"


def test_groq_errors_and_truncation_raise_unavailable():
    with pytest.raises(LLMUnavailable):
        groq_client(lambda req: httpx.Response(200, json=_completion('{"a"', "length"))).analyze("s", "u", {})
    with pytest.raises(LLMUnavailable):
        groq_client(lambda req: httpx.Response(200, json=_completion(None))).analyze("s", "u", {})
    with pytest.raises(LLMUnavailable):
        groq_client(lambda req: httpx.Response(401, json={"error": {"message": "bad key"}})).analyze("s", "u", {})


# ---- Claude (optional provider) -----------------------------------------------------------------------------
def claude_client(handler):
    return AnthropicClaudeClient("sk-ant-test", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))


def _message(text, stop_reason="end_turn"):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": text}], "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 10}}


def test_claude_request_uses_structured_output_and_returns_text():
    seen = {}

    def handler(req):
        seen["body"] = json.loads(req.content)
        seen["beta"] = req.headers.get("anthropic-beta", "")
        return httpx2.Response(200, json=_message(json.dumps(RETRIABLE)))

    out = claude_client(handler).analyze("sys", "user", ANALYSIS_JSON_SCHEMA)
    assert json.loads(out)["classification"] == "retriable"
    body = seen["body"]
    assert body["model"] == "claude-opus-5-5"
    assert body["output_config"]["format"] == {"type": "json_schema", "schema": ANALYSIS_JSON_SCHEMA}
    assert body["thinking"] == {"type": "adaptive"} and body["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in seen["beta"]


def test_claude_refusal_and_errors_raise_unavailable():
    with pytest.raises(ClaudeUnavailable):
        claude_client(lambda req: httpx2.Response(200, json=_message("", "refusal"))).analyze("s", "u", {})
    with pytest.raises(ClaudeUnavailable):
        claude_client(lambda req: httpx2.Response(400, json={"type": "error", "error": {
            "type": "invalid_request_error", "message": "bad"}})).analyze("s", "u", {})
