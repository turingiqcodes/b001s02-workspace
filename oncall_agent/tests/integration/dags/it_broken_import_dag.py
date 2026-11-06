"""Integration-test DAG: deliberately fails to import (import error detection)."""
from datetime import datetime

import oncall_it_module_that_does_not_exist  # noqa: F401

from airflow.sdk import dag


@dag(dag_id="it_broken_import_dag", schedule=None, start_date=datetime(2026, 1, 1))
def it_broken_import_dag():
    pass


it_broken_import_dag()
