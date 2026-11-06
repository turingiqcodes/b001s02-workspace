"""In-memory Linear mock, including helpers to act as a human
(comment, add label, close an issue)."""
from __future__ import annotations

import itertools
from typing import Optional

from ...models import LabelEvent, LinearComment, LinearIssue, utcnow
from .. import IntegrationError, LinearClient, LinearUnavailable

DEFAULT_LABELS = ["oncall:non-retriable", "oncall:needs-human", "oncall:approval-required", "oncall:approved"]


class MockLinearClient(LinearClient):
    mode = "mock"

    def __init__(self, labels: Optional[list[str]] = None, agent_user_id: str = "linear-bot") -> None:
        self.available = True
        self.agent_user_id = agent_user_id
        self.issues: dict[str, LinearIssue] = {}
        self.comments: dict[str, list[LinearComment]] = {}
        self.history: dict[str, list[LabelEvent]] = {}
        self.label_catalog = {name: f"label-{i}" for i, name in enumerate(labels if labels is not None else DEFAULT_LABELS)}
        self._ids = itertools.count(1)
        self.calls: list[str] = []

    def _check(self, op: str) -> None:
        self.calls.append(op)
        if not self.available:
            raise LinearUnavailable(f"mock Linear is down ({op})")

    def _label_name(self, label_id: str) -> str:
        for name, lid in self.label_catalog.items():
            if lid == label_id:
                return name
        raise IntegrationError(f"unknown label id {label_id}")

    # ---- human-side helpers (simulation / tests) -------------------------------------
    def human_comment(self, issue_id: str, body: str, author_id: str) -> LinearComment:
        c = LinearComment(id=f"c-{next(self._ids)}", body=body, author_id=author_id, created_at=utcnow())
        self.comments.setdefault(issue_id, []).append(c)
        return c

    def human_add_label(self, issue_id: str, name: str, actor_id: str) -> None:
        self.issues[issue_id].labels.append(name)
        self.history.setdefault(issue_id, []).append(
            LabelEvent(id=f"h-{next(self._ids)}", label_name=name, actor_id=actor_id, created_at=utcnow()))

    def human_close(self, issue_id: str) -> None:
        self.issues[issue_id].state_type = "completed"

    # ---- API --------------------------------------------------------------------------
    def health(self) -> bool:
        return self.available

    def create_issue(self, title: str, description: str, *, label_ids: list[str],
                     assignee_id: Optional[str], priority: int = 0) -> LinearIssue:
        self._check("create_issue")
        n = next(self._ids)
        issue = LinearIssue(id=f"issue-{n}", identifier=f"ONC-{n}", title=title, description=description,
                            labels=[self._label_name(l) for l in label_ids], assignee_id=assignee_id,
                            priority=priority, url=f"https://linear.example/ONC-{n}")
        self.issues[issue.id] = issue
        for name in issue.labels:
            self.history.setdefault(issue.id, []).append(
                LabelEvent(id=f"h-{next(self._ids)}", label_name=name, actor_id=self.agent_user_id,
                           created_at=utcnow()))
        return issue

    def get_issue(self, issue_id: str) -> LinearIssue:
        self._check("get_issue")
        if issue_id not in self.issues:
            raise IntegrationError(f"issue {issue_id} not found")
        return self.issues[issue_id]

    def search_issues(self, text: str) -> list[LinearIssue]:
        self._check("search_issues")
        return [i for i in self.issues.values() if text in i.description or text in i.title]

    def add_comment(self, issue_id: str, body: str) -> LinearComment:
        self._check("add_comment")
        return self.human_comment(issue_id, body, self.agent_user_id)

    def list_comments(self, issue_id: str) -> list[LinearComment]:
        self._check("list_comments")
        return list(self.comments.get(issue_id, []))

    def add_label(self, issue_id: str, label_id: str) -> None:
        self._check("add_label")
        name = self._label_name(label_id)
        if name not in self.issues[issue_id].labels:
            self.issues[issue_id].labels.append(name)
            self.history.setdefault(issue_id, []).append(
                LabelEvent(id=f"h-{next(self._ids)}", label_name=name, actor_id=self.agent_user_id,
                           created_at=utcnow()))

    def get_label_ids(self, names: list[str]) -> dict[str, str]:
        self._check("get_label_ids")
        return {n: self.label_catalog[n] for n in names if n in self.label_catalog}

    def assign_issue(self, issue_id: str, user_id: str) -> None:
        self._check("assign_issue")
        self.issues[issue_id].assignee_id = user_id

    def resolve_issue(self, issue_id: str) -> None:
        self._check("resolve_issue")
        self.issues[issue_id].state_type = "completed"

    def label_events(self, issue_id: str) -> list[LabelEvent]:
        self._check("label_events")
        return list(self.history.get(issue_id, []))
