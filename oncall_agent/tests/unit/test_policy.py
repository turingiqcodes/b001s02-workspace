import pytest

from oncall_agent.config import DagReposConfig, Settings
from oncall_agent.models import Analysis, AnalysisResult, Decision, Failure, FailureKind
from oncall_agent.policy import DecisionPolicy, RetryState

from tests.conftest import RETRIABLE

REPOS = DagReposConfig.model_validate({
    "no_retry_dags": ["payments_dag"],
    "dags": {"flagged_dag": {"repo": "o/r", "dag_file_path": "dags/f.py", "no_retry": True}},
})


def failure(dag="d", kind=FailureKind.TASK_INSTANCE):
    return Failure(kind=kind, dag_id=dag, run_id="r", task_id="t" if kind == FailureKind.TASK_INSTANCE else None,
                   try_number=1)


def result(source="groq", **overrides):
    return AnalysisResult(Analysis.model_validate({**RETRIABLE, **overrides}), source)


def ok_state(**kw):
    base = dict(task_retries_used=0, retries_last_hour=0, already_processing=False, live_task_state="failed")
    return RetryState(**{**base, **kw})


def policy(**settings):
    return DecisionPolicy(Settings(**settings), REPOS)


def test_retry_when_all_conditions_pass():
    d = policy().decide(failure(), result(), ok_state())
    assert d.decision == Decision.RETRY
    assert "all automatic retry conditions passed" in d.reasons


@pytest.mark.parametrize("case, kwargs, state, expected_reason", [
    ("low confidence", {"confidence": 0.69}, {}, "below 0.7"),
    ("uncertain", {"classification": "uncertain"}, {}, "not retriable"),
    ("no_action", {"proposed_action": "no_action"}, {}, "not retry_task"),
    ("task limit", {}, {"task_retries_used": 2}, "already used 2 of 2"),
    ("hourly limit", {}, {"retries_last_hour": 10}, "hourly retry limit"),
    ("in progress", {}, {"already_processing": True}, "already being processed"),
    ("state changed", {}, {"live_task_state": "success"}, "not eligible"),
    ("state unknown", {}, {"live_task_state": None}, "not eligible"),
])
def test_any_failed_condition_blocks_retry(case, kwargs, state, expected_reason):
    d = policy().decide(failure(), result(**kwargs), ok_state(**state))
    assert d.decision == Decision.NEEDS_HUMAN, case
    assert any(expected_reason in r for r in d.reasons), (case, d.reasons)


def test_confidence_exactly_threshold_is_allowed():
    assert policy().decide(failure(), result(confidence=0.7), ok_state()).decision == Decision.RETRY


@pytest.mark.parametrize("dag", ["payments_dag", "flagged_dag"])
def test_no_retry_dags(dag):
    d = policy().decide(failure(dag), result(), ok_state())
    assert d.decision == Decision.NEEDS_HUMAN and any("no-retry" in r for r in d.reasons)


def test_max_auto_retries_zero_disables_everything():
    d = policy(max_auto_retries=0).decide(failure(), result(), ok_state())
    assert d.decision == Decision.NEEDS_HUMAN and any("disabled" in r for r in d.reasons)


def test_dag_run_failures_are_never_auto_retried():
    d = policy().decide(failure(kind=FailureKind.DAG_RUN), result(), ok_state(live_task_state=None))
    assert d.decision == Decision.NEEDS_HUMAN and any("dag_run" in r for r in d.reasons)


def test_rule_based_analysis_never_retries_even_if_confident():
    d = policy().decide(failure(), result(source="rule_based"), ok_state())
    assert d.decision == Decision.NEEDS_HUMAN


@pytest.mark.parametrize("action", ["rerun_dag", "mark_success"])
def test_risky_actions_require_approval_even_with_full_confidence(action):
    d = policy().decide(failure(), result(proposed_action=action, confidence=1.0), ok_state())
    assert d.decision == Decision.APPROVAL_REQUIRED


def test_non_retriable_and_missing_analysis():
    assert policy().decide(failure(), result(classification="non_retriable", proposed_action="no_action"),
                           ok_state()).decision == Decision.NON_RETRIABLE
    assert policy().decide(failure(), None, ok_state()).decision == Decision.NEEDS_HUMAN


def test_unknown_analysis_source_never_retries():
    assert policy().decide(failure(), result(source="unknown"), ok_state()).decision == Decision.NEEDS_HUMAN
    assert policy().decide(failure(), result(source="claude"), ok_state()).decision == Decision.RETRY
