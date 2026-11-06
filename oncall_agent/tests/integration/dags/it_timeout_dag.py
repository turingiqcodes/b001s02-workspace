"""Integration-test DAG: the run exceeds dagrun_timeout, so the DAG run fails
without any failed task instance."""
import time
from datetime import datetime, timedelta

from airflow.sdk import dag, task


@dag(dag_id="it_timeout_dag", schedule=None, start_date=datetime(2026, 1, 1), catchup=False,
     dagrun_timeout=timedelta(seconds=20), default_args={"retries": 0}, tags=["oncall-it"])
def it_timeout_dag():
    @task
    def slow():
        time.sleep(300)

    slow()


it_timeout_dag()
