"""Collects everything needed to investigate a failure.

Each source is fetched independently. A failure in one source is recorded as a
"context gap" and never stops the rest of the investigation.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Callable, Optional, TypeVar

from .config import DagReposConfig
from .fingerprint import pick_error_line
from .integrations import AirflowClient, GitHubClient
from .models import CommitInfo, FailureContext, Failure, FailureKind, iso, utcnow

T = TypeVar("T")

LOG_TAIL_LINES = 200
COMMIT_LOOKBACK_DAYS = 14
COMMITS_PER_PATH = 5
MAX_COMMITS = 6
PATCHES_TO_FETCH = 3


class ContextCollector:
    def __init__(self, airflow: AirflowClient, github: Optional[GitHubClient], dag_repos: DagReposConfig):
        self.airflow = airflow
        self.github = github
        self.dag_repos = dag_repos

    @staticmethod
    def _try(ctx: FailureContext, source: str, fn: Callable[[], T], default: T) -> T:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 - a gap, not a crash
            ctx.gaps.append(f"{source}: {exc}")
            return default

    def prepare(self, failure: Failure) -> None:
        """Fetch the cheap data needed to fingerprint a failure (the task log tail).

        Results are cached on ``failure.details`` so ``collect`` does not fetch twice.
        """
        if failure.details.get("prepared"):
            return
        failure.details["prepared"] = True
        gaps: list[str] = failure.details.setdefault("gaps", [])
        if failure.kind == FailureKind.TASK_INSTANCE:
            try:
                log = self.airflow.get_task_log(failure.dag_id, failure.run_id or "", failure.task_id or "",
                                                failure.try_number, failure.map_index)
            except Exception as exc:  # noqa: BLE001
                gaps.append(f"task_log: {exc}")
                log = ""
            tail = "\n".join(log.splitlines()[-LOG_TAIL_LINES:])
            failure.details["log_tail"] = tail
            if not failure.error_message:
                failure.error_message = pick_error_line(tail) or f"Task {failure.task_id} failed (no log available)"

    def collect(self, failure: Failure) -> FailureContext:
        self.prepare(failure)
        ctx = FailureContext(failure=failure)

        # ---- Airflow ----
        ctx.gaps.extend(failure.details.get("gaps", []))
        ctx.log_tail = failure.details.get("log_tail", "")
        ctx.error_message = failure.error_message
        known_dag = failure.kind != FailureKind.IMPORT_ERROR or failure.dag_id in self.dag_repos.dags
        if known_dag:
            ctx.dag_details = self._try(ctx, "dag_details", lambda: self.airflow.get_dag_details(failure.dag_id), {})
            ctx.dag_source = self._try(ctx, "dag_source", lambda: self.airflow.get_dag_source(failure.dag_id), "")
            runs = self._try(ctx, "recent_runs", lambda: self.airflow.list_recent_dag_runs(failure.dag_id, 10), [])
            ctx.recent_runs = [{"run_id": r.run_id, "state": r.state, "start_date": iso(r.start_date),
                                "end_date": iso(r.end_date), "run_type": r.run_type} for r in runs]
        else:
            ctx.gaps.append("dag_details/source/runs: import error file is not mapped to a configured DAG")

        # ---- GitHub ----
        entry = self.dag_repos.entry(failure.dag_id)
        if entry is None:
            ctx.gaps.append(f"github: dag {failure.dag_id!r} has no entry in dag_repos.json")
        elif self.github is None:
            ctx.gaps.append("github: client not configured")
        else:
            ctx.repo, ctx.dag_file_path = entry.repo, entry.dag_file_path
            ctx.commits = self._collect_commits(ctx, entry.repo, [entry.dag_file_path, *entry.extra_paths])
        return ctx

    def _collect_commits(self, ctx: FailureContext, repo: str, paths: list[str]) -> list[CommitInfo]:
        assert self.github is not None
        since = utcnow() - timedelta(days=COMMIT_LOOKBACK_DAYS)
        by_sha: dict[str, CommitInfo] = {}
        for path in paths:
            commits = self._try(ctx, f"github_commits[{path}]",
                                lambda p=path: self.github.list_commits(repo, p, since=since, limit=COMMITS_PER_PATH), [])
            for c in commits:
                by_sha.setdefault(c.sha, c)
        commits = sorted(by_sha.values(), key=lambda c: c.timestamp or utcnow(), reverse=True)[:MAX_COMMITS]
        relevant = tuple(p.rstrip("/") for p in paths)
        for i, c in enumerate(commits[:PATCHES_TO_FETCH]):
            full = self._try(ctx, f"github_patch[{c.sha[:8]}]", lambda sha=c.sha: self.github.get_commit(repo, sha), None)
            if full is None:
                continue
            # keep only the parts of the patch that touch relevant paths
            c.files = full.files or c.files
            if full.patch:
                chunks = full.patch.split("--- ")
                kept = [ch for ch in chunks if ch.strip() and ch.split("\n", 1)[0].strip().startswith(relevant)]
                c.patch = ("--- " + "--- ".join(kept))[:4000] if kept else full.patch[:4000]
            commits[i] = c
        return commits
