"""Test DAG for the Airflow Oncall Agent - it fails ON PURPOSE.

Trigger it manually from the Airflow UI ("Trigger DAG"). The `mode` parameter
chooses which kind of failure to produce:

* permanent (default): `transform` raises a KeyError (a code bug).
  Expected: the oncall agent classifies it non-retriable and opens a Linear
  issue (label oncall:non-retriable) assigned to the DAG owner.
* transient: `transform` fails on its first try with a connection timeout and
  succeeds when it is run again.
  Expected: the oncall agent retries the task automatically, verifies the
  success and resolves the incident without creating a Linear issue.

Self-contained (single file, no external connections) so it can be deployed
with submit_dag.sh. Airflow's own retries are disabled so that the failure
is visible to the oncall agent.
"""
import logging
from datetime import datetime

from airflow.providers.standard.operators.python import PythonOperator
from airflow.sdk import DAG, Param

log = logging.getLogger(__name__)


def extract(**context):
    rows = [{"order_id": 1, "amount": 120.0}, {"order_id": 2, "amount": 75.5}]
    log.info("Extracted %d rows", len(rows))
    return rows


def transform(**context):
    mode = context["params"]["mode"]
    try_number = context["ti"].try_number
    log.info("Running transform in mode=%s (try %s)", mode, try_number)

    if mode == "transient":
        if try_number < 2:
            # Looks like a temporary network problem; succeeds on the next try.
            raise ConnectionError(
                'connection to server at "10.0.5.20", port 5432 failed: Connection timed out'
            )
        log.info("Database reachable on retry - transform succeeded")
        return "ok"

    # permanent: a real code bug - every retry would fail the same way.
    row = {"order_id": 1, "amount": 120.0}
    log.info("Reading region code for order %s", row["order_id"])
    return row["region_code"]  # KeyError: 'region_code'


def load(**context):
    log.info("Loaded transformed data")


with DAG(
    dag_id="b001s02_oncall_test_dag",
    description="Fails on purpose to test the oncall agent (mode: permanent | transient)",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    default_args={"retries": 0},
    params={
        "mode": Param("permanent", type="string", enum=["permanent", "transient"],
                      description="permanent = code bug (Linear issue), transient = timeout (auto-retry)"),
    },
    tags=["b001s02", "oncall-test"],
) as dag:
    t_extract = PythonOperator(task_id="extract", python_callable=extract)
    t_transform = PythonOperator(task_id="transform", python_callable=transform)
    t_load = PythonOperator(task_id="load", python_callable=load)
    t_extract >> t_transform >> t_load
