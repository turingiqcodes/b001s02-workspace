"""Real GitHub REST API client (read-only: use a fine-grained token with
``Contents: read`` / ``Metadata: read`` on the DAG repositories)."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

import httpx

from ..models import CommitInfo, iso, parse_dt
from . import GitHubClient, IntegrationError

MAX_PATCH_CHARS = 4000


def _commit(d: dict[str, Any]) -> CommitInfo:
    commit = d.get("commit", {})
    author = d.get("author") or {}
    files = d.get("files") or []
    return CommitInfo(
        sha=d.get("sha", ""),
        author_login=author.get("login"),
        author_name=(commit.get("author") or {}).get("name"),
        message=(commit.get("message") or "")[:1000],
        timestamp=parse_dt((commit.get("author") or {}).get("date")),
        url=d.get("html_url"),
        files=[f.get("filename", "") for f in files],
        patch="\n".join(
            f"--- {f.get('filename')}\n{f.get('patch', '')}" for f in files if f.get("patch")
        )[:MAX_PATCH_CHARS] or None,
    )


class RestGitHubClient(GitHubClient):
    mode = "real"

    def __init__(self, token: str, base_url: str = "https://api.github.com", timeout: float = 20.0,
                 transport: Optional[httpx.BaseTransport] = None):
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "oncall-agent",
            },
        )

    def _get(self, path: str, params: Optional[dict[str, Any]] = None) -> Any:
        try:
            resp = self._http.get(path, params=params)
        except httpx.HTTPError as exc:
            raise IntegrationError(f"GitHub GET {path} failed: {exc}") from exc
        if resp.status_code >= 400:
            raise IntegrationError(f"GitHub GET {path} returned HTTP {resp.status_code}")
        return resp.json()

    def health(self) -> bool:
        try:
            self._get("/rate_limit")
            return True
        except IntegrationError:
            return False

    def list_commits(self, repo: str, path: str, since: Optional[datetime] = None, limit: int = 5) -> list[CommitInfo]:
        params: dict[str, Any] = {"path": path, "per_page": limit}
        if since:
            params["since"] = iso(since)
        return [_commit(c) for c in self._get(f"/repos/{repo}/commits", params)]

    def get_commit(self, repo: str, sha: str) -> CommitInfo:
        return _commit(self._get(f"/repos/{repo}/commits/{sha}"))
