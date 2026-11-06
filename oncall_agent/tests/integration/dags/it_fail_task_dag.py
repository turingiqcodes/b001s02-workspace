"""Integration-test DAG: a task that always fails with a non-transient error."""
from datetime import datetime

from airflow.sdk import dag, task


@dag(dag_id="it_fail_task_dag", schedule=None, start_date=datetime(2026, 1, 1), catchup=False,
     default_args={"retries": 0}, tags=["oncall-it"])
def it_fail_task_dag():
    @task
    def extract():
        return 1

    @task
    def transform(value):
        raise KeyError("region_code")

    @task
    def publish(value):
        return value

    publish(transform(extract()))


it_fail_task_dag()
