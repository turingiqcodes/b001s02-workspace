"""Tool tests: an in-process MCP client calls the server; GitHub's REST API is mocked with respx."""

from __future__ import annotations

import base64
import json

import httpx
import pytest
import respx
from mcp import Client

from github_mcp.config import settings
from github_mcp.server import build_server

API = "https://api.github.com"
pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def token(monkeypatch):
    monkeypatch.setattr(settings, "token", "test-token")
    monkeypatch.setattr(settings, "api_url", API)


@pytest.fixture
def github():
    with respx.mock(base_url=API, assert_all_called=False) as mock:
        yield mock


async def call(name: str, args: dict, server=None):
    async with Client(server or build_server()) as client:
        return await client.call_tool(name, args)


def payload(result):
    assert not result.is_error, result.content
    if result.structured_content is not None:
        sc = result.structured_content
        return sc["result"] if isinstance(sc, dict) and set(sc) == {"result"} else sc
    return result.content[0].text


async def test_all_toolsets_registered():
    async with Client(build_server()) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    for expected in ["get_repository", "create_pull_request", "list_workflow_runs", "create_issue", "search_code"]:
        assert expected in names


async def test_read_only_hides_write_tools():
    async with Client(build_server(read_only=True)) as client:
        tools = (await client.list_tools()).tools
    names = {t.name for t in tools}
    assert "merge_pull_request" not in names and "create_issue" not in names
    assert "get_issue" in names
    assert all(t.annotations.read_only_hint for t in tools)


async def test_toolset_filter():
    async with Client(build_server(toolsets=("search",))) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert names and all(n.startswith("search_") for n in names)


def test_unknown_toolset_rejected():
    with pytest.raises(ValueError):
        build_server(toolsets=("nope",))


async def test_get_repository_sends_auth(github):
    route = github.get("/repos/octo/demo").respond(
        json={"full_name": "octo/demo", "default_branch": "main", "stargazers_count": 5, "private": False}
    )
    data = payload(await call("get_repository", {"owner": "octo", "repo": "demo"}))
    assert data["full_name"] == "octo/demo" and data["stars"] == 5
    assert route.calls.last.request.headers["authorization"] == "Bearer test-token"


async def test_get_file_contents_decodes(github):
    github.get("/repos/octo/demo/contents/README.md").respond(
        json={"type": "file", "path": "README.md", "sha": "abc", "size": 5, "encoding": "base64",
              "content": base64.b64encode(b"hello").decode()}
    )
    data = payload(await call("get_file_contents", {"owner": "octo", "repo": "demo", "path": "README.md"}))
    assert data["content"] == "hello"


async def test_create_branch_from_default(github):
    github.get("/repos/octo/demo").respond(json={"full_name": "octo/demo", "default_branch": "main"})
    github.get("/repos/octo/demo/git/ref/heads/main").respond(json={"object": {"sha": "s1"}})
    create = github.post("/repos/octo/demo/git/refs").respond(201, json={"object": {"sha": "s1"}})
    data = payload(await call("create_branch", {"owner": "octo", "repo": "demo", "branch": "feat"}))
    assert data == {"branch": "feat", "sha": "s1", "from": "main"}
    assert json.loads(create.calls.last.request.content) == {"ref": "refs/heads/feat", "sha": "s1"}


async def test_create_pull_request(github):
    route = github.post("/repos/octo/demo/pulls").respond(
        201, json={"number": 7, "title": "Add x", "state": "open", "head": {"ref": "feat"}, "base": {"ref": "main"}}
    )
    data = payload(await call("create_pull_request", {"owner": "octo", "repo": "demo", "title": "Add x", "head": "feat", "base": "main"}))
    assert data["number"] == 7 and data["head"] == "feat"
    assert json.loads(route.calls.last.request.content)["base"] == "main"


async def test_merge_pull_request(github):
    github.put("/repos/octo/demo/pulls/7/merge").respond(json={"merged": True, "sha": "m1", "message": "ok"})
    data = payload(await call("merge_pull_request", {"owner": "octo", "repo": "demo", "pull_number": 7}))
    assert data["merged"] is True


async def test_failing_ci_flow(github):
    github.get("/repos/octo/demo/actions/workflows/ci.yml/runs").respond(
        json={"workflow_runs": [{"id": 99, "name": "CI", "status": "completed", "conclusion": "failure", "head_branch": "main"}]}
    )
    github.get("/repos/octo/demo/actions/runs/99/jobs").respond(
        json={"jobs": [{"id": 5, "name": "test", "status": "completed", "conclusion": "failure",
                        "steps": [{"name": "pytest", "conclusion": "failure"}, {"name": "lint", "conclusion": "success"}]}]}
    )
    github.get("/repos/octo/demo/actions/jobs/5/logs").respond(text="x" * 100 + "AssertionError", headers={"content-type": "text/plain"})
    runs = payload(await call("list_workflow_runs", {"owner": "octo", "repo": "demo", "workflow": "ci.yml", "status": "failure"}))
    assert runs[0]["conclusion"] == "failure"
    jobs = payload(await call("list_workflow_jobs", {"owner": "octo", "repo": "demo", "run_id": 99}))
    assert jobs[0]["failed_steps"] == ["pytest"]
    log = payload(await call("get_job_logs", {"owner": "octo", "repo": "demo", "job_id": 5, "tail_chars": 20}))
    assert log.endswith("AssertionError") and log.startswith("[showing last 20")


async def test_trigger_workflow(github):
    route = github.post("/repos/octo/demo/actions/workflows/deploy.yml/dispatches").respond(204)
    data = payload(await call("trigger_workflow", {"owner": "octo", "repo": "demo", "workflow": "deploy.yml", "ref": "main", "inputs": {"env": "prod"}}))
    assert data["dispatched"] is True
    assert json.loads(route.calls.last.request.content) == {"ref": "main", "inputs": {"env": "prod"}}


async def test_list_issues_excludes_prs(github):
    github.get("/repos/octo/demo/issues").respond(json=[
        {"number": 1, "title": "bug", "labels": [{"name": "bug"}]},
        {"number": 2, "title": "pr", "pull_request": {}},
    ])
    data = payload(await call("list_issues", {"owner": "octo", "repo": "demo", "labels": ["bug"]}))
    assert [i["number"] for i in data] == [1] and data[0]["labels"] == ["bug"]


async def test_update_issue_only_sends_given_fields(github):
    route = github.patch("/repos/octo/demo/issues/1").respond(json={"number": 1, "title": "bug", "state": "closed"})
    payload(await call("update_issue", {"owner": "octo", "repo": "demo", "issue_number": 1, "state": "closed", "state_reason": "completed"}))
    assert json.loads(route.calls.last.request.content) == {"state": "closed", "state_reason": "completed"}


async def test_search_issues(github):
    route = github.get("/search/issues").respond(json={"total_count": 1, "items": [
        {"number": 3, "title": "t", "repository_url": f"{API}/repos/octo/demo", "pull_request": {}}
    ]})
    data = payload(await call("search_issues", {"query": "is:pr is:open"}))
    assert data["total_count"] == 1 and data["items"][0]["repo"] == "octo/demo"
    assert route.calls.last.request.url.params["q"] == "is:pr is:open"


async def test_github_error_becomes_tool_error(github):
    github.get("/repos/octo/missing").respond(404, json={"message": "Not Found"})
    result = await call("get_repository", {"owner": "octo", "repo": "missing"})
    assert result.is_error
    assert "GitHub API 404" in result.content[0].text


async def test_missing_token_is_reported(monkeypatch):
    monkeypatch.setattr(settings, "token", None)
    result = await call("get_repository", {"owner": "octo", "repo": "demo"})
    assert result.is_error and "No GitHub token" in result.content[0].text
