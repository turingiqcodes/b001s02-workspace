"""oncall-airflow-mcp: exposes the agent's Airflow client over MCP.

Read tools are always available. Write tools are disabled unless
ONCALL_MCP_ALLOW_AIRFLOW_WRITES=true, and even then they go through the same
safety machinery as the agent:

* ``clear_failed_task`` -> full DecisionPolicy + RetryManager (limits, no-retry
  list, live-state check, dry-run guard, idempotency, audit).
* ``rerun_dag`` / ``mark_task_success`` -> only with an approval that a
  human authorised in Linear; executed through ApprovalManager (once only).
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Optional

from ..factory import Services
from ..models import Analysis, AnalysisResult, Failure, FailureKind, ProposedAction
from ..policy import RetryState


def _plain(obj: Any) -> Any:
    if is_dataclass(obj):
        return {k: _plain(v) for k, v in asdict(obj).items()}
    if isinstance(obj, list):
        return [_plain(o) for o in obj]
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj


class AirflowTools:
    def __init__(self, services: Services):
        self.s = services

    def _writes_allowed(self) -> Optional[dict[str, Any]]:
        if not self.s.settings.mcp_allow_airflow_writes:
            return {"ok": False, "error": "Airflow writes are disabled (ONCALL_MCP_ALLOW_AIRFLOW_WRITES=false)"}
        return None

    # ---- reads --------------------------------------------------------------------------
    def get_dag(self, dag_id: str, include_source: bool = False) -> dict[str, Any]:
        out: dict[str, Any] = {"details": self.s.airflow.get_dag_details(dag_id)}
        if include_source:
            out["source"] = self.s.airflow.get_dag_source(dag_id)
        return _plain(out)

    def list_dag_runs(self, dag_id: str, limit: int = 10) -> list[dict[str, Any]]:
        return _plain(self.s.airflow.list_recent_dag_runs(dag_id, min(limit, 50)))

    def list_task_instances(self, dag_id: str, run_id: str) -> list[dict[str, Any]]:
        return _plain(self.s.airflow.list_task_instances(dag_id, run_id))

    def get_task_log(self, dag_id: str, run_id: str, task_id: str, try_number: int, map_index: int = -1,
                     tail_lines: int = 200) -> str:
        log = self.s.airflow.get_task_log(dag_id, run_id, task_id, try_number, map_index)
        return "\n".join(log.splitlines()[-min(tail_lines, 2000):])

    def list_import_errors(self) -> list[dict[str, Any]]:
        return _plain(self.s.airflow.list_import_errors())

    # ---- writes (guarded) ---------------------------------------------------------------
    def clear_failed_task(self, dag_id: str, run_id: str, task_id: str, map_index: int = -1) -> dict[str, Any]:
        denied = self._writes_allowed()
        if denied:
            return denied
        store, audit = self.s.store, self.s.audit
        live = self.s.airflow.get_task_instance(dag_id, run_id, task_id, map_index)
        failure = Failure(kind=FailureKind.TASK_INSTANCE, dag_id=dag_id, run_id=run_id, task_id=task_id,
                          map_index=map_index, try_number=live.try_number, state=live.state)
        incident = next((i for i in store.open_incidents()
                         if i["dag_id"] == dag_id and i.get("run_id") == run_id and i.get("task_id") == task_id), None)
        if incident is None or not incident.get("analysis_json"):
            return {"ok": False, "error": "no analysed open incident for this task; the agent must detect it first"}
        result = AnalysisResult(Analysis.model_validate(incident["analysis_json"]),
                                incident.get("analysis_source") or "unknown")
        state = RetryState(
            task_retries_used=store.count_task_retries(dag_id, run_id, task_id, map_index),
            retries_last_hour=store.count_retries_last_hour(),
            already_processing=store.has_active_retry(dag_id, run_id, task_id, map_index),
            live_task_state=live.state)
        decision = self.s.policy.decide(failure, result, state)
        audit.record("decision", "mcp_clear_failed_task_policy", incident_id=incident["id"],
                     input=failure.summary(), output={"decision": decision.decision.value, "reasons": decision.reasons})
        if decision.decision.value != "retry":
            return {"ok": False, "decision": decision.decision.value, "reasons": decision.reasons}
        executed, reasons = self.s.retries.execute(incident["id"], failure, ["requested via MCP"] + decision.reasons)
        return {"ok": executed, "reasons": reasons}

    def _approved_action(self, approval_id: str, expected: ProposedAction) -> dict[str, Any]:
        denied = self._writes_allowed()
        if denied:
            return denied
        appr = self.s.store.get_approval(approval_id)
        if appr is None or appr["action"] != expected.value:
            return {"ok": False, "error": f"approval {approval_id} for {expected.value} not found"}
        if appr["status"] != "pending":
            return {"ok": False, "error": f"approval is {appr['status']}; an approval executes only once"}
        if not appr.get("linear_issue_id"):
            return {"ok": False, "error": "approval has no Linear issue yet"}
        signal = self.s.approvals.find_valid_signal(appr)
        if signal is None:
            return {"ok": False, "error": "no authorised human approval found on the Linear issue"}
        executed = self.s.approvals.execute(approval_id, f"mcp+{signal['source']}", signal["actor"])
        return {"ok": executed, "approval": self.s.store.get_approval(approval_id)}

    def rerun_dag(self, approval_id: str) -> dict[str, Any]:
        return self._approved_action(approval_id, ProposedAction.RERUN_DAG)

    def mark_task_success(self, approval_id: str) -> dict[str, Any]:
        return self._approved_action(approval_id, ProposedAction.MARK_SUCCESS)


def create_server(services: Services):  # pragma: no cover - thin MCP wiring
    from mcp.server.mcpserver import MCPServer

    tools = AirflowTools(services)
    server = MCPServer(name="oncall-airflow-mcp",
                       instructions="Airflow access for the oncall agent. Write tools enforce the oncall safety "
                                    "policy and human approvals; they are disabled by default.")
    descriptions = {
        "get_dag": "Read DAG details (and optionally its source code).",
        "list_dag_runs": "List the most recent runs of a DAG.",
        "list_task_instances": "List task instances (with states) of one DAG run.",
        "get_task_log": "Read the tail of a task instance log for a given try number.",
        "list_import_errors": "List current DAG import/parse errors.",
        "clear_failed_task": "Retry (clear) a failed task and its failed/upstream_failed children. Disabled unless "
                             "ONCALL_MCP_ALLOW_AIRFLOW_WRITES=true; subject to the full oncall retry policy and limits.",
        "rerun_dag": "Execute an approved rerun_dag action. Requires an approval_id that an authorised human "
                     "approved in Linear. Executes at most once.",
        "mark_task_success": "Execute an approved mark_success action. Requires an approval_id that an authorised "
                             "human approved in Linear. Executes at most once.",
    }
    for fn in (tools.get_dag, tools.list_dag_runs, tools.list_task_instances, tools.get_task_log,
               tools.list_import_errors, tools.clear_failed_task, tools.rerun_dag, tools.mark_task_success):
        server.add_tool(fn, name=fn.__name__, description=descriptions[fn.__name__])
    return server


def main() -> None:  # pragma: no cover
    from ..audit import setup_json_logging
    from ..config import load_settings
    from ..factory import build_services

    setup_json_logging()
    create_server(build_services(load_settings())).run("stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
