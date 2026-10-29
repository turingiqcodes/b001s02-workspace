"""Pull request tools."""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import Context

from github_mcp.github_client import gh
from github_mcp.tools import Registrar, slim_pr, slim_user

MAX_DIFF_CHARS = 60_000


def register(r: Registrar) -> None:
    @r.read("List pull requests")
    async def list_pull_requests(
        owner: str,
        repo: str,
        ctx: Context,
        state: Literal["open", "closed", "all"] = "open",
        base: str | None = None,
        head: str | None = None,
        per_page: int = 30,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        """List pull requests. head filters by 'user:branch'."""
        params = {"state": state, "base": base, "head": head, "per_page": per_page, "page": page}
        return [slim_pr(p) for p in await gh(ctx).get(f"/repos/{owner}/{repo}/pulls", params=params)]

    @r.read("Get pull request")
    async def get_pull_request(owner: str, repo: str, pull_number: int, ctx: Context) -> dict[str, Any]:
        """Get a pull request with body, mergeability and change stats."""
        p = await gh(ctx).get(f"/repos/{owner}/{repo}/pulls/{pull_number}")
        return {
            **slim_pr(p),
            "body": p.get("body"),
            "mergeable": p.get("mergeable"),
            "mergeable_state": p.get("mergeable_state"),
            "head_sha": p["head"]["sha"],
            "commits": p.get("commits"),
            "additions": p.get("additions"),
            "deletions": p.get("deletions"),
            "changed_files": p.get("changed_files"),
            "requested_reviewers": [u["login"] for u in p.get("requested_reviewers", [])],
        }

    @r.read("Get pull request files")
    async def get_pull_request_files(owner: str, repo: str, pull_number: int, ctx: Context, per_page: int = 100) -> list[dict[str, Any]]:
        """List files changed in a pull request with per-file additions/deletions."""
        data = await gh(ctx).get(f"/repos/{owner}/{repo}/pulls/{pull_number}/files", params={"per_page": per_page})
        return [
            {"filename": f["filename"], "status": f["status"], "additions": f["additions"], "deletions": f["deletions"]}
            for f in data
        ]

    @r.read("Get pull request diff")
    async def get_pull_request_diff(owner: str, repo: str, pull_number: int, ctx: Context) -> str:
        """Get the unified diff of a pull request (truncated for very large PRs)."""
        diff = await gh(ctx).get(f"/repos/{owner}/{repo}/pulls/{pull_number}", accept="application/vnd.github.diff")
        if len(diff) > MAX_DIFF_CHARS:
            return diff[:MAX_DIFF_CHARS] + f"\n... [truncated, {len(diff) - MAX_DIFF_CHARS} more characters]"
        return diff

    @r.read("List pull request reviews")
    async def list_pull_request_reviews(owner: str, repo: str, pull_number: int, ctx: Context) -> list[dict[str, Any]]:
        """List reviews submitted on a pull request."""
        data = await gh(ctx).get(f"/repos/{owner}/{repo}/pulls/{pull_number}/reviews")
        return [{"id": v["id"], "user": slim_user(v.get("user")), "state": v["state"], "body": v.get("body")} for v in data]

    @r.write("Create pull request")
    async def create_pull_request(
        owner: str,
        repo: str,
        title: str,
        head: str,
        base: str,
        ctx: Context,
        body: str = "",
        draft: bool = False,
    ) -> dict[str, Any]:
        """Open a pull request from head into base."""
        payload = {"title": title, "head": head, "base": base, "body": body, "draft": draft}
        return slim_pr(await gh(ctx).post(f"/repos/{owner}/{repo}/pulls", json=payload))

    @r.write("Update pull request", idempotent=True)
    async def update_pull_request(
        owner: str,
        repo: str,
        pull_number: int,
        ctx: Context,
        title: str | None = None,
        body: str | None = None,
        state: Literal["open", "closed"] | None = None,
        base: str | None = None,
    ) -> dict[str, Any]:
        """Edit a pull request's title, body, base branch, or close/reopen it."""
        payload = {k: v for k, v in {"title": title, "body": body, "state": state, "base": base}.items() if v is not None}
        return slim_pr(await gh(ctx).patch(f"/repos/{owner}/{repo}/pulls/{pull_number}", json=payload))

    @r.write("Request reviewers")
    async def request_reviewers(owner: str, repo: str, pull_number: int, reviewers: list[str], ctx: Context) -> dict[str, Any]:
        """Request reviews from users on a pull request."""
        p = await gh(ctx).post(
            f"/repos/{owner}/{repo}/pulls/{pull_number}/requested_reviewers", json={"reviewers": reviewers}
        )
        return {"number": p["number"], "requested_reviewers": [u["login"] for u in p.get("requested_reviewers", [])]}

    @r.write("Create pull request review")
    async def create_pull_request_review(
        owner: str,
        repo: str,
        pull_number: int,
        event: Literal["APPROVE", "REQUEST_CHANGES", "COMMENT"],
        ctx: Context,
        body: str = "",
    ) -> dict[str, Any]:
        """Submit a review: approve, request changes, or comment."""
        v = await gh(ctx).post(f"/repos/{owner}/{repo}/pulls/{pull_number}/reviews", json={"event": event, "body": body})
        return {"id": v["id"], "state": v["state"], "html_url": v.get("html_url")}

    @r.write("Merge pull request", destructive=True)
    async def merge_pull_request(
        owner: str,
        repo: str,
        pull_number: int,
        ctx: Context,
        merge_method: Literal["merge", "squash", "rebase"] = "squash",
        commit_title: str | None = None,
        commit_message: str | None = None,
    ) -> dict[str, Any]:
        """Merge a pull request."""
        payload = {"merge_method": merge_method, "commit_title": commit_title, "commit_message": commit_message}
        data = await gh(ctx).put(
            f"/repos/{owner}/{repo}/pulls/{pull_number}/merge", json={k: v for k, v in payload.items() if v is not None}
        )
        return {"merged": data.get("merged"), "sha": data.get("sha"), "message": data.get("message")}
