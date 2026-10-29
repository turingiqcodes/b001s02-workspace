"""Issue tools."""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import Context

from github_mcp.github_client import gh
from github_mcp.tools import Registrar, slim_issue, slim_user


def register(r: Registrar) -> None:
    @r.read("List issues")
    async def list_issues(
        owner: str,
        repo: str,
        ctx: Context,
        state: Literal["open", "closed", "all"] = "open",
        labels: list[str] | None = None,
        assignee: str | None = None,
        since: str | None = None,
        per_page: int = 30,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        """List issues (pull requests excluded). since is an ISO 8601 timestamp."""
        params = {
            "state": state,
            "labels": ",".join(labels) if labels else None,
            "assignee": assignee,
            "since": since,
            "per_page": per_page,
            "page": page,
        }
        data = await gh(ctx).get(f"/repos/{owner}/{repo}/issues", params=params)
        return [slim_issue(i) for i in data if "pull_request" not in i]

    @r.read("Get issue")
    async def get_issue(owner: str, repo: str, issue_number: int, ctx: Context) -> dict[str, Any]:
        """Get one issue including its body."""
        i = await gh(ctx).get(f"/repos/{owner}/{repo}/issues/{issue_number}")
        return {**slim_issue(i), "body": i.get("body")}

    @r.read("List issue comments")
    async def list_issue_comments(owner: str, repo: str, issue_number: int, ctx: Context, per_page: int = 30, page: int = 1) -> list[dict[str, Any]]:
        """List comments on an issue or pull request."""
        data = await gh(ctx).get(
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments", params={"per_page": per_page, "page": page}
        )
        return [{"id": c["id"], "author": slim_user(c.get("user")), "body": c["body"], "created_at": c["created_at"]} for c in data]

    @r.write("Create issue")
    async def create_issue(
        owner: str,
        repo: str,
        title: str,
        ctx: Context,
        body: str = "",
        labels: list[str] | None = None,
        assignees: list[str] | None = None,
    ) -> dict[str, Any]:
        """Open a new issue."""
        payload = {"title": title, "body": body, "labels": labels or [], "assignees": assignees or []}
        return slim_issue(await gh(ctx).post(f"/repos/{owner}/{repo}/issues", json=payload))

    @r.write("Update issue", idempotent=True)
    async def update_issue(
        owner: str,
        repo: str,
        issue_number: int,
        ctx: Context,
        title: str | None = None,
        body: str | None = None,
        state: Literal["open", "closed"] | None = None,
        state_reason: Literal["completed", "not_planned", "reopened"] | None = None,
        labels: list[str] | None = None,
        assignees: list[str] | None = None,
    ) -> dict[str, Any]:
        """Edit an issue. labels/assignees replace the existing lists when given."""
        payload = {
            "title": title,
            "body": body,
            "state": state,
            "state_reason": state_reason,
            "labels": labels,
            "assignees": assignees,
        }
        data = await gh(ctx).patch(
            f"/repos/{owner}/{repo}/issues/{issue_number}", json={k: v for k, v in payload.items() if v is not None}
        )
        return slim_issue(data)

    @r.write("Add issue comment")
    async def add_issue_comment(owner: str, repo: str, issue_number: int, body: str, ctx: Context) -> dict[str, Any]:
        """Comment on an issue or pull request."""
        c = await gh(ctx).post(f"/repos/{owner}/{repo}/issues/{issue_number}/comments", json={"body": body})
        return {"id": c["id"], "html_url": c["html_url"]}
