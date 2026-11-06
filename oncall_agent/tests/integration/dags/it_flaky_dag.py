"""Integration-test DAG: fails with a transient-looking error on the first try,
succeeds when the oncall agent clears (retries) it."""
from datetime import datetime

from airflow.sdk import dag, get_current_context, task


@dag(dag_id="it_flaky_dag", schedule=None, start_date=datetime(2026, 1, 1), catchup=False,
     default_args={"retries": 0}, tags=["oncall-it"])
def it_flaky_dag():
    @task
    def extract():
        return 1

    @task
    def load(value):
        ti = get_current_context()["ti"]
        if ti.try_number < 2:
            raise ConnectionError("connection to server at 10.0.0.5 port 5432 failed: Connection timed out")
        return value

    @task
    def publish(value):
        return value

    publish(load(extract()))


it_flaky_dag()
