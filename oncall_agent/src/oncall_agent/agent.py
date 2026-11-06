"""The orchestrator: one polling cycle = verify retries -> approvals ->
follow-ups -> detect -> process -> watermark -> audit."""
from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import signal
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

from .analysis import Analyzer
from .audit import AuditLogger, log_event
from .config import DagReposConfig, OwnersConfig, Settings
from .context import ContextCollector
from .detection import Detector
from .fingerprint import fingerprint
from .integrations import AirflowClient, AirflowUnavailable, IntegrationError, LinearUnavailable
from .models import (
    Analysis,
    AnalysisResult,
    Decision,
    Failure,
    FailureKind,
    IncidentStatus,
    utcnow,
    iso,
)
from .ownership import resolve_owner
from .policy import DecisionPolicy, RetryState
from .reporting import issue_description, issue_title, repeat_comment
from .store import Store, StoreError

if TYPE_CHECKING:
    from .approval_manager import ApprovalManager
    from .incident_manager import IncidentManager
    from .retry_manager import RetryManager


class AgentLockError(Exception):
    pass


class CycleLock:
    """Prevents two agent processes from running cycles against the same DB."""

    def __init__(self, db_path: str):
        self.path = (db_path if db_path != ":memory:" else "/tmp/oncall_agent_memory") + ".lock"
        self._fh: Optional[Any] = None

    def __enter__(self) -> "CycleLock":
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        self._fh = open(self.path, "w")
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._fh.close()
            raise AgentLockError(f"another oncall-agent process holds {self.path}") from exc
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._fh:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
            self._fh.close()


@dataclass
class CycleReport:
    started_at: str
    ok: bool = True
    detected: int = 0
    new_incidents: int = 0
    repeats: int = 0
    retries: dict[str, int] = field(default_factory=dict)
    approvals: dict[str, int] = field(default_factory=dict)
    watermark_advanced: bool = False
    errors: list[str] = field(default_factory=list)


class OncallAgent:
    def __init__(self, *, settings: Settings, store: Store, audit: AuditLogger, airflow: AirflowClient,
                 detector: Detector, collector: ContextCollector, analyzer: Analyzer, policy: DecisionPolicy,
                 retries: "RetryManager", approvals: "ApprovalManager", incidents: "IncidentManager",
                 owners: OwnersConfig, dag_repos: DagReposConfig):
        self.settings = settings
        self.store = store
        self.audit = audit
        self.airflow = airflow
        self.detector = detector
        self.collector = collector
        self.analyzer = analyzer
        self.policy = policy
        self.retries = retries
        self.approvals = approvals
        self.incidents = incidents
        self.owners = owners
        self.dag_repos = dag_repos

    # ==================================================================================
    # One polling cycle
    # ==================================================================================
    def run_cycle(self) -> CycleReport:
        report = CycleReport(started_at=iso(utcnow()) or "")
        try:
            self._cycle(report)
        except StoreError as exc:
            # SQLite is unavailable: fail safe. Nothing below a failed write is executed.
            report.ok = False
            report.errors.append(f"sqlite: {exc}")
            log_event("cycle_aborted_sqlite_unavailable", logging.ERROR, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - the loop must survive anything
            report.ok = False
            report.errors.append(f"unexpected: {exc!r}")
            logging.getLogger("oncall_agent").exception("cycle_crashed")
        try:
            self.audit.record("cycle", "polling_cycle", input={"started_at": report.started_at},
                              output={k: v for k, v in report.__dict__.items() if k != "errors"},
                              result="ok" if report.ok else "error",
                              error="; ".join(report.errors) if report.errors else None)
        except Exception:  # noqa: BLE001
            pass
        return report

    def _cycle(self, report: CycleReport) -> None:
        # 1. check previously scheduled retries
        report.retries = self.retries.verify_pending()
        # 2. pending approvals
        report.approvals = self.approvals.process_pending()
        # 3. follow up on open incidents (Linear outbox, human closures, vanished import errors)
        self.incidents.flush_pending()
        self.incidents.sync_open_incidents()
        self._resolve_fixed_import_errors()

        # 4. detect new failures
        try:
            detection = self.detector.detect()
        except (AirflowUnavailable, IntegrationError) as exc:
            report.ok = False
            report.errors.append(f"airflow: {exc}")
            self.audit.record("failure", "detection", result="error",
                              error=f"Airflow unavailable; watermark held: {exc}")
            return
        report.detected = len(detection.failures)

        # 5. process each failure
        all_ok = True
        for failure in detection.failures:
            try:
                outcome = self.process_failure(failure)
                if outcome == "new":
                    report.new_incidents += 1
                elif outcome == "repeat":
                    report.repeats += 1
            except StoreError:
                raise
            except Exception as exc:  # noqa: BLE001 - one bad failure must not block the rest
                all_ok = False
                report.errors.append(f"process {failure.occurrence_key}: {exc!r}")
                self.audit.record("failure", "process_failure", input=failure.summary(), result="error", error=repr(exc))
                logging.getLogger("oncall_agent").exception("process_failure_crashed")

        # 6. advance the watermark only when every detected failure was safely persisted
        if all_ok:
            self.store.set_watermark(iso(detection.cycle_start) or "",
                                     {"since": iso(detection.since), "detected": report.detected})
            report.watermark_advanced = True
        else:
            self.audit.record("failure", "watermark_held", result="error",
                              error="one or more failures could not be processed; will re-detect next cycle")

    # ==================================================================================
    # Processing one failure
    # ==================================================================================
    def process_failure(self, failure: Failure) -> str:
        """Returns 'new', 'repeat' or 'skipped'."""
        if self.store.occurrence_seen(failure.occurrence_key):
            return "skipped"
        self.collector.prepare(failure)  # fetch log tail -> error line for the fingerprint
        fp, normalized = fingerprint(failure.dag_id, failure.task_id, failure.error_message)

        existing = self.store.find_open_incident(fp)
        if existing is None:
            existing = self._adopt_linear_incident(fp, failure, normalized)
        if existing is not None:
            self._handle_repeat(existing, failure, fp)
            return "repeat"
        self._handle_new(failure, fp, normalized)
        return "new"

    def _adopt_linear_incident(self, fp: str, failure: Failure, normalized: str) -> Optional[dict[str, Any]]:
        """After a restart / DB loss, an open Linear issue for this fingerprint still means 'open incident'."""
        try:
            issue = self.incidents.find_open_issue_by_fingerprint(fp)
        except (LinearUnavailable, IntegrationError) as exc:
            self.audit.record("external_api", "linear_search_fingerprint", input=fp, result="error", error=exc)
            return None
        if issue is None:
            return None
        incident_id = self.store.create_incident(
            {"fingerprint": fp, "kind": failure.kind.value, "dag_id": failure.dag_id, "task_id": failure.task_id,
             "run_id": failure.run_id, "map_index": failure.map_index, "try_number": failure.try_number,
             "error": failure.error_message[:4000], "normalized_error": normalized,
             "status": IncidentStatus.OPEN.value, "linear_issue_id": issue.id,
             "linear_issue_identifier": issue.identifier, "decision": "adopted"},
            f"adopt:{fp}:{issue.id}", {"adopted_issue": issue.identifier})
        self.audit.record("detection", "adopt_existing_linear_issue", incident_id=incident_id,
                          input=fp, output=issue.identifier)
        return self.store.get_incident(incident_id)

    def _handle_repeat(self, inc: dict[str, Any], failure: Failure, fp: str) -> None:
        if not self.store.add_repeat_occurrence(failure.occurrence_key, fp, inc["id"], failure.summary()):
            return
        count = (inc.get("occurrence_count") or 1) + 1
        self.audit.record("detection", "repeat_occurrence", incident_id=inc["id"], input=failure.summary(),
                          output={"occurrence_count": count})
        log_event("duplicate_failure", incident_id=inc["id"], fingerprint=fp, occurrence=count)
        has_issue = inc.get("linear_issue_id") or self.store.has_pending_op(inc["id"], "create_issue")
        if has_issue:
            self.incidents.comment(inc["id"], repeat_comment(failure.summary(), failure.error_message, count),
                                   f"repeat:{failure.occurrence_key}")
            return
        if inc["status"] == IncidentStatus.RETRYING.value and failure.kind == FailureKind.TASK_INSTANCE:
            # Same transient error on another task instance while we are already retrying:
            # evaluate it with the same analysis and the same limits.
            self._decide_and_act_on_repeat(inc, failure)

    def _decide_and_act_on_repeat(self, inc: dict[str, Any], failure: Failure) -> None:
        analysis = Analysis.model_validate(inc["analysis_json"]) if inc.get("analysis_json") else None
        result = AnalysisResult(analysis, inc.get("analysis_source") or "unknown") if analysis else None
        decision = self.policy.decide(failure, result, self._retry_state(failure))
        self.audit.record("decision", "repeat_retry_policy", incident_id=inc["id"],
                          input=failure.summary(), output={"decision": decision.decision.value,
                                                           "reasons": decision.reasons})
        if decision.decision == Decision.RETRY:
            executed, reasons = self.retries.execute(inc["id"], failure, decision.reasons)
            if executed:
                return
            decision.reasons.extend(reasons)
        self.incidents.escalate(inc["id"], ["Repeat occurrence could not be retried automatically."]
                                + decision.reasons)

    def _retry_state(self, failure: Failure) -> RetryState:
        live_state: Optional[str] = None
        if failure.kind == FailureKind.TASK_INSTANCE and failure.run_id and failure.task_id:
            try:
                live = self.airflow.get_task_instance(failure.dag_id, failure.run_id, failure.task_id,
                                                      failure.map_index)
                live_state = live.state if live.try_number == failure.try_number else f"{live.state} (newer try)"
            except IntegrationError:
                live_state = None
        processing = False
        if failure.run_id and failure.task_id:
            processing = self.store.has_active_retry(failure.dag_id, failure.run_id, failure.task_id,
                                                     failure.map_index)
        return RetryState(
            task_retries_used=(self.store.count_task_retries(failure.dag_id, failure.run_id or "",
                                                             failure.task_id or "", failure.map_index)
                               if failure.task_id else 0),
            retries_last_hour=self.store.count_retries_last_hour(),
            already_processing=processing,
            live_task_state=live_state,
        )

    def _handle_new(self, failure: Failure, fp: str, normalized: str) -> None:
        previous = self.store.find_latest_incident(fp)
        incident_id = self.store.create_incident(
            {"fingerprint": fp, "kind": failure.kind.value, "dag_id": failure.dag_id, "task_id": failure.task_id,
             "run_id": failure.run_id, "map_index": failure.map_index, "try_number": failure.try_number,
             "error": failure.error_message[:4000], "normalized_error": normalized,
             "status": IncidentStatus.INVESTIGATING.value,
             "previous_incident_id": previous["id"] if previous else None},
            failure.occurrence_key, failure.summary())
        self.audit.record("detection", "new_failure", incident_id=incident_id, input=failure.summary(),
                          output={"fingerprint": fp, "normalized_error": normalized})
        log_event("failure_detected", incident_id=incident_id, kind=failure.kind.value, dag_id=failure.dag_id,
                  task_id=failure.task_id, fingerprint=fp[:16])

        # --- investigate ---
        ctx = self.collector.collect(failure)
        result = self.analyzer.analyze(ctx)
        a = result.analysis
        self.audit.record("analysis", f"analyze_{result.source}", incident_id=incident_id,
                          input={"gaps": ctx.gaps, "commits": [c.sha[:10] for c in ctx.commits]},
                          output=a.model_dump(mode="json") | {"notes": result.notes})
        suspected = ctx.commit_by_sha(a.suspected_commit)
        assignment = resolve_owner(failure.dag_id, suspected, self.owners, self.dag_repos)

        # --- decide ---
        state = self._retry_state(failure)
        decision = self.policy.decide(failure, result, state)
        self.audit.record("decision", "policy_decision", incident_id=incident_id,
                          input={"analysis": a.model_dump(mode="json"), "source": result.source,
                                 "state": state.__dict__},
                          output={"decision": decision.decision.value, "reasons": decision.reasons})
        log_event("retry_decision", incident_id=incident_id, decision=decision.decision.value,
                  reason="; ".join(decision.reasons))

        actions: list[str] = []
        approval_action = a.proposed_action.value if decision.decision == Decision.APPROVAL_REQUIRED else None
        prev_issue = previous.get("linear_issue_identifier") if previous else None
        title = issue_title({"kind": failure.kind.value, "dag_id": failure.dag_id, "task_id": failure.task_id,
                             "normalized_error": normalized})
        description = issue_description(
            {"fingerprint": fp}, ctx, result, decision, assignment, actions,
            previous_issue=prev_issue, approval_action=approval_action)
        self.store.update_incident(
            incident_id,
            classification=a.classification.value, confidence=a.confidence, root_cause=a.root_cause,
            suggested_fix=a.suggested_fix, proposed_action=a.proposed_action.value, analysis_source=result.source,
            analysis_json=a.model_dump(mode="json"), decision=decision.decision.value,
            decision_reasons=decision.reasons, context_gaps=ctx.gaps,
            assigned_developer=assignment.developer or assignment.linear_user_id,
            assignment_json=assignment.__dict__,
            issue_draft={"title": title, "description": description, "assignee_id": assignment.linear_user_id})

        # --- act ---
        if decision.decision == Decision.RETRY:
            executed, reasons = self.retries.execute(incident_id, failure, decision.reasons)
            if executed:
                return
            self.incidents.escalate(incident_id, ["Automatic retry was permitted but could not be executed."] + reasons)
            return

        if decision.decision == Decision.APPROVAL_REQUIRED:
            self.approvals.request(incident_id, a.proposed_action.value, decision.reasons)
            self.store.update_incident(incident_id, status=IncidentStatus.AWAITING_APPROVAL.value)
        else:
            self.store.update_incident(incident_id, status=IncidentStatus.OPEN.value)
        self.incidents.request_issue(incident_id, title, description, decision.decision.value,
                                     assignment.linear_user_id)

    def _resolve_fixed_import_errors(self) -> None:
        open_imports = [i for i in self.store.open_incidents() if i["kind"] == FailureKind.IMPORT_ERROR.value]
        if not open_imports:
            return
        try:
            current = {ie.filename for ie in self.airflow.list_import_errors()}
        except (AirflowUnavailable, IntegrationError):
            return
        for inc in open_imports:
            occ = self.store.occurrences_for_incident(inc["id"])
            files = {(json.loads(o["details_json"] or "{}")).get("filename") for o in occ}
            if files and not (files & current):
                self.incidents.resolve(inc["id"], "The DAG file no longer reports an import error in Airflow.")

    # ==================================================================================
    # Continuous loop
    # ==================================================================================
    async def run_forever(self) -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except (NotImplementedError, RuntimeError):
                pass
        log_event("agent_started", poll_interval_seconds=self.settings.poll_interval_seconds)
        while not stop.is_set():
            report = await asyncio.to_thread(self.run_cycle)
            log_event("cycle_complete", ok=report.ok, detected=report.detected, new=report.new_incidents,
                      repeats=report.repeats, watermark_advanced=report.watermark_advanced, errors=report.errors)
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.settings.poll_interval_seconds)
            except asyncio.TimeoutError:
                pass
        log_event("agent_stopped")
