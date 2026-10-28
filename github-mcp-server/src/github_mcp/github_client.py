"""Thin async wrapper over the GitHub REST API.

The MCP server never talks to GitHub directly from a tool body; every call goes
through ``GitHubClient`` so auth, error mapping and pagination live in one place.
"""

from __future__ import annotations

import base64
from typing import Any

import httpx
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError

from github_mcp.config import settings

API_VERSION = "2022-11-28"


class GitHubClient:
    def __init__(self, token: str | None, base_url: str | None = None, timeout: float = 30.0):
        self.base_url = (base_url or settings.api_url).rstrip("/")
        self.headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "github-mcp-server/0.1",
        }
        if token:
            self.headers["Authorization"] = f"Bearer {token}"
        self.timeout = timeout

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        accept: str | None = None,
        follow_redirects: bool = False,
    ) -> Any:
        headers = dict(self.headers)
        if accept:
            headers["Accept"] = accept
        if params:
            params = {k: v for k, v in params.items() if v is not None}
        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=follow_redirects) as http:
            resp = await http.request(method, f"{self.base_url}{path}", params=params, json=json, headers=headers)
        if resp.status_code >= 400:
            raise ToolError(_describe_error(resp))
        if resp.status_code == 204 or not resp.content:
            return None
        if "json" in resp.headers.get("content-type", ""):
            return resp.json()
        return resp.text

    async def get(self, path: str, **kw: Any) -> Any:
        return await self.request("GET", path, **kw)

    async def post(self, path: str, **kw: Any) -> Any:
        return await self.request("POST", path, **kw)

    async def patch(self, path: str, **kw: Any) -> Any:
        return await self.request("PATCH", path, **kw)

    async def put(self, path: str, **kw: Any) -> Any:
        return await self.request("PUT", path, **kw)

    async def delete(self, path: str, **kw: Any) -> Any:
        return await self.request("DELETE", path, **kw)


def _describe_error(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        message = body.get("message", resp.text)
        errors = body.get("errors")
        if errors:
            message = f"{message}: {errors}"
    except ValueError:
        message = resp.text[:500]
    hint = ""
    if resp.status_code == 401:
        hint = " (check the GitHub token)"
    elif resp.status_code == 403 and resp.headers.get("x-ratelimit-remaining") == "0":
        hint = f" (rate limit exceeded, resets at epoch {resp.headers.get('x-ratelimit-reset')})"
    elif resp.status_code == 404:
        hint = " (not found, or the token lacks access)"
    return f"GitHub API {resp.status_code}: {message}{hint}"


def token_from_context(ctx: Context | None) -> str | None:
    """Pick the GitHub token for this request.

    Remote HTTP clients send their own token as ``Authorization: Bearer <pat>``;
    that wins over the server's environment token, so one HTTP server can serve
    many users without sharing credentials. Over stdio there are no headers and
    the environment token is used.
    """
    if ctx is not None:
        try:
            headers = ctx.headers
        except ValueError:
            headers = None
        if headers:
            auth = next((v for k, v in headers.items() if k.lower() == "authorization"), None)
            if auth and auth.lower().startswith("bearer "):
                return auth[7:].strip()
    return settings.token


def gh(ctx: Context | None) -> GitHubClient:
    token = token_from_context(ctx)
    if not token:
        raise ToolError(
            "No GitHub token: set GITHUB_PERSONAL_ACCESS_TOKEN, or send 'Authorization: Bearer <token>' over HTTP"
        )
    return GitHubClient(token)


def decode_content(item: dict[str, Any]) -> str:
    if item.get("encoding") == "base64":
        return base64.b64decode(item["content"]).decode("utf-8", errors="replace")
    return item.get("content", "")


def encode_content(text: str) -> str:
    return base64.b64encode(text.encode()).decode()
