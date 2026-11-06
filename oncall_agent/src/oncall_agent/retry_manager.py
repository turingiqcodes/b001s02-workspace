"""Executes permitted automatic retries and verifies their outcome.

Safety sequence for every retry (all steps are audited):

1. Re-read the live task state: it must still be ``failed`` with the same try.
2. Re-check every limit (per-task, per-hour, kill switch, no-retry list).
3. Insert the retry row (``status=executing``). The UNIQUE key on
   (dag, run, task, map_index, try) makes this the idempotency gate: a second
   attempt for the same failed try - even after a restart - is refused.
4. Dry-run the clear and verify it touches only failed / upstream_failed task
   instances (never a successful one).
5. Execute the clear (``only_failed=True, include_downstream=True``).
6. Record the API result and move the row to ``requested``.

If the agent crashes between 3 and 6, the row is left in ``executing``. On the
next cycle it is reconciled by reading the live state - it is never blindly
re-executed.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Optional

from .audit import AuditLogger, log_event
from .config import Settings
from .incident_manager import IncidentManager
from .integrations import AirflowClient, AirflowUnavailable, IntegrationError
from .models import Analysis, AnalysisResult, Failure, FailureKind, IncidentStatus, iso, parse_dt, utcnow
from .policy import DecisionPolicy, RetryState
from .store import Store, StoreError

PENDING_STATES = {None, "none", "queued", "scheduled", "running", "up_for_retry", "up_for_reschedule",
                  "restarting", "deferred"}
CLEARABLE_STATES = {"failed", "upstream_failed"}


class RetryManager:
    def __init__(self, airflow: AirflowClient, store: Store, audit: AuditLogger, settings: Settings,
                 policy: DecisionPolicy, incidents: IncidentManager):
        self.airflow = airflow
        self.store = store
        self.audit = audit
        self.settings = settings
        self.policy = policy
        self.incidents = incidents

    # ---- execute ----------------------------------------------------------------------
    def execute(self, incident_id: str, failure: Failure, decision_reasons: list[str]) -> tuple[bool, list[str]]:
        """Run one automatic retry. Returns (executed, reasons)."""
        if failure.kind != FailureKind.TASK_INSTANCE or not (failure.run_id and failure.task_id):
            return False, ["only task instance failures can be retried"]
        dag_id, run_id, task_id, mi = failure.dag_id, failure.run_id, failure.task_id, failure.map_index

        try:
            live = self.airflow.get_task_instance(dag_id, run_id, task_id, mi)
        except IntegrationError as exc:
            return False, [f"could not verify live task state: {exc}"]
        if live.state != "failed" or live.try_number != failure.try_number:
            return False, [f"task state changed since detection (now {live.state}, try {live.try_number})"]

        state = RetryState(
            task_retries_used=self.store.count_task_retries(dag_id, run_id, task_id, mi),
            retries_last_hour=self.store.count_retries_last_hour(),
            already_processing=self.store.has_active_retry(dag_id, run_id, task_id, mi),
            live_task_state=live.state,
        )
        _, failed = self.policy.retry_checks(failure, None, state, require_analysis=False)
        if failed:
            return False, failed

        retry_id = self.store.create_retry(incident_id, dag_id, run_id, task_id, mi, failure.try_number,
                                           decision_reasons)
        if retry_id is None:
            return False, ["this failed try was already retried (idempotency guard)"]
        target = {"dag_id": dag_id, "run_id": run_id, "task_id": task_id, "map_index": mi,
                  "try_number": failure.try_number}
        try:
            self.audit.record("retry", "retry_requested", incident_id=incident_id, input=target,
                              output={"retry_id": retry_id}, strict=True)
        except StoreError:
            self.store.update_retry(retry_id, status="aborted", verification_status="n/a")
            raise

        # Dry run: make sure we only clear failed / upstream_failed task instances.
        try:
            preview = self.airflow.clear_task_instances(dag_id, run_id, [task_id], dry_run=True, only_failed=True,
                                                        include_downstream=True, map_indexes=[mi])
        except IntegrationError as exc:
            self.store.update_retry(retry_id, status="aborted", verification_status="n/a",
                                    result_json={"error": str(exc), "stage": "dry_run"})
            self.audit.record("external_api", "airflow_clear_dry_run", incident_id=incident_id, input=target,
                              result="error", error=exc)
            return False, [f"dry-run clear failed: {exc}"]
        unsafe = [f"{t.task_id}={t.state}" for t in preview if t.state not in CLEARABLE_STATES]
        if unsafe or not any(t.task_id == task_id for t in preview):
            reason = (f"dry-run would clear non-failed task instances: {unsafe}" if unsafe
                      else "dry-run did not include the failed task")
            self.store.update_retry(retry_id, status="aborted", verification_status="n/a",
                                    result_json={"preview": [vars(t) for t in preview], "reason": reason})
            self.audit.record("retry", "retry_aborted", incident_id=incident_id, input=target, result="skipped",
                              error=reason)
            return False, [reason]

        try:
            cleared = self.airflow.clear_task_instances(dag_id, run_id, [task_id], dry_run=False, only_failed=True,
                                                        include_downstream=True, map_indexes=[mi])
        except IntegrationError as exc:
            # The clear may or may not have happened: keep the row (status=error) so it is
            # reconciled from live state next cycle and never re-sent blindly.
            self.store.update_retry(retry_id, status="error", result_json={"error": str(exc)})
            self.audit.record("external_api", "airflow_clear_task_instances", incident_id=incident_id, input=target,
                              result="error", error=exc)
            self.store.update_incident(incident_id, status=IncidentStatus.RETRYING.value)
            return True, ["clear request returned an error; outcome will be reconciled from live state"]

        cleared_summary = [f"{t.task_id}[{t.map_index}]" for t in cleared]
        self.store.update_retry(retry_id, status="requested", result_json={"cleared": cleared_summary})
        self.store.update_incident(incident_id, status=IncidentStatus.RETRYING.value)
        self.store.append_incident_action(incident_id, f"automatic retry #{self.store.get_retry(retry_id)['attempt']} "
                                                       f"cleared {', '.join(cleared_summary)}")
        self.audit.record("external_api", "airflow_clear_task_instances", incident_id=incident_id, input=target,
                          output={"cleared": cleared_summary})
        log_event("retry_executed", incident_id=incident_id, retry_id=retry_id, cleared=cleared_summary)
        return True, ["retry executed"]

    # ---- verify -----------------------------------------------------------------------
    def verify_pending(self) -> dict[str, int]:
        stats = {"succeeded": 0, "failed": 0, "timed_out": 0, "pending": 0, "interrupted": 0}
        for retry in self.store.active_retries():
            try:
                outcome = self._verify_one(retry)
            except AirflowUnavailable as exc:
                self.audit.record("failure", "retry_verification", incident_id=retry["incident_id"], result="error",
                                  error=f"Airflow unavailable; will verify next cycle: {exc}")
                break
            stats[outcome] = stats.get(outcome, 0) + 1
        return stats

    def _verify_one(self, retry: dict[str, Any]) -> str:
        incident_id = retry["incident_id"]
        live = self.airflow.get_task_instance(retry["dag_id"], retry["run_id"], retry["task_id"], retry["map_index"])
        before = retry["try_number_before"]

        if retry["status"] in ("executing", "error") and live.state == "failed" and live.try_number == before:
            age = utcnow() - (parse_dt(retry["executed_at"]) or utcnow())
            if retry["status"] == "error" and age < timedelta(minutes=2):
                return "pending"  # give Airflow a moment before concluding nothing happened
            # The clear never took effect. Do NOT re-send it automatically.
            self.store.update_retry(retry["id"], status="interrupted", verification_status="n/a",
                                    verified_at=iso(utcnow()))
            self.audit.record("retry", "retry_interrupted", incident_id=incident_id, input=retry["id"],
                              result="failed", error="retry was interrupted before Airflow applied it")
            self.incidents.escalate(incident_id, [
                "An automatic retry was interrupted (agent restart or API error) and Airflow shows it never ran.",
                "The agent does not re-send interrupted retries automatically, to avoid duplicate execution."])
            return "interrupted"

        if live.state == "success":
            self.store.update_retry(retry["id"], status="succeeded", verification_status="verified_success",
                                    verified_at=iso(utcnow()))
            self.audit.record("retry", "retry_verified_success", incident_id=incident_id,
                              input={"retry_id": retry["id"]}, output={"state": live.state, "try": live.try_number})
            self.store.append_incident_action(incident_id, f"retry attempt {retry['attempt']} succeeded")
            if not any(r["status"] in ("executing", "requested", "error")
                       for r in self.store.retries_for_incident(incident_id) if r["id"] != retry["id"]):
                self.incidents.resolve(incident_id, f"Automatic retry succeeded (try {live.try_number}).")
            return "succeeded"

        if live.state in ("failed", "upstream_failed") and live.try_number > before:
            self.store.update_retry(retry["id"], status="failed", verification_status="verified_failure",
                                    verified_at=iso(utcnow()))
            self.audit.record("retry", "retry_verified_failure", incident_id=incident_id,
                              input={"retry_id": retry["id"]}, output={"state": live.state, "try": live.try_number})
            self.store.append_incident_action(incident_id, f"retry attempt {retry['attempt']} failed again")
            self._after_failed_retry(retry, live)
            return "failed"

        executed_at = parse_dt(retry["executed_at"]) or utcnow()
        if utcnow() - executed_at > timedelta(minutes=self.settings.retry_timeout_minutes):
            self.store.update_retry(retry["id"], status="timed_out", verification_status="timed_out",
                                    verified_at=iso(utcnow()))
            self.audit.record("retry", "retry_timed_out", incident_id=incident_id, input={"retry_id": retry["id"]},
                              output={"state": live.state}, result="failed")
            self.incidents.escalate(incident_id, [
                f"Retried task is still '{live.state}' after {self.settings.retry_timeout_minutes} minutes."])
            return "timed_out"
        if retry["status"] in ("executing", "error"):
            self.store.update_retry(retry["id"], status="requested")  # Airflow did pick it up
        return "pending"

    def _after_failed_retry(self, retry: dict[str, Any], live: Any) -> None:
        incident_id = retry["incident_id"]
        inc = self.store.get_incident(incident_id) or {}
        failure = Failure(kind=FailureKind.TASK_INSTANCE, dag_id=retry["dag_id"], run_id=retry["run_id"],
                          task_id=retry["task_id"], map_index=retry["map_index"], try_number=live.try_number,
                          state=live.state)
        # This new failed try belongs to the incident; detection must not treat it as new.
        self.store.mark_occurrence_seen(failure.occurrence_key, inc.get("fingerprint", ""), incident_id,
                                        failure.summary())
        analysis: Optional[Analysis] = None
        if inc.get("analysis_json"):
            try:
                analysis = Analysis.model_validate(inc["analysis_json"])
            except ValueError:
                analysis = None
        result = AnalysisResult(analysis, inc.get("analysis_source") or "unknown") if analysis else None
        state = RetryState(
            task_retries_used=self.store.count_task_retries(retry["dag_id"], retry["run_id"], retry["task_id"],
                                                            retry["map_index"]),
            retries_last_hour=self.store.count_retries_last_hour(),
            already_processing=False,
            live_task_state=live.state,
        )
        decision = self.policy.decide(failure, result, state)
        self.audit.record("decision", "retry_policy_after_failed_retry", incident_id=incident_id,
                          input=state.__dict__, output={"decision": decision.decision.value,
                                                        "reasons": decision.reasons})
        log_event("retry_decision", incident_id=incident_id, decision=decision.decision.value,
                  reason="; ".join(decision.reasons))
        if decision.decision.value == "retry":
            executed, reasons = self.execute(incident_id, failure, decision.reasons)
            if executed:
                return
            self.incidents.escalate(incident_id, ["Retry failed again and another retry was not possible."] + reasons)
            return
        self.incidents.escalate(incident_id, ["Automatic retry failed and no further automatic retry is allowed."]
                                + decision.reasons)
