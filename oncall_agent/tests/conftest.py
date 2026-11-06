"""Shared test helpers. Everything is offline: mocks + in-memory/tmp SQLite."""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

import pytest

from oncall_agent.audit import setup_json_logging
from oncall_agent.config import Settings
from oncall_agent.integrations.mocks import MockAirflowClient, MockLLMClient, MockGitHubClient, MockLinearClient
from oncall_agent.simulate import make_services
from oncall_agent.store import Store

RUN = "scheduled__2026-11-06T08:00:00+00:00"

RETRIABLE = {"classification": "retriable", "confidence": 0.9, "root_cause": "Temporary DB timeout",
             "evidence": ["Connection timed out in log"], "suggested_fix": "Retry the failed task",
             "proposed_action": "retry_task", "suspected_commit": None}
NON_RETRIABLE = {"classification": "non_retriable", "confidence": 0.95, "root_cause": "Missing column",
                 "evidence": ["UndefinedColumn"], "suggested_fix": "Add the column",
                 "proposed_action": "no_action", "suspected_commit": None}
TIMEOUT_LOG = "ERROR - psycopg2.OperationalError: connection to server at 10.0.0.5 failed: Connection timed out"
COLUMN_LOG = 'psycopg2.errors.UndefinedColumn: column "discount_code" does not exist'


@pytest.fixture(autouse=True)
def quiet_logs():
    setup_json_logging(logging.CRITICAL)


def build(script: Optional[dict] = None, *, settings: Optional[Settings] = None, store: Optional[Store] = None,
          airflow: Optional[MockAirflowClient] = None, linear: Optional[MockLinearClient] = None,
          github: Optional[MockGitHubClient] = None, llm: Optional[MockLLMClient] = None,
          use_llm: bool = True):
    return make_services(script, settings=settings or Settings(force_mocks=True, database_path=":memory:"),
                         store=store, airflow=airflow, linear=linear, github=github, llm=llm,
                         use_llm=use_llm)


def add_failed_task(af: MockAirflowClient, dag_id: str = "sales_etl_dag", task_id: str = "load",
                    log: str = TIMEOUT_LOG, run_id: str = RUN, with_neighbours: bool = True) -> None:
    if dag_id not in af.dags:
        af.add_dag(dag_id, f"# source of {dag_id}", {"dag_id": dag_id},
                   downstream={"extract": [task_id], task_id: ["publish"]})
    af.add_run(dag_id, run_id, "failed")
    if with_neighbours:
        af.add_ti(dag_id, run_id, "extract", "success")
        af.add_ti(dag_id, run_id, "publish", "upstream_failed")
    af.add_ti(dag_id, run_id, task_id, "failed", log=log)


def only_incident(s) -> dict[str, Any]:
    rows = s.store.list_incidents()
    assert len(rows) == 1, rows
    return rows[0]


def real_clears(af: MockAirflowClient) -> list[dict]:
    return [c for c in af.write_calls if c["op"] == "clear_task_instances" and not c["dry_run"]]
