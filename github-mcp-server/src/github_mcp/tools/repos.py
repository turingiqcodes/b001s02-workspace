"""Repository tools: repos, branches, files, commits."""

from __future__ import annotations

from typing import Any, Literal

from mcp.server.mcpserver import Context

from github_mcp.github_client import decode_content, encode_content, gh
from github_mcp.tools import Registrar, slim_repo, slim_user


def register(r: Registrar) -> None:
    @r.read("Get repository")
    async def get_repository(owner: str, repo: str, ctx: Context) -> dict[str, Any]:
        """Get metadata for a repository (description, default branch, stars, visibility)."""
        return slim_repo(await gh(ctx).get(f"/repos/{owner}/{repo}"))

    @r.read("List repositories")
    async def list_repositories(
        ctx: Context,
        owner: str | None = None,
        owner_type: Literal["user", "org"] = "user",
        sort: Literal["created", "updated", "pushed", "full_name"] = "updated",
        per_page: int = 30,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        """List repositories. With no owner, lists repos of the authenticated user."""
        params = {"sort": sort, "per_page": per_page, "page": page}
        if owner is None:
            path = "/user/repos"
        elif owner_type == "org":
            path = f"/orgs/{owner}/repos"
        else:
            path = f"/users/{owner}/repos"
        return [slim_repo(x) for x in await gh(ctx).get(path, params=params)]

    @r.write("Create repository")
    async def create_repository(
        name: str,
        ctx: Context,
        description: str = "",
        private: bool = True,
        auto_init: bool = True,
        org: str | None = None,
    ) -> dict[str, Any]:
        """Create a repository for the authenticated user, or in an organization when org is given."""
        path = f"/orgs/{org}/repos" if org else "/user/repos"
        body = {"name": name, "description": description, "private": private, "auto_init": auto_init}
        return slim_repo(await gh(ctx).post(path, json=body))

    @r.read("List branches")
    async def list_branches(owner: str, repo: str, ctx: Context, per_page: int = 30, page: int = 1) -> list[dict[str, Any]]:
        """List branches with their head commit SHA and protection flag."""
        data = await gh(ctx).get(f"/repos/{owner}/{repo}/branches", params={"per_page": per_page, "page": page})
        return [{"name": b["name"], "sha": b["commit"]["sha"], "protected": b.get("protected")} for b in data]

    @r.write("Create branch")
    async def create_branch(owner: str, repo: str, branch: str, ctx: Context, from_branch: str | None = None) -> dict[str, Any]:
        """Create a branch from from_branch (defaults to the repo's default branch)."""
        client = gh(ctx)
        if from_branch is None:
            from_branch = (await client.get(f"/repos/{owner}/{repo}"))["default_branch"]
        base = await client.get(f"/repos/{owner}/{repo}/git/ref/heads/{from_branch}")
        ref = await client.post(
            f"/repos/{owner}/{repo}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": base["object"]["sha"]}
        )
        return {"branch": branch, "sha": ref["object"]["sha"], "from": from_branch}

    @r.read("Get file contents")
    async def get_file_contents(owner: str, repo: str, path: str, ctx: Context, ref: str | None = None) -> dict[str, Any]:
        """Get a file's decoded text, or a directory listing when path is a directory."""
        data = await gh(ctx).get(f"/repos/{owner}/{repo}/contents/{path.lstrip('/')}", params={"ref": ref})
        if isinstance(data, list):
            return {"type": "dir", "path": path, "entries": [{"name": e["name"], "type": e["type"], "path": e["path"]} for e in data]}
        return {"type": data["type"], "path": data["path"], "sha": data["sha"], "size": data["size"], "content": decode_content(data)}

    @r.write("Create or update file", idempotent=True)
    async def create_or_update_file(
        owner: str,
        repo: str,
        path: str,
        content: str,
        message: str,
        branch: str,
        ctx: Context,
        sha: str | None = None,
    ) -> dict[str, Any]:
        """Create or overwrite a file in one commit. Pass the current blob sha when updating an existing file."""
        body = {"message": message, "content": encode_content(content), "branch": branch, "sha": sha}
        data = await gh(ctx).put(
            f"/repos/{owner}/{repo}/contents/{path.lstrip('/')}", json={k: v for k, v in body.items() if v is not None}
        )
        return {"path": data["content"]["path"], "sha": data["content"]["sha"], "commit": data["commit"]["sha"]}

    @r.read("List commits")
    async def list_commits(
        owner: str,
        repo: str,
        ctx: Context,
        sha: str | None = None,
        path: str | None = None,
        author: str | None = None,
        per_page: int = 30,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        """List commits, optionally filtered by branch/SHA, file path or author."""
        params = {"sha": sha, "path": path, "author": author, "per_page": per_page, "page": page}
        data = await gh(ctx).get(f"/repos/{owner}/{repo}/commits", params=params)
        return [
            {
                "sha": c["sha"],
                "message": c["commit"]["message"].splitlines()[0],
                "author": slim_user(c.get("author")) or c["commit"]["author"]["name"],
                "date": c["commit"]["author"]["date"],
            }
            for c in data
        ]

    @r.read("Get commit")
    async def get_commit(owner: str, repo: str, sha: str, ctx: Context) -> dict[str, Any]:
        """Get one commit with its full message, stats and changed files."""
        c = await gh(ctx).get(f"/repos/{owner}/{repo}/commits/{sha}")
        return {
            "sha": c["sha"],
            "message": c["commit"]["message"],
            "author": slim_user(c.get("author")) or c["commit"]["author"]["name"],
            "date": c["commit"]["author"]["date"],
            "stats": c.get("stats"),
            "files": [
                {"filename": f["filename"], "status": f["status"], "additions": f["additions"], "deletions": f["deletions"]}
                for f in c.get("files", [])
            ],
        }
