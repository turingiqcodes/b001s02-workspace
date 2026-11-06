"""Shared data models used across the agent."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def parse_dt(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------------------
# Enums (str-based so they serialise cleanly into SQLite / JSON)
# ---------------------------------------------------------------------------------------
class FailureKind(str, Enum):
    TASK_INSTANCE = "task_instance"
    DAG_RUN = "dag_run"
    IMPORT_ERROR = "import_error"


class Classification(str, Enum):
    RETRIABLE = "retriable"
    NON_RETRIABLE = "non_retriable"
    UNCERTAIN = "uncertain"


class ProposedAction(str, Enum):
    RETRY_TASK = "retry_task"
    RERUN_DAG = "rerun_dag"
    MARK_SUCCESS = "mark_success"
    NO_ACTION = "no_action"


RISKY_ACTIONS = {ProposedAction.RERUN_DAG, ProposedAction.MARK_SUCCESS}


class Decision(str, Enum):
    RETRY = "retry"
    NON_RETRIABLE = "non_retriable"
    NEEDS_HUMAN = "needs_human"
    APPROVAL_REQUIRED = "approval_required"


class IncidentStatus(str, Enum):
    INVESTIGATING = "investigating"      # detected, analysis in progress
    RETRYING = "retrying"                # automatic retry requested, being verified
    OPEN = "open"                        # Linear issue (or pending issue) waiting for a human
    AWAITING_APPROVAL = "awaiting_approval"
    VERIFYING = "verifying"              # approved action executed, watching the result
    RESOLVED = "resolved"


# ---------------------------------------------------------------------------------------
# Airflow objects (normalised from the REST API)
# ---------------------------------------------------------------------------------------
@dataclass
class TaskInstanceInfo:
    dag_id: str
    run_id: str
    task_id: str
    state: Optional[str]
    try_number: int = 0
    map_index: int = -1
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    operator: Optional[str] = None


@dataclass
class DagRunInfo:
    dag_id: str
    run_id: str
    state: Optional[str]
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    logical_date: Optional[datetime] = None
    run_type: Optional[str] = None
    note: Optional[str] = None


@dataclass
class ImportErrorInfo:
    import_error_id: Any
    filename: str
    stack_trace: str
    timestamp: Optional[datetime] = None
    bundle_name: Optional[str] = None


@dataclass
class Failure:
    """One detected failure occurrence."""

    kind: FailureKind
    dag_id: str
    run_id: Optional[str] = None
    task_id: Optional[str] = None
    try_number: int = 0
    map_index: int = -1
    state: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    error_message: str = ""
    filename: Optional[str] = None  # import errors
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def occurrence_key(self) -> str:
        """Identifies this exact occurrence; used to never process it twice."""
        if self.kind == FailureKind.TASK_INSTANCE:
            return f"ti:{self.dag_id}:{self.run_id}:{self.task_id}:{self.map_index}:{self.try_number}"
        if self.kind == FailureKind.DAG_RUN:
            return f"run:{self.dag_id}:{self.run_id}"
        import hashlib

        digest = hashlib.sha256(self.error_message.encode()).hexdigest()[:16]
        return f"import:{self.filename}:{digest}"

    def summary(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "dag_id": self.dag_id,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "try_number": self.try_number,
            "map_index": self.map_index,
            "state": self.state,
            "filename": self.filename,
            "start_date": iso(self.start_date),
            "end_date": iso(self.end_date),
        }


# ---------------------------------------------------------------------------------------
# GitHub / context
# ---------------------------------------------------------------------------------------
@dataclass
class CommitInfo:
    sha: str
    author_login: Optional[str]
    author_name: Optional[str]
    message: str
    timestamp: Optional[datetime]
    url: Optional[str] = None
    patch: Optional[str] = None
    files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "sha": self.sha,
            "author_login": self.author_login,
            "author_name": self.author_name,
            "message": self.message,
            "timestamp": iso(self.timestamp),
            "url": self.url,
            "files": self.files,
            "patch": self.patch,
        }


@dataclass
class FailureContext:
    failure: Failure
    error_message: str = ""
    log_tail: str = ""
    dag_details: dict[str, Any] = field(default_factory=dict)
    dag_source: str = ""
    recent_runs: list[dict[str, Any]] = field(default_factory=list)
    repo: Optional[str] = None
    dag_file_path: Optional[str] = None
    commits: list[CommitInfo] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)

    def commit_by_sha(self, sha: Optional[str]) -> Optional[CommitInfo]:
        if not sha:
            return None
        for c in self.commits:
            if c.sha == sha or (len(sha) >= 7 and c.sha.startswith(sha)):
                return c
        return None


# ---------------------------------------------------------------------------------------
# Analysis (validated output of the LLM (Groq) or the rule-based analyzer)
# ---------------------------------------------------------------------------------------
class Analysis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    classification: Classification
    confidence: float = Field(ge=0.0, le=1.0)
    root_cause: str = Field(min_length=1, max_length=2000)
    evidence: list[str] = Field(max_length=20)
    suggested_fix: str = Field(min_length=1, max_length=2000)
    proposed_action: ProposedAction
    suspected_commit: Optional[str] = None

    @field_validator("evidence")
    @classmethod
    def _evidence_items(cls, v: list[str]) -> list[str]:
        return [str(item)[:1000] for item in v]


@dataclass
class AnalysisResult:
    analysis: Analysis
    source: str  # "groq" | "claude" | "rule_based"
    notes: list[str] = field(default_factory=list)


# Analysis sources that come from a real LLM. Only these can ever lead to an automatic retry.
LLM_SOURCES = frozenset({"groq", "claude"})

# JSON schema of the analysis (given to the LLM; kept in sync with Analysis above).
ANALYSIS_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "classification": {"type": "string", "enum": [c.value for c in Classification]},
        "confidence": {"type": "number"},
        "root_cause": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "suggested_fix": {"type": "string"},
        "proposed_action": {"type": "string", "enum": [a.value for a in ProposedAction]},
        "suspected_commit": {"type": ["string", "null"]},
    },
    "required": [
        "classification",
        "confidence",
        "root_cause",
        "evidence",
        "suggested_fix",
        "proposed_action",
        "suspected_commit",
    ],
    "additionalProperties": False,
}


@dataclass
class PolicyResult:
    decision: Decision
    reasons: list[str]
    passed_checks: list[str] = field(default_factory=list)


@dataclass
class OwnerAssignment:
    linear_user_id: str
    developer: Optional[str]
    source: str  # commit_author | dag_owner | default_owner
    reason: str


# ---------------------------------------------------------------------------------------
# Linear objects
# ---------------------------------------------------------------------------------------
@dataclass
class LinearIssue:
    id: str
    identifier: str
    title: str
    description: str = ""
    state_type: str = "unstarted"  # Linear workflow state type: triage/backlog/unstarted/started/completed/canceled
    labels: list[str] = field(default_factory=list)
    assignee_id: Optional[str] = None
    priority: int = 0
    url: Optional[str] = None

    @property
    def is_open(self) -> bool:
        return self.state_type not in {"completed", "canceled"}


@dataclass
class LinearComment:
    id: str
    body: str
    author_id: Optional[str]
    created_at: datetime


@dataclass
class LabelEvent:
    """A label being added to an issue, with who added it (from issue history)."""

    id: str
    label_name: str
    actor_id: Optional[str]
    created_at: datetime
