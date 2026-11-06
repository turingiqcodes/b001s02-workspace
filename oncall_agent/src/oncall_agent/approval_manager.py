"""Human approval workflow for risky actions (rerun_dag, mark_success).

How approval works
------------------
* A pending approval row is created together with the Linear issue.
* Each cycle the agent reads the issue's comments and label history.
  A signal is valid only if:
    - it is the exact comment ``/approve`` OR the label ``oncall:approved``,
    - it was made after the approval was requested,
    - its author/actor is in ``owners.json -> approvers``.
  Every signal is evaluated once and recorded (accepted or ignored).
* ``Store.claim_approval`` atomically flips pending -> executing. Only one
  caller can win, so the action executes at most once, even with repeated
  polling, restarts or a concurrent MCP call.
* If the agent dies while an approval is ``executing``, it is marked
  ``interrupted`` on the next cycle and handed to a human - never re-run.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Optional

from .audit import AuditLogger, log_event
from .config import OwnersConfig, Settings
from .incident_manager import IncidentManager
from .integrations import AirflowClient, AirflowUnavailable, IntegrationError, LinearClient, LinearUnavailable
from .models import IncidentStatus, ProposedAction, iso, parse_dt, utcnow
from .reporting import APPROVE_COMMAND, APPROVED_LABEL
from .store import Store, StoreError


class ApprovalManager:
    def __init__(self, airflow: AirflowClient, linear: LinearClient, store: Store, audit: AuditLogger,
                 owners: OwnersConfig, incidents: IncidentManager, settings: Settings):
        self.airflow = airflow
        self.linear = linear
        self.store = store
        self.audit = audit
        self.owners = owners
        self.incidents = incidents
        self.settings = settings

    def request(self, incident_id: str, action: str, reasons: list[str]) -> str:
        approval_id = self.store.create_approval(incident_id, None, action, reasons)
        self.audit.record("approval", "approval_requested", incident_id=incident_id,
                          input={"action": action, "reasons": reasons}, output={"approval_id": approval_id})
        return approval_id

    # ---- polling ----------------------------------------------------------------------
    def process_pending(self) -> dict[str, int]:
        stats = {"executed": 0, "waiting": 0, "interrupted": 0}
        for appr in self.store.approvals_with_status("executing"):
            self.store.finish_approval(appr["id"], "interrupted", {"reason": "agent stopped during execution"})
            self.audit.record("approval", "approval_interrupted", incident_id=appr["incident_id"],
                              input=appr["id"], result="failed",
                              error="execution was interrupted; not re-run automatically")
            self.incidents.escalate(appr["incident_id"], [
                f"Approved action `{appr['action']}` was interrupted while executing. "
                "Check Airflow manually; the agent will not run it again."])
            stats["interrupted"] += 1

        for appr in self.store.approvals_with_status("pending"):
            if not appr.get("linear_issue_id"):
                stats["waiting"] += 1
                continue
            try:
                signal = self.find_valid_signal(appr)
            except LinearUnavailable as exc:
                self.audit.record("failure", "approval_poll", incident_id=appr["incident_id"], result="error",
                                  error=f"Linear unavailable: {exc}")
                break
            if signal is None:
                stats["waiting"] += 1
                continue
            if self.execute(appr["id"], signal["source"], signal["actor"]):
                stats["executed"] += 1
        self._follow_up_verifying()
        return stats

    def find_valid_signal(self, appr: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Return the first authorised approval signal, recording every signal evaluated."""
        issue_id = appr["linear_issue_id"]
        requested_at = parse_dt(appr["requested_at"]) or utcnow()
        approvers = set(self.owners.approvers)
        signals: list[dict[str, Any]] = []
        for c in self.linear.list_comments(issue_id):
            if c.body.strip() == APPROVE_COMMAND:
                signals.append({"id": f"comment:{c.id}", "source": "comment", "actor": c.author_id,
                                "at": c.created_at})
        for e in self.linear.label_events(issue_id):
            if e.label_name == APPROVED_LABEL:
                signals.append({"id": f"label:{e.id}", "source": "label", "actor": e.actor_id, "at": e.created_at})
        signals.sort(key=lambda s: s["at"])

        for s in signals:
            if s["at"] < requested_at - timedelta(seconds=5):
                ok, why = False, "signal predates the approval request"
            elif not s["actor"] or s["actor"] not in approvers:
                ok, why = False, f"user {s['actor']!r} is not an authorised approver"
            else:
                ok, why = True, f"approved by {s['actor']} via {s['source']}"
            if self.store.record_signal(s["id"], appr["id"], ok, why):
                self.audit.record("approval", "approval_signal_accepted" if ok else "approval_signal_ignored",
                                  incident_id=appr["incident_id"], input={"signal": s["id"], "actor": s["actor"]},
                                  output=why, result="ok" if ok else "skipped")
            if ok:
                return s
        return None

    # ---- execution --------------------------------------------------------------------
    def execute(self, approval_id: str, source: str, approved_by: Optional[str]) -> bool:
        """Execute an approved action exactly once. Returns True if this call executed it."""
        if not self.store.claim_approval(approval_id, source, approved_by):
            return False  # someone else already claimed (or it is not pending)
        appr = self.store.get_approval(approval_id)
        assert appr is not None
        incident_id = appr["incident_id"]
        inc = self.store.get_incident(incident_id) or {}
        try:
            self.audit.record("approval", "approval_execute_start", incident_id=incident_id,
                              input={"approval_id": approval_id, "action": appr["action"], "by": approved_by,
                                     "source": source}, strict=True)
        except StoreError:
            raise

        try:
            result = self._run_action(appr["action"], inc)
        except (AirflowUnavailable, IntegrationError) as exc:
            self.store.finish_approval(approval_id, "failed", {"error": str(exc)})
            self.audit.record("approval", "approval_execute", incident_id=incident_id, input=appr["action"],
                              result="error", error=exc)
            self.incidents.escalate(incident_id, [f"Approved action `{appr['action']}` failed: {exc}"])
            return True

        status = "stale" if result.get("stale") else "executed"
        self.store.finish_approval(approval_id, status, result)
        self.audit.record("approval", "approval_execute", incident_id=incident_id, input=appr["action"],
                          output=result, result="ok" if status == "executed" else "skipped")
        log_event("approval_executed", incident_id=incident_id, action=appr["action"], status=status)
        self.store.append_incident_action(incident_id, f"approved action {appr['action']} {status} "
                                                       f"(approved by {approved_by} via {source})")
        body = (f"**Approved action `{appr['action']}` {status}.** Approved by `{approved_by}` via {source}.\n\n"
                f"Result: `{result}`")
        self.incidents.comment(incident_id, body, f"approval-exec:{approval_id}")
        if status == "stale":
            return True
        if appr["action"] == ProposedAction.MARK_SUCCESS.value:
            # The DAG run itself stays 'failed' in Airflow; a human accepted that, so the
            # run must not be re-reported as a "failed run without failed task".
            run_key = f"run:{inc['dag_id']}:{inc.get('run_id')}"
            self.store.mark_occurrence_seen(run_key, inc["fingerprint"], incident_id,
                                            {"reason": "accepted via approved mark_success"})
            self.incidents.resolve(incident_id, "Marked successful after human approval.")
        else:
            self.store.update_incident(incident_id, status=IncidentStatus.VERIFYING.value)
        return True

    def _run_action(self, action: str, inc: dict[str, Any]) -> dict[str, Any]:
        dag_id, run_id, task_id = inc["dag_id"], inc.get("run_id"), inc.get("task_id")
        map_index = inc.get("map_index", -1) if inc.get("map_index") is not None else -1
        if not run_id:
            raise IntegrationError("incident has no DAG run to act on")
        run = self.airflow.get_dag_run(dag_id, run_id)
        if action == ProposedAction.RERUN_DAG.value:
            if run.state != "failed":
                return {"stale": True, "reason": f"DAG run is now {run.state}; nothing to rerun"}
            preview = self.airflow.clear_dag_run(dag_id, run_id, dry_run=True)
            cleared = self.airflow.clear_dag_run(dag_id, run_id, dry_run=False)
            return {"action": action, "previewed": len(preview), "cleared": [t.task_id for t in cleared]}
        if action == ProposedAction.MARK_SUCCESS.value:
            if task_id:
                ti = self.airflow.get_task_instance(dag_id, run_id, task_id, map_index)
                if ti.state != "failed":
                    return {"stale": True, "reason": f"task is now {ti.state}; not marking success"}
                updated = self.airflow.set_task_instance_state(dag_id, run_id, task_id, "success", map_index)
                return {"action": action, "task_state": updated.state}
            if run.state != "failed":
                return {"stale": True, "reason": f"DAG run is now {run.state}; not marking success"}
            updated_run = self.airflow.set_dag_run_state(dag_id, run_id, "success")
            return {"action": action, "dag_run_state": updated_run.state}
        raise IntegrationError(f"unsupported approved action {action!r}")

    def _follow_up_verifying(self) -> None:
        """After an approved rerun_dag, watch the DAG run and resolve or escalate."""
        for inc in self.store.list_incidents(status=IncidentStatus.VERIFYING.value):
            try:
                run = self.airflow.get_dag_run(inc["dag_id"], inc["run_id"])
            except (AirflowUnavailable, IntegrationError) as exc:
                self.audit.record("failure", "verify_rerun", incident_id=inc["id"], result="error", error=exc)
                continue
            if run.state == "success":
                self.incidents.resolve(inc["id"], "DAG run succeeded after the approved rerun.")
            elif run.state == "failed":
                self.incidents.escalate(inc["id"], ["DAG run failed again after the approved rerun."])
            else:
                approvals = [a for a in self.store.approvals_for_incident(inc["id"]) if a.get("executed_at")]
                started = parse_dt(approvals[-1]["executed_at"]) if approvals else None
                if started and utcnow() - started > timedelta(minutes=self.settings.retry_timeout_minutes):
                    self.incidents.escalate(inc["id"], [
                        f"DAG run still '{run.state}' {self.settings.retry_timeout_minutes} minutes after the "
                        "approved rerun."])
