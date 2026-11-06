"""In-memory GitHub mock."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ...models import CommitInfo
from .. import GitHubClient, IntegrationError


class MockGitHubClient(GitHubClient):
    mode = "mock"

    def __init__(self) -> None:
        self.available = True
        self.commits: dict[str, list[CommitInfo]] = {}  # repo -> newest-first commits
        self.calls: list[str] = []

    def add_commit(self, repo: str, commit: CommitInfo) -> None:
        self.commits.setdefault(repo, []).insert(0, commit)

    def _check(self, op: str) -> None:
        self.calls.append(op)
        if not self.available:
            raise IntegrationError(f"mock GitHub is down ({op})")

    def health(self) -> bool:
        return self.available

    def list_commits(self, repo: str, path: str, since: Optional[datetime] = None, limit: int = 5) -> list[CommitInfo]:
        self._check("list_commits")
        out = []
        for c in self.commits.get(repo, []):
            touches = any(f == path or f.startswith(path.rstrip("/") + "/") for f in c.files)
            if touches and (since is None or (c.timestamp and c.timestamp >= since)):
                out.append(CommitInfo(sha=c.sha, author_login=c.author_login, author_name=c.author_name,
                                      message=c.message, timestamp=c.timestamp, url=c.url, files=list(c.files)))
        return out[:limit]

    def get_commit(self, repo: str, sha: str) -> CommitInfo:
        self._check("get_commit")
        for c in self.commits.get(repo, []):
            if c.sha == sha:
                return c
        raise IntegrationError(f"commit {sha} not found")
