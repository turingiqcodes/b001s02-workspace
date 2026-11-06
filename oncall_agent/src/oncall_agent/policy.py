"""Deterministic decision policy - the safety core.

The LLM's analysis (Groq) is an *input*. This module decides, using fixed rules, what
the agent is allowed to do. Every rule that is checked is reported back as a
reason (passed or failed) so the audit log explains the decision exactly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .config import DagReposConfig, Settings
from .models import (
    RISKY_ACTIONS,
    LLM_SOURCES,
    Analysis,
    AnalysisResult,
    Classification,
    Decision,
    Failure,
    FailureKind,
    PolicyResult,
    ProposedAction,
)

RETRY_ELIGIBLE_STATES = {"failed"}


@dataclass
class RetryState:
    """Facts about the world the policy needs, gathered by the caller."""

    task_retries_used: int            # automatic retries already spent on this task instance
    retries_last_hour: int            # automatic retries across all DAGs in the last hour
    already_processing: bool          # an active retry / approval exists for this task
    live_task_state: Optional[str]    # current state read from Airflow just now (None = unknown)


class DecisionPolicy:
    def __init__(self, settings: Settings, dag_repos: DagReposConfig):
        self.settings = settings
        self.dag_repos = dag_repos

    def retry_checks(self, failure: Failure, analysis: Optional[Analysis], state: RetryState,
                     *, require_analysis: bool = True) -> tuple[list[str], list[str]]:
        """Evaluate every automatic-retry condition. Returns (passed, failed) reason lists."""
        s = self.settings
        passed: list[str] = []
        failed: list[str] = []

        def check(ok: bool, good: str, bad: str) -> None:
            (passed if ok else failed).append(good if ok else bad)

        check(failure.kind == FailureKind.TASK_INSTANCE, "failure is a task instance failure",
              f"failure kind is {failure.kind.value}, only task instance failures can be auto-retried")
        if require_analysis:
            if analysis is None:
                failed.append("no valid analysis available")
            else:
                check(analysis.classification == Classification.RETRIABLE, "classification is retriable",
                      f"classification is {analysis.classification.value}, not retriable")
                check(analysis.confidence >= s.auto_retry_confidence_threshold,
                      f"confidence {analysis.confidence:.2f} >= {s.auto_retry_confidence_threshold}",
                      f"confidence {analysis.confidence:.2f} is below {s.auto_retry_confidence_threshold}")
                check(analysis.proposed_action == ProposedAction.RETRY_TASK, "proposed action is retry_task",
                      f"proposed action is {analysis.proposed_action.value}, not retry_task")
        check(s.max_auto_retries > 0, "automatic retries are enabled (ONCALL_MAX_AUTO_RETRIES > 0)",
              "automatic retries are disabled (ONCALL_MAX_AUTO_RETRIES=0)")
        check(state.task_retries_used < s.max_auto_retries,
              f"task has used {state.task_retries_used} of {s.max_auto_retries} automatic retries",
              f"task already used {state.task_retries_used} of {s.max_auto_retries} automatic retries")
        check(state.retries_last_hour < s.max_retries_per_hour,
              f"{state.retries_last_hour} automatic retries in the last hour (< {s.max_retries_per_hour})",
              f"hourly retry limit reached ({state.retries_last_hour}/{s.max_retries_per_hour})")
        check(not self.dag_repos.is_no_retry(failure.dag_id), "DAG is not on the no-retry list",
              f"DAG {failure.dag_id} is on the no-retry list")
        check(not state.already_processing, "failure is not already being processed",
              "failure is already being processed (active retry or approval)")
        check(state.live_task_state in RETRY_ELIGIBLE_STATES,
              f"live task state is {state.live_task_state}",
              f"live task state is {state.live_task_state!r}, not eligible for retry")
        return passed, failed

    def decide(self, failure: Failure, result: Optional[AnalysisResult], state: RetryState) -> PolicyResult:
        analysis = result.analysis if result else None

        # 1. Risky actions are never automatic.
        if analysis is not None and analysis.proposed_action in RISKY_ACTIONS:
            action = analysis.proposed_action.value
            if failure.kind == FailureKind.IMPORT_ERROR:
                return PolicyResult(Decision.NEEDS_HUMAN, [
                    f"proposed action {action} does not apply to an import error"])
            return PolicyResult(Decision.APPROVAL_REQUIRED, [
                f"proposed action {action} is a risky action and requires human approval",
                f"analysis: {analysis.classification.value} (confidence {analysis.confidence:.2f})"])

        # 2. Clearly non-retriable failures.
        if analysis is not None and analysis.classification == Classification.NON_RETRIABLE:
            reasons = ["classification is non_retriable"]
            if result is not None and result.source not in LLM_SOURCES:
                reasons.append("classified by the rule-based analyzer (LLM unavailable)")
            return PolicyResult(Decision.NON_RETRIABLE, reasons)

        # 3. Automatic retry only if every condition passes.
        passed, failed = self.retry_checks(failure, analysis, state)
        if result is not None and result.source not in LLM_SOURCES:
            failed.append("analysis came from the rule-based fallback, which never triggers automatic retries")
        if not failed:
            return PolicyResult(Decision.RETRY, ["all automatic retry conditions passed"] + passed, passed)
        return PolicyResult(Decision.NEEDS_HUMAN, failed, passed)
