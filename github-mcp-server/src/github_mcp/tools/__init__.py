"""Toolset registry.

Each toolset module exposes ``register(registrar)``. The registrar wraps
``MCPServer.tool`` so that write tools are skipped in read-only mode and every
tool gets consistent MCP annotations.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations


class Registrar:
    def __init__(self, mcp: MCPServer, read_only: bool):
        self.mcp = mcp
        self.read_only = read_only
        self.registered: list[str] = []

    def read(self, title: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return self._register(title, ToolAnnotations(title=title, read_only_hint=True, open_world_hint=True))

    def write(self, title: str, *, destructive: bool = False, idempotent: bool = False):
        annotations = ToolAnnotations(
            title=title,
            read_only_hint=False,
            destructive_hint=destructive,
            idempotent_hint=idempotent,
            open_world_hint=True,
        )
        return self._register(title, annotations, is_write=True)

    def _register(self, title: str, annotations: ToolAnnotations, is_write: bool = False):
        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            if is_write and self.read_only:
                return fn
            self.mcp.tool(title=title, annotations=annotations)(fn)
            self.registered.append(fn.__name__)
            return fn

        return decorator


def slim_user(u: dict[str, Any] | None) -> str | None:
    return u.get("login") if u else None


def slim_repo(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "full_name": r["full_name"],
        "description": r.get("description"),
        "private": r.get("private"),
        "default_branch": r.get("default_branch"),
        "language": r.get("language"),
        "stars": r.get("stargazers_count"),
        "forks": r.get("forks_count"),
        "open_issues": r.get("open_issues_count"),
        "html_url": r.get("html_url"),
        "updated_at": r.get("updated_at"),
    }


def slim_issue(i: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": i["number"],
        "title": i["title"],
        "state": i.get("state"),
        "author": slim_user(i.get("user")),
        "labels": [lbl["name"] if isinstance(lbl, dict) else lbl for lbl in i.get("labels", [])],
        "assignees": [a["login"] for a in i.get("assignees") or []],
        "comments": i.get("comments"),
        "is_pull_request": "pull_request" in i,
        "html_url": i.get("html_url"),
        "created_at": i.get("created_at"),
        "updated_at": i.get("updated_at"),
    }


def slim_pr(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": p["number"],
        "title": p["title"],
        "state": p.get("state"),
        "draft": p.get("draft"),
        "merged": p.get("merged"),
        "author": slim_user(p.get("user")),
        "head": p.get("head", {}).get("ref"),
        "base": p.get("base", {}).get("ref"),
        "html_url": p.get("html_url"),
        "created_at": p.get("created_at"),
        "updated_at": p.get("updated_at"),
    }


def slim_run(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": r["id"],
        "name": r.get("name"),
        "workflow_id": r.get("workflow_id"),
        "event": r.get("event"),
        "branch": r.get("head_branch"),
        "sha": r.get("head_sha"),
        "status": r.get("status"),
        "conclusion": r.get("conclusion"),
        "run_number": r.get("run_number"),
        "actor": slim_user(r.get("actor")),
        "html_url": r.get("html_url"),
        "created_at": r.get("created_at"),
    }
