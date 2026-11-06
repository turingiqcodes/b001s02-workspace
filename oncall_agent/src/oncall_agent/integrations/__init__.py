"""Integration interfaces.

The agent core only talks to these abstract classes. Each one has a real
implementation (``airflow.py``, ``github.py``, ``linear.py``, ``groq.py`` / ``claude.py``)
and an offline mock (``mocks/``). Tests and the simulation use the mocks.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Optional

from ..models import (
    CommitInfo,
    DagRunInfo,
    ImportErrorInfo,
    LabelEvent,
    LinearComment,
    LinearIssue,
    TaskInstanceInfo,
)


class IntegrationError(Exception):
    """An external service call failed (network, auth, 5xx, bad response)."""


class AirflowUnavailable(IntegrationError):
    """Airflow could not be reached; detection must hold the watermark."""


class LinearUnavailable(IntegrationError):
    pass


class LLMUnavailable(IntegrationError):
    """The LLM provider (Groq or Claude) failed, refused, or returned nothing usable."""


ClaudeUnavailable = LLMUnavailable  # name used by the optional Claude client


class AirflowClient(ABC):
    mode: str = "real"

    @abstractmethod
    def health(self) -> bool: ...

    # --- reads ---
    @abstractmethod
    def list_failed_task_instances(self, end_date_gte: datetime) -> list[TaskInstanceInfo]: ...

    @abstractmethod
    def list_failed_dag_runs(self, end_date_gte: datetime) -> list[DagRunInfo]: ...

    @abstractmethod
    def list_task_instances(self, dag_id: str, run_id: str) -> list[TaskInstanceInfo]: ...

    @abstractmethod
    def get_task_instance(self, dag_id: str, run_id: str, task_id: str, map_index: int = -1) -> TaskInstanceInfo: ...

    @abstractmethod
    def get_dag_run(self, dag_id: str, run_id: str) -> DagRunInfo: ...

    @abstractmethod
    def get_task_log(self, dag_id: str, run_id: str, task_id: str, try_number: int, map_index: int = -1) -> str: ...

    @abstractmethod
    def get_dag_details(self, dag_id: str) -> dict[str, Any]: ...

    @abstractmethod
    def get_dag_source(self, dag_id: str) -> str: ...

    @abstractmethod
    def list_recent_dag_runs(self, dag_id: str, limit: int = 10) -> list[DagRunInfo]: ...

    @abstractmethod
    def list_import_errors(self) -> list[ImportErrorInfo]: ...

    # --- writes (only ever called by RetryManager / ApprovalManager / guarded MCP tools) ---
    @abstractmethod
    def clear_task_instances(
        self, dag_id: str, run_id: str, task_ids: list[str], *, dry_run: bool,
        only_failed: bool = True, include_downstream: bool = True, map_indexes: Optional[list[int]] = None,
    ) -> list[TaskInstanceInfo]: ...

    @abstractmethod
    def clear_dag_run(self, dag_id: str, run_id: str, *, dry_run: bool) -> list[TaskInstanceInfo]: ...

    @abstractmethod
    def set_task_instance_state(self, dag_id: str, run_id: str, task_id: str, state: str,
                                map_index: int = -1) -> TaskInstanceInfo: ...

    @abstractmethod
    def set_dag_run_state(self, dag_id: str, run_id: str, state: str) -> DagRunInfo: ...


class GitHubClient(ABC):
    mode: str = "real"

    @abstractmethod
    def health(self) -> bool: ...

    @abstractmethod
    def list_commits(self, repo: str, path: str, since: Optional[datetime] = None, limit: int = 5) -> list[CommitInfo]: ...

    @abstractmethod
    def get_commit(self, repo: str, sha: str) -> CommitInfo: ...


class LinearClient(ABC):
    mode: str = "real"

    @abstractmethod
    def health(self) -> bool: ...

    @abstractmethod
    def create_issue(self, title: str, description: str, *, label_ids: list[str],
                     assignee_id: Optional[str], priority: int = 0) -> LinearIssue: ...

    @abstractmethod
    def get_issue(self, issue_id: str) -> LinearIssue: ...

    @abstractmethod
    def search_issues(self, text: str) -> list[LinearIssue]: ...

    @abstractmethod
    def add_comment(self, issue_id: str, body: str) -> LinearComment: ...

    @abstractmethod
    def list_comments(self, issue_id: str) -> list[LinearComment]: ...

    @abstractmethod
    def add_label(self, issue_id: str, label_id: str) -> None: ...

    @abstractmethod
    def get_label_ids(self, names: list[str]) -> dict[str, str]:
        """Map label name -> id for the configured team. Missing labels are absent."""

    @abstractmethod
    def assign_issue(self, issue_id: str, user_id: str) -> None: ...

    @abstractmethod
    def resolve_issue(self, issue_id: str) -> None: ...

    @abstractmethod
    def label_events(self, issue_id: str) -> list[LabelEvent]: ...

    # --- configuration checks (used by `oncall-agent check`) ---
    def team_name(self) -> str:
        """Return 'KEY (Name)' of the configured team; raise IntegrationError if it does not exist."""
        return "mock team"

    def missing_users(self, user_ids: list[str]) -> list[str]:
        """Return the user ids that do not exist in this Linear workspace."""
        return []


class LLMClient(ABC):
    """Any LLM that can return the analysis JSON (Groq by default, Claude optional)."""

    mode: str = "real"
    provider: str = "llm"  # recorded as the analysis source, e.g. "groq"

    @abstractmethod
    def analyze(self, system_prompt: str, user_content: str, json_schema: dict[str, Any]) -> str:
        """Return the raw JSON text produced by the model."""


ClaudeClient = LLMClient  # name used by the optional Claude client
