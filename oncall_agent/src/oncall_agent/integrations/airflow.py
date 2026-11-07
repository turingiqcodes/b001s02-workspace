"""Real Airflow 3.x REST API v2 client.

Authentication: Airflow 3 issues JWTs. We exchange AIRFLOW_USERNAME /
AIRFLOW_PASSWORD at ``POST /auth/token`` and send ``Authorization: Bearer``.
The token is refreshed once automatically on a 401.

Use an Airflow user with the **Op** role: it can read everything and clear /
set task state, but cannot manage users or connections.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote

import httpx

from ..models import DagRunInfo, ImportErrorInfo, TaskInstanceInfo, iso, parse_dt
from . import AirflowClient, AirflowUnavailable, IntegrationError

PAGE = 100


def _ti(d: dict[str, Any]) -> TaskInstanceInfo:
    return TaskInstanceInfo(
        dag_id=d.get("dag_id", ""),
        run_id=d.get("dag_run_id") or d.get("run_id", ""),
        task_id=d.get("task_id", ""),
        state=d.get("state"),
        try_number=int(d.get("try_number") or 0),
        map_index=int(d.get("map_index", -1) if d.get("map_index") is not None else -1),
        start_date=parse_dt(d.get("start_date")),
        end_date=parse_dt(d.get("end_date")),
        operator=d.get("operator") or d.get("operator_name"),
    )


def _run(d: dict[str, Any]) -> DagRunInfo:
    return DagRunInfo(
        dag_id=d.get("dag_id", ""),
        run_id=d.get("dag_run_id") or d.get("run_id", ""),
        state=d.get("state"),
        start_date=parse_dt(d.get("start_date")),
        end_date=parse_dt(d.get("end_date")),
        logical_date=parse_dt(d.get("logical_date")),
        run_type=d.get("run_type"),
        note=d.get("note"),
    )


def _q(value: str) -> str:
    return quote(value, safe="")


def _render_error_detail(detail: Any) -> str:
    """Render an Airflow 3 structured-log ``error_detail`` as a Python-style traceback.

    Airflow 3 keeps the exception out of the log message ("Task failed with exception") and puts
    it in ``error_detail``: a list of exceptions, outermost first, each followed by its cause
    (``is_cause``) or the exception it was raised while handling. Like Python, the raised
    exception is printed last, so it is the line the fingerprint and the analysis pick up.
    """
    if isinstance(detail, str):
        try:
            detail = json.loads(detail)
        except ValueError:
            return detail
    if not isinstance(detail, list):
        return str(detail)
    chain = [e for e in detail if isinstance(e, dict)]
    blocks: list[str] = []
    for i in range(len(chain) - 1, -1, -1):  # causes first, the raised (outermost) exception last
        exc = chain[i]
        frames = [f for f in exc.get("frames") or [] if isinstance(f, dict)]
        lines = ["Traceback (most recent call last):"] if frames else []
        lines += [f'  File "{f.get("filename", "?")}", line {f.get("lineno", "?")}, in {f.get("name", "?")}'
                  for f in frames]
        exc_type, exc_value = exc.get("exc_type") or "Exception", exc.get("exc_value")
        lines.append(f"{exc_type}: {exc_value}" if exc_value not in (None, "") else str(exc_type))
        blocks.append("\n".join(lines))
        if i > 0:
            blocks.append("\nThe above exception was the direct cause of the following exception:\n"
                          if exc.get("is_cause") else
                          "\nDuring handling of the above exception, another exception occurred:\n")
    return "\n".join(blocks)


class RestAirflowClient(AirflowClient):
    mode = "real"

    def __init__(self, base_url: str, username: str, password: str, timeout: float = 30.0,
                 transport: Optional[httpx.BaseTransport] = None):
        self.base_url = base_url.rstrip("/")
        self._username = username
        self._password = password
        self._token: Optional[str] = None
        self._http = httpx.Client(base_url=self.base_url, timeout=timeout, transport=transport)

    # ---- auth / transport -------------------------------------------------------------
    def _login(self) -> None:
        try:
            resp = self._http.post("/auth/token", json={"username": self._username, "password": self._password})
        except httpx.HTTPError as exc:
            raise AirflowUnavailable(f"Airflow auth request failed: {exc}") from exc
        if resp.status_code >= 400:
            raise AirflowUnavailable(f"Airflow auth failed with HTTP {resp.status_code}")
        token = resp.json().get("access_token")
        if not token:
            raise AirflowUnavailable("Airflow auth response had no access_token")
        self._token = token

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        if self._token is None:
            self._login()
        for attempt in range(2):
            headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
            try:
                resp = self._http.request(method, f"/api/v2{path}", headers=headers, **kwargs)
            except httpx.HTTPError as exc:
                raise AirflowUnavailable(f"Airflow {method} {path} failed: {exc}") from exc
            if resp.status_code == 401 and attempt == 0:
                self._login()
                continue
            if resp.status_code >= 500:
                raise AirflowUnavailable(f"Airflow {method} {path} returned HTTP {resp.status_code}")
            if resp.status_code >= 400:
                raise IntegrationError(f"Airflow {method} {path} returned HTTP {resp.status_code}: {resp.text[:300]}")
            return resp.json() if resp.content else {}
        raise AirflowUnavailable("Airflow authentication keeps failing")

    def _paged(self, path: str, key: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        offset = 0
        while True:
            data = self._request("GET", path, params={**params, "limit": PAGE, "offset": offset})
            batch = data.get(key, [])
            items.extend(batch)
            total = data.get("total_entries", len(items))
            offset += len(batch)
            if not batch or offset >= total:
                return items

    # ---- reads ------------------------------------------------------------------------
    def health(self) -> bool:
        try:
            self._http.get("/api/v2/monitor/health").raise_for_status()
            return True
        except httpx.HTTPError:
            return False

    def list_failed_task_instances(self, end_date_gte: datetime) -> list[TaskInstanceInfo]:
        rows = self._paged("/dags/~/dagRuns/~/taskInstances", "task_instances",
                           {"state": "failed", "end_date_gte": iso(end_date_gte), "order_by": "end_date"})
        return [_ti(r) for r in rows]

    def list_failed_dag_runs(self, end_date_gte: datetime) -> list[DagRunInfo]:
        rows = self._paged("/dags/~/dagRuns", "dag_runs",
                           {"state": "failed", "end_date_gte": iso(end_date_gte), "order_by": "end_date"})
        return [_run(r) for r in rows]

    def list_task_instances(self, dag_id: str, run_id: str) -> list[TaskInstanceInfo]:
        rows = self._paged(f"/dags/{_q(dag_id)}/dagRuns/{_q(run_id)}/taskInstances", "task_instances", {})
        return [_ti(r) for r in rows]

    def get_task_instance(self, dag_id: str, run_id: str, task_id: str, map_index: int = -1) -> TaskInstanceInfo:
        base = f"/dags/{_q(dag_id)}/dagRuns/{_q(run_id)}/taskInstances/{_q(task_id)}"
        path = base if map_index < 0 else f"{base}/{map_index}"
        return _ti(self._request("GET", path))

    def get_dag_run(self, dag_id: str, run_id: str) -> DagRunInfo:
        return _run(self._request("GET", f"/dags/{_q(dag_id)}/dagRuns/{_q(run_id)}"))

    def get_task_log(self, dag_id: str, run_id: str, task_id: str, try_number: int, map_index: int = -1) -> str:
        params: dict[str, Any] = {"full_content": "true"}
        if map_index >= 0:
            params["map_index"] = map_index
        data = self._request(
            "GET", f"/dags/{_q(dag_id)}/dagRuns/{_q(run_id)}/taskInstances/{_q(task_id)}/logs/{try_number}",
            params=params)
        content = data.get("content", "")
        if isinstance(content, list):  # Airflow 3 structured logs: list of {"event": ..., "timestamp": ...}
            lines = []
            for item in content:
                if isinstance(item, dict):
                    ts = item.get("timestamp", "")
                    lvl = item.get("level", "")
                    lines.append(" ".join(str(p) for p in (ts, lvl, item.get("event", "")) if p))
                    if item.get("error_detail"):
                        lines.append(_render_error_detail(item["error_detail"]))
                else:
                    lines.append(str(item))
            return "\n".join(lines)
        return str(content)

    def get_dag_details(self, dag_id: str) -> dict[str, Any]:
        return self._request("GET", f"/dags/{_q(dag_id)}/details")

    def get_dag_source(self, dag_id: str) -> str:
        return str(self._request("GET", f"/dagSources/{_q(dag_id)}").get("content", ""))

    def list_recent_dag_runs(self, dag_id: str, limit: int = 10) -> list[DagRunInfo]:
        data = self._request("GET", f"/dags/{_q(dag_id)}/dagRuns",
                             params={"order_by": "-start_date", "limit": limit})
        return [_run(r) for r in data.get("dag_runs", [])]

    def list_import_errors(self) -> list[ImportErrorInfo]:
        rows = self._paged("/importErrors", "import_errors", {})
        return [
            ImportErrorInfo(
                import_error_id=r.get("import_error_id"),
                filename=r.get("filename", ""),
                stack_trace=r.get("stack_trace", ""),
                timestamp=parse_dt(r.get("timestamp")),
                bundle_name=r.get("bundle_name"),
            )
            for r in rows
        ]

    # ---- writes -----------------------------------------------------------------------
    def clear_task_instances(self, dag_id: str, run_id: str, task_ids: list[str], *, dry_run: bool,
                             only_failed: bool = True, include_downstream: bool = True,
                             map_indexes: Optional[list[int]] = None) -> list[TaskInstanceInfo]:
        tasks: list[Any] = task_ids
        if map_indexes and any(m >= 0 for m in map_indexes):
            tasks = [[t, m] for t, m in zip(task_ids, map_indexes)]
        body = {
            "dry_run": dry_run,
            "dag_run_id": run_id,
            "task_ids": tasks,
            "only_failed": only_failed,
            "include_downstream": include_downstream,
            "include_upstream": False,
            "reset_dag_runs": True,
        }
        data = self._request("POST", f"/dags/{_q(dag_id)}/clearTaskInstances", json=body)
        return [_ti(r) for r in data.get("task_instances", [])]

    def clear_dag_run(self, dag_id: str, run_id: str, *, dry_run: bool) -> list[TaskInstanceInfo]:
        data = self._request("POST", f"/dags/{_q(dag_id)}/dagRuns/{_q(run_id)}/clear",
                             json={"dry_run": dry_run, "only_failed": False})
        return [_ti(r) for r in data.get("task_instances", [])]

    def set_task_instance_state(self, dag_id: str, run_id: str, task_id: str, state: str,
                                map_index: int = -1) -> TaskInstanceInfo:
        base = f"/dags/{_q(dag_id)}/dagRuns/{_q(run_id)}/taskInstances/{_q(task_id)}"
        path = base if map_index < 0 else f"{base}/{map_index}"
        data = self._request("PATCH", path, json={"new_state": state})
        items = data.get("task_instances") if isinstance(data, dict) else None
        return _ti(items[0]) if items else self.get_task_instance(dag_id, run_id, task_id, map_index)

    def set_dag_run_state(self, dag_id: str, run_id: str, state: str) -> DagRunInfo:
        return _run(self._request("PATCH", f"/dags/{_q(dag_id)}/dagRuns/{_q(run_id)}", json={"state": state}))
