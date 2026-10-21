"""
Week 6/7 pipeline lab - load new authors into pubs_b001s02.

start -> insert_into_stage -> insert_into_public -> end

1. insert_into_stage:  new_authors.csv  -> stage.authors  (upsert on au_id)
2. insert_into_public: stage.authors    -> public.authors (new au_ids only)

DB credentials come from the Airflow Connection "pubs_b001s02"
(Admin -> Connections in the Airflow UI), never from this file.
Both tasks are idempotent, so the DAG can be re-run safely.
"""
import csv
from datetime import datetime

import psycopg2
from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.providers.standard.operators.python import PythonOperator
from airflow.sdk import DAG, BaseHook

CONN_ID = "pubs_b001s02"
CSV_PATH = "/home/training/pipeline-data/raw/new_authors.csv"
COLUMNS = ["au_id", "au_lname", "au_fname", "phone", "address",
           "city", "state", "zip", "contract"]


def get_db_connection():
    c = BaseHook.get_connection(CONN_ID)
    return psycopg2.connect(host=c.host, port=c.port or 5432, dbname=c.schema,
                            user=c.login, password=c.password)


def read_authors_csv(path=CSV_PATH):
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        missing = set(COLUMNS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path} is missing columns: {sorted(missing)}")
        return [tuple(row[col] for col in COLUMNS) for row in reader]


def stage_authors(conn, rows):
    cols = ", ".join(COLUMNS)
    updates = ", ".join(f"{col} = EXCLUDED.{col}" for col in COLUMNS[1:])
    with conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO stage.authors ({cols}) "
            f"VALUES ({', '.join(['%s'] * len(COLUMNS))}) "
            f"ON CONFLICT (au_id) DO UPDATE SET {updates}",
            rows,
        )
    return len(rows)


def publish_authors(conn):
    cols = ", ".join(COLUMNS)
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO public.authors ({cols}) "
            f"SELECT {cols} FROM stage.authors "
            f"ON CONFLICT (au_id) DO NOTHING"
        )
        return cur.rowcount


def insert_into_stage():
    rows = read_authors_csv()
    with get_db_connection() as conn:  # commits on success, rolls back on error
        n = stage_authors(conn, rows)
    print(f"Upserted {n} rows from {CSV_PATH} into stage.authors")


def insert_into_public():
    with get_db_connection() as conn:
        n = publish_authors(conn)
    print(f"Inserted {n} new rows from stage.authors into public.authors")


with DAG(
    dag_id="b001s02_pipeline_dag",
    description="new_authors.csv -> stage.authors -> public.authors",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    tags=["b001s02", "pubs"],
) as dag:
    start = EmptyOperator(task_id="start")
    load_stage = PythonOperator(task_id="insert_into_stage",
                                python_callable=insert_into_stage)
    load_public = PythonOperator(task_id="insert_into_public",
                                 python_callable=insert_into_public)
    end = EmptyOperator(task_id="end")

    start >> load_stage >> load_public >> end
