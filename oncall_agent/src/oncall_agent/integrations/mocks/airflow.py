"""In-memory Airflow 3.x mock with a tiny 'scheduler' (``advance``) so retries
can be simulated end to end."""
from __future__ import annotations

import copy
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Optional

from ...models import DagRunInfo, ImportErrorInfo, TaskInstanceInfo, utcnow
from .. import AirflowClient, AirflowUnavailable, IntegrationError


class MockAirflowClient(AirflowClient):
    mode = "mock"

    def __init__(self) -> None:
        self.available = True
        self.dags: dict[str, dict[str, Any]] = {}          # dag_id -> {"source", "details", "downstream"}
        self.runs: dict[tuple[str, str], DagRunInfo] = {}
        self.tis: dict[tuple[str, str, str, int], TaskInstanceInfo] = {}
        self.logs: dict[tuple[str, str, str, int], str] = {}
        self.import_errors: list[ImportErrorInfo] = []
        # (dag, run, task) -> list of outcomes consumed by advance(): "success" | "failed" | "running"
        self.retry_outcomes: dict[tuple[str, str, str], list[str]] = {}
        self.write_calls: list[dict[str, Any]] = []
        self.read_calls: list[str] = []

    # ---- helpers to build scenarios ------------------------------------------------------
    def add_dag(self, dag_id: str, source: str = "", details: Optional[dict] = None,
                downstream: Optional[dict[str, list[str]]] = None) -> None:
        self.dags[dag_id] = {"source": source, "details": details or {"dag_id": dag_id},
                             "downstream": downstream or {}}

    def add_run(self, dag_id: str, run_id: str, state: str, start: Optional[datetime] = None,
                end: Optional[datetime] = None, **kw: Any) -> DagRunInfo:
        run = DagRunInfo(dag_id=dag_id, run_id=run_id, state=state, start_date=start or utcnow(),
                         end_date=end if end is not None else (utcnow() if state in ("failed", "success") else None),
                         **kw)
        self.runs[(dag_id, run_id)] = run
        return run

    def add_ti(self, dag_id: str, run_id: str, task_id: str, state: Optional[str], try_number: int = 1,
               map_index: int = -1, log: str = "", end: Optional[datetime] = None) -> TaskInstanceInfo:
        ti = TaskInstanceInfo(dag_id=dag_id, run_id=run_id, task_id=task_id, state=state, try_number=try_number,
                              map_index=map_index, start_date=utcnow() - timedelta(minutes=2),
                              end_date=end if end is not None else (utcnow() if state in ("failed", "success", "upstream_failed") else None))
        self.tis[(dag_id, run_id, task_id, map_index)] = ti
        if log:
            self.logs[(dag_id, run_id, task_id, try_number)] = log
        return ti

    def add_import_error(self, filename: str, stack_trace: str) -> None:
        self.import_errors.append(ImportErrorInfo(import_error_id=len(self.import_errors) + 1,
                                                  filename=filename, stack_trace=stack_trace, timestamp=utcnow()))

    def advance(self) -> None:
        """Simulate the scheduler running every cleared (state=None) task once."""
        for key, ti in list(self.tis.items()):
            if ti.state is not None:
                continue
            outcomes = self.retry_outcomes.get((ti.dag_id, ti.run_id, ti.task_id), [])
            outcome = outcomes.pop(0) if outcomes else "success"
            ti.try_number += 1
            ti.start_date = utcnow()
            if outcome == "running":
                ti.state = "running"
                ti.end_date = None
            else:
                ti.state = outcome
                ti.end_date = utcnow()
                if outcome == "failed":
                    self.logs[(ti.dag_id, ti.run_id, ti.task_id, ti.try_number)] = \
                        self.logs.get((ti.dag_id, ti.run_id, ti.task_id, ti.try_number - 1), "failed again")
        for (dag_id, run_id), run in self.runs.items():
            states = [t.state for t in self.tis.values() if t.dag_id == dag_id and t.run_id == run_id]
            if run.state in ("queued", "running") and states:
                if all(s in ("success", "skipped") for s in states):
                    run.state, run.end_date = "success", utcnow()
                elif any(s == "failed" for s in states) and not any(s in (None, "running") for s in states):
                    run.state, run.end_date = "failed", utcnow()

    # ---- internals ----------------------------------------------------------------------
    def _check(self, op: str) -> None:
        self.read_calls.append(op)
        if not self.available:
            raise AirflowUnavailable(f"mock Airflow is down ({op})")

    # ---- reads --------------------------------------------------------------------------
    def health(self) -> bool:
        return self.available

    def list_failed_task_instances(self, end_date_gte: datetime) -> list[TaskInstanceInfo]:
        self._check("list_failed_task_instances")
        return [copy.copy(t) for t in self.tis.values()
                if t.state == "failed" and t.end_date and t.end_date >= end_date_gte]

    def list_failed_dag_runs(self, end_date_gte: datetime) -> list[DagRunInfo]:
        self._check("list_failed_dag_runs")
        return [copy.copy(r) for r in self.runs.values()
                if r.state == "failed" and r.end_date and r.end_date >= end_date_gte]

    def list_task_instances(self, dag_id: str, run_id: str) -> list[TaskInstanceInfo]:
        self._check("list_task_instances")
        return [copy.copy(t) for t in self.tis.values() if t.dag_id == dag_id and t.run_id == run_id]

    def get_task_instance(self, dag_id: str, run_id: str, task_id: str, map_index: int = -1) -> TaskInstanceInfo:
        self._check("get_task_instance")
        ti = self.tis.get((dag_id, run_id, task_id, map_index))
        if ti is None:
            raise IntegrationError(f"task instance not found: {dag_id}/{run_id}/{task_id}")
        return copy.copy(ti)

    def get_dag_run(self, dag_id: str, run_id: str) -> DagRunInfo:
        self._check("get_dag_run")
        run = self.runs.get((dag_id, run_id))
        if run is None:
            raise IntegrationError(f"dag run not found: {dag_id}/{run_id}")
        return copy.copy(run)

    def get_task_log(self, dag_id: str, run_id: str, task_id: str, try_number: int, map_index: int = -1) -> str:
        self._check("get_task_log")
        log = self.logs.get((dag_id, run_id, task_id, try_number))
        if log is None:
            raise IntegrationError("log not found")
        return log

    def get_dag_details(self, dag_id: str) -> dict[str, Any]:
        self._check("get_dag_details")
        if dag_id not in self.dags:
            raise IntegrationError(f"dag not found: {dag_id}")
        return dict(self.dags[dag_id]["details"])

    def get_dag_source(self, dag_id: str) -> str:
        self._check("get_dag_source")
        if dag_id not in self.dags:
            raise IntegrationError(f"dag not found: {dag_id}")
        return self.dags[dag_id]["source"]

    def list_recent_dag_runs(self, dag_id: str, limit: int = 10) -> list[DagRunInfo]:
        self._check("list_recent_dag_runs")
        runs = [copy.copy(r) for r in self.runs.values() if r.dag_id == dag_id]
        runs.sort(key=lambda r: r.start_date or utcnow(), reverse=True)
        return runs[:limit]

    def list_import_errors(self) -> list[ImportErrorInfo]:
        self._check("list_import_errors")
        return list(self.import_errors)

    # ---- writes -------------------------------------------------------------------------
    def _downstream(self, dag_id: str, task_id: str) -> set[str]:
        graph = self.dags.get(dag_id, {}).get("downstream", {})
        seen: set[str] = set()
        stack = list(graph.get(task_id, []))
        while stack:
            t = stack.pop()
            if t not in seen:
                seen.add(t)
                stack.extend(graph.get(t, []))
        return seen

    def clear_task_instances(self, dag_id: str, run_id: str, task_ids: list[str], *, dry_run: bool,
                             only_failed: bool = True, include_downstream: bool = True,
                             map_indexes: Optional[list[int]] = None) -> list[TaskInstanceInfo]:
        self._check("clear_task_instances")
        self.write_calls.append({"op": "clear_task_instances", "dag_id": dag_id, "run_id": run_id,
                                 "task_ids": list(task_ids), "dry_run": dry_run, "only_failed": only_failed})
        targets = set(task_ids)
        if include_downstream:
            for t in task_ids:
                targets |= self._downstream(dag_id, t)
        affected = []
        for ti in self.tis.values():
            if ti.dag_id != dag_id or ti.run_id != run_id or ti.task_id not in targets:
                continue
            if only_failed and ti.state not in ("failed", "upstream_failed"):
                continue
            affected.append(copy.copy(ti))
            if not dry_run:
                ti.state = None
        if not dry_run and affected and (dag_id, run_id) in self.runs:
            self.runs[(dag_id, run_id)].state = "queued"
        return affected

    def clear_dag_run(self, dag_id: str, run_id: str, *, dry_run: bool) -> list[TaskInstanceInfo]:
        self._check("clear_dag_run")
        self.write_calls.append({"op": "clear_dag_run", "dag_id": dag_id, "run_id": run_id, "dry_run": dry_run})
        affected = [copy.copy(t) for t in self.tis.values() if t.dag_id == dag_id and t.run_id == run_id]
        if not dry_run:
            for t in self.tis.values():
                if t.dag_id == dag_id and t.run_id == run_id:
                    t.state = None
            if (dag_id, run_id) in self.runs:
                self.runs[(dag_id, run_id)].state = "queued"
        return affected

    def set_task_instance_state(self, dag_id: str, run_id: str, task_id: str, state: str,
                                map_index: int = -1) -> TaskInstanceInfo:
        self._check("set_task_instance_state")
        self.write_calls.append({"op": "set_task_instance_state", "dag_id": dag_id, "run_id": run_id,
                                 "task_id": task_id, "state": state})
        ti = self.tis[(dag_id, run_id, task_id, map_index)]
        ti.state = state
        return copy.copy(ti)

    def set_dag_run_state(self, dag_id: str, run_id: str, state: str) -> DagRunInfo:
        self._check("set_dag_run_state")
        self.write_calls.append({"op": "set_dag_run_state", "dag_id": dag_id, "run_id": run_id, "state": state})
        run = self.runs[(dag_id, run_id)]
        run.state = state
        return copy.copy(run)
