"""Failure detection with a persistent, overlap-safe watermark.

Strategy
--------
* Each cycle asks Airflow for failed task instances and failed DAG runs whose
  ``end_date`` is >= ``watermark - overlap``. The overlap window re-reads the
  last few minutes, so a state that changed late (or a run whose end_date was
  written slightly before the previous cycle's clock) is never missed.
* Every occurrence has a unique key (dag/run/task/map_index/try, or dag/run, or
  import file + trace hash). Keys already recorded in SQLite are skipped, so
  the overlap never causes double processing.
* Import errors are not time-based: the full current list is read every cycle
  and new (file, trace) pairs are reported.
* If *any* Airflow call fails, ``AirflowUnavailable`` propagates and the caller
  holds the watermark.
* Scope: only DAGs configured in dag_repos.json are monitored (SRS: "all
  configured DAGs"), unless ONCALL_MONITOR_ALL_DAGS=true. On a shared Airflow
  this keeps the agent away from other people's DAGs.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from .config import DagReposConfig, Settings
from .integrations import AirflowClient, IntegrationError
from .models import DagRunInfo, Failure, FailureKind, TaskInstanceInfo, parse_dt, utcnow
from .store import Store


@dataclass
class DetectionResult:
    cycle_start: datetime
    since: datetime
    failures: list[Failure] = field(default_factory=list)
    import_error_files: set[str] = field(default_factory=set)
    skipped_seen: int = 0
    skipped_out_of_scope: int = 0


def parse_timedelta(value: Any) -> Optional[timedelta]:
    """Parse the various shapes Airflow uses for dagrun_timeout."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return timedelta(seconds=value)
    if isinstance(value, dict):
        return timedelta(days=value.get("days", 0), seconds=value.get("seconds", 0),
                         microseconds=value.get("microseconds", 0))
    text = str(value).strip()
    m = re.fullmatch(r"(?:(\d+) days?, )?(\d+):(\d{2}):(\d{2}(?:\.\d+)?)", text)
    if m:
        return timedelta(days=int(m.group(1) or 0), hours=int(m.group(2)), minutes=int(m.group(3)),
                         seconds=float(m.group(4)))
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:([\d.]+)S)?)?", text)
    if m and any(m.groups()):
        return timedelta(days=int(m.group(1) or 0), hours=int(m.group(2) or 0), minutes=int(m.group(3) or 0),
                         seconds=float(m.group(4) or 0))
    return None


class Detector:
    def __init__(self, airflow: AirflowClient, store: Store, dag_repos: DagReposConfig, settings: Settings):
        self.airflow = airflow
        self.store = store
        self.dag_repos = dag_repos
        self.settings = settings

    def in_scope(self, dag_id: Optional[str]) -> bool:
        return self.settings.monitor_all_dags or (dag_id is not None and dag_id in self.dag_repos.dags)

    def window_start(self, now: datetime) -> datetime:
        wm = self.store.get_watermark()["value"]
        if wm:
            return parse_dt(wm) - timedelta(minutes=self.settings.detection_overlap_minutes)  # type: ignore[operator]
        return now - timedelta(hours=self.settings.initial_lookback_hours)

    def detect(self) -> DetectionResult:
        now = utcnow()
        result = DetectionResult(cycle_start=now, since=self.window_start(now))

        all_tis = self.airflow.list_failed_task_instances(result.since)
        all_runs = self.airflow.list_failed_dag_runs(result.since)
        import_errors = self.airflow.list_import_errors()
        failed_tis = [t for t in all_tis if self.in_scope(t.dag_id)]
        failed_runs = [r for r in all_runs if self.in_scope(r.dag_id)]
        result.skipped_out_of_scope = (len(all_tis) - len(failed_tis)) + (len(all_runs) - len(failed_runs))

        ti_failures: dict[str, Failure] = {}
        for ti in failed_tis:
            self._add_ti(ti, ti_failures, result)

        runs_with_failed_ti = {(t.dag_id, t.run_id) for t in failed_tis}
        for run in failed_runs:
            if (run.dag_id, run.run_id) in runs_with_failed_ti:
                continue
            tis = self.airflow.list_task_instances(run.dag_id, run.run_id)
            failed_here = [t for t in tis if t.state == "failed"]
            if failed_here:  # failed TIs that ended before the window: report them as TI failures
                for ti in failed_here:
                    self._add_ti(ti, ti_failures, result)
                continue
            failure = self._dag_run_failure(run, tis)
            if self.store.occurrence_seen(failure.occurrence_key):
                result.skipped_seen += 1
            else:
                result.failures.append(failure)
        result.failures[:0] = list(ti_failures.values())

        for ie in import_errors:
            result.import_error_files.add(ie.filename)
            mapped = self.dag_repos.dag_id_for_file(ie.filename)
            if not self.settings.monitor_all_dags and mapped is None:
                result.skipped_out_of_scope += 1
                continue
            dag_id = mapped or ie.filename
            failure = Failure(kind=FailureKind.IMPORT_ERROR, dag_id=dag_id, filename=ie.filename,
                              error_message=ie.stack_trace, end_date=ie.timestamp,
                              details={"import_error_id": ie.import_error_id, "bundle_name": ie.bundle_name})
            if self.store.occurrence_seen(failure.occurrence_key):
                result.skipped_seen += 1
            else:
                result.failures.append(failure)
        return result

    def _add_ti(self, ti: TaskInstanceInfo, out: dict[str, Failure], result: DetectionResult) -> None:
        failure = Failure(kind=FailureKind.TASK_INSTANCE, dag_id=ti.dag_id, run_id=ti.run_id, task_id=ti.task_id,
                          try_number=ti.try_number, map_index=ti.map_index, state=ti.state,
                          start_date=ti.start_date, end_date=ti.end_date)
        key = failure.occurrence_key
        if key in out:
            return
        if self.store.occurrence_seen(key) or self.store.has_active_retry(ti.dag_id, ti.run_id, ti.task_id, ti.map_index):
            result.skipped_seen += 1  # already handled, or owned by retry verification
            return
        out[key] = failure

    def _dag_run_failure(self, run: DagRunInfo, tis: list[TaskInstanceInfo]) -> Failure:
        counts: dict[str, int] = {}
        for t in tis:
            counts[t.state or "none"] = counts.get(t.state or "none", 0) + 1
        states = ", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "no task instances"
        reason = "DAG run failed without any failed task instance"
        timeout = None
        try:
            details = self.airflow.get_dag_details(run.dag_id)
            # Airflow 3.1 REST API uses "dag_run_timeout" (ISO 8601, e.g. "PT20S"); older shapes use "dagrun_timeout".
            timeout = parse_timedelta(details.get("dag_run_timeout") or details.get("dagrun_timeout"))
        except IntegrationError:
            pass
        duration = (run.end_date - run.start_date) if (run.end_date and run.start_date) else None
        if timeout and duration and duration >= timeout:
            reason = "DAG run failed: dagrun_timeout exceeded"
        message = (f"{reason} (task states: {states})"
                   + (f"; duration {int(duration.total_seconds())}s" if duration else "")
                   + (f"; dagrun_timeout {int(timeout.total_seconds())}s" if timeout else "")
                   + (f"; note: {run.note}" if run.note else ""))
        return Failure(kind=FailureKind.DAG_RUN, dag_id=run.dag_id, run_id=run.run_id, state=run.state,
                       start_date=run.start_date, end_date=run.end_date, error_message=message,
                       details={"task_states": counts, "dagrun_timeout_exceeded": reason.endswith("exceeded")})
