"""Search tools over GitHub's search API (uses GitHub search qualifier syntax)."""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import Context

from github_mcp.github_client import gh
from github_mcp.tools import Registrar, slim_issue, slim_repo, slim_user


def _page(data: dict[str, Any], items: list[Any]) -> dict[str, Any]:
    return {"total_count": data["total_count"], "incomplete_results": data.get("incomplete_results"), "items": items}


def register(r: Registrar) -> None:
    @r.read("Search repositories")
    async def search_repositories(
        query: str,
        ctx: Context,
        sort: Literal["stars", "forks", "updated", "help-wanted-issues"] | None = None,
        per_page: int = 20,
        page: int = 1,
    ) -> dict[str, Any]:
        """Search repositories, e.g. 'mcp language:python stars:>100'."""
        data = await gh(ctx).get("/search/repositories", params={"q": query, "sort": sort, "per_page": per_page, "page": page})
        return _page(data, [slim_repo(x) for x in data["items"]])

    @r.read("Search code")
    async def search_code(query: str, ctx: Context, per_page: int = 20, page: int = 1) -> dict[str, Any]:
        """Search code, e.g. 'FastAPI repo:owner/name path:src extension:py'."""
        data = await gh(ctx).get("/search/code", params={"q": query, "per_page": per_page, "page": page})
        items = [{"repo": x["repository"]["full_name"], "path": x["path"], "sha": x["sha"], "html_url": x["html_url"]} for x in data["items"]]
        return _page(data, items)

    @r.read("Search issues and pull requests")
    async def search_issues(
        query: str,
        ctx: Context,
        sort: Literal["comments", "reactions", "created", "updated"] | None = None,
        per_page: int = 20,
        page: int = 1,
    ) -> dict[str, Any]:
        """Search issues and PRs, e.g. 'repo:owner/name is:pr is:open label:bug'."""
        data = await gh(ctx).get("/search/issues", params={"q": query, "sort": sort, "per_page": per_page, "page": page})
        items = [{**slim_issue(x), "repo": x["repository_url"].split("/repos/", 1)[-1]} for x in data["items"]]
        return _page(data, items)

    @r.read("Search commits")
    async def search_commits(query: str, ctx: Context, per_page: int = 20, page: int = 1) -> dict[str, Any]:
        """Search commits by message/author, e.g. 'fix login repo:owner/name author:octocat'."""
        data = await gh(ctx).get("/search/commits", params={"q": query, "per_page": per_page, "page": page})
        items = [
            {
                "repo": x["repository"]["full_name"],
                "sha": x["sha"],
                "message": x["commit"]["message"].splitlines()[0],
                "author": slim_user(x.get("author")),
                "html_url": x["html_url"],
            }
            for x in data["items"]
        ]
        return _page(data, items)

    @r.read("Search users")
    async def search_users(query: str, ctx: Context, per_page: int = 20, page: int = 1) -> dict[str, Any]:
        """Search users and organizations, e.g. 'location:berlin language:go'."""
        data = await gh(ctx).get("/search/users", params={"q": query, "per_page": per_page, "page": page})
        items = [{"login": x["login"], "type": x["type"], "html_url": x["html_url"]} for x in data["items"]]
        return _page(data, items)
