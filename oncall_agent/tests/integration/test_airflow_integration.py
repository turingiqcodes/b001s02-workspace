"""Integration tests against a throwaway Airflow 3.x instance.

Skipped unless ONCALL_IT_AIRFLOW_URL is set. The Airflow instance must load
DAGs from tests/integration/dags/ (see README "Integration tests").
Claude, GitHub and Linear stay mocked: these tests exercise the real Airflow API.

    ONCALL_IT_AIRFLOW_URL=http://localhost:18080 \
    ONCALL_IT_AIRFLOW_USERNAME=oncall ONCALL_IT_AIRFLOW_PASSWORD=... \
    pytest tests/integration -m integration -v
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable

import httpx
import pytest

from oncall_agent.config import Settings
from oncall_agent.integrations.airflow import RestAirflowClient
from oncall_agent.store import Store

from tests.conftest import NON_RETRIABLE, RETRIABLE, build

URL = os.environ.get("ONCALL_IT_AIRFLOW_URL")
USER = os.environ.get("ONCALL_IT_AIRFLOW_USERNAME", "")
PASSWORD = os.environ.get("ONCALL_IT_AIRFLOW_PASSWORD", "")

pytestmark = [pytest.mark.integration,
              pytest.mark.skipif(not URL, reason="set ONCALL_IT_AIRFLOW_URL to run Airflow integration tests")]

SCRIPT = {"Connection timed out": RETRIABLE, "region_code": NON_RETRIABLE,
          "dagrun_timeout exceeded": {**NON_RETRIABLE, "classification": "uncertain", "confidence": 0.6},
          "oncall_it_module_that_does_not_exist": NON_RETRIABLE}


class AirflowAdmin:
    """Test-side helper to unpause / trigger DAGs (the agent itself never does this)."""

    def __init__(self) -> None:
        self.http = httpx.Client(base_url=URL, timeout=30)
        token = self.http.post("/auth/token", json={"username": USER, "password": PASSWORD}).json()["access_token"]
        self.http.headers["Authorization"] = f"Bearer {token}"

    def trigger(self, dag_id: str) -> str:
        self.http.patch(f"/api/v2/dags/{dag_id}", json={"is_paused": False}).raise_for_status()
        resp = self.http.post(f"/api/v2/dags/{dag_id}/dagRuns", json={"logical_date": None})
        resp.raise_for_status()
        return resp.json()["dag_run_id"]

    def run_state(self, dag_id: str, run_id: str) -> str:
        return self.http.get(f"/api/v2/dags/{dag_id}/dagRuns/{run_id}").json()["state"]


def wait_for(cond: Callable[[], Any], timeout: float = 240, every: float = 3) -> Any:
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = cond()
        if value:
            return value
        time.sleep(every)
    raise AssertionError("condition not met in time")


@pytest.fixture(scope="module")
def admin() -> AirflowAdmin:
    return AirflowAdmin()


@pytest.fixture
def services(tmp_path):
    # monitor_all_dags: the it_* DAGs are not in config/dag_repos.json, and this Airflow is throwaway.
    settings = Settings(force_mocks=False, database_path=str(tmp_path / "it.db"), initial_lookback_hours=1,
                        detection_overlap_minutes=10, monitor_all_dags=True)
    return build(SCRIPT, settings=settings, store=Store(settings.database_path),
                 airflow=RestAirflowClient(URL, USER, PASSWORD))


def _incidents(s, **match):
    return [i for i in s.store.list_incidents() if all(i.get(k) == v for k, v in match.items())]


def test_failed_task_detection(admin, services):
    run_id = admin.trigger("it_fail_task_dag")
    wait_for(lambda: admin.run_state("it_fail_task_dag", run_id) == "failed")
    services.agent.run_cycle()
    # Earlier runs of this DAG with the same error share the fingerprint, so this run may be
    # recorded as a repeat occurrence of one incident: look it up via its occurrences.
    incs = _incidents(services, dag_id="it_fail_task_dag")
    assert len(incs) == 1, "same error in several runs must give one incident"
    inc = incs[0]
    assert inc["task_id"] == "transform" and inc["kind"] == "task_instance"
    assert inc["decision"] == "non_retriable"
    occurrences = services.store.occurrences_for_incident(inc["id"])
    assert any(run_id in o["occurrence_key"] for o in occurrences)


def test_failed_dag_run_detection(admin, services):
    run_id = admin.trigger("it_timeout_dag")
    wait_for(lambda: admin.run_state("it_timeout_dag", run_id) == "failed", timeout=180)
    services.agent.run_cycle()
    incs = _incidents(services, dag_id="it_timeout_dag")
    assert len(incs) == 1 and incs[0]["kind"] == "dag_run" and incs[0]["task_id"] is None
    assert "dagrun_timeout exceeded" in incs[0]["error"]
    assert any(run_id in o["occurrence_key"] for o in services.store.occurrences_for_incident(incs[0]["id"]))


def test_import_error_detection(services):
    services.agent.run_cycle()
    inc = [i for i in services.store.list_incidents() if i["kind"] == "import_error"]
    assert any("it_broken_import_dag" in (i["dag_id"] or "") for i in inc)


def test_task_retry_verification_and_resolution(admin, services):
    run_id = admin.trigger("it_flaky_dag")
    wait_for(lambda: admin.run_state("it_flaky_dag", run_id) == "failed")
    services.agent.run_cycle()
    inc = _incidents(services, dag_id="it_flaky_dag", run_id=run_id)[0]
    assert inc["decision"] == "retry" and inc["status"] == "retrying"
    retries = services.store.retries_for_incident(inc["id"])
    assert len(retries) == 1 and retries[0]["status"] == "requested"
    # extract (success) must not have been cleared
    ti = services.airflow.get_task_instance("it_flaky_dag", run_id, "extract")
    assert ti.state == "success" and ti.try_number == 1

    def resolved():
        services.agent.run_cycle()
        return services.store.get_incident(inc["id"])["status"] == "resolved"

    wait_for(resolved, timeout=240, every=5)
    assert services.store.retries_for_incident(inc["id"])[0]["verification_status"] == "verified_success"
    assert admin.run_state("it_flaky_dag", run_id) == "success"
