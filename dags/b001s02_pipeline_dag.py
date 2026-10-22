"""
Week 6/7 pipeline lab - load new authors into pubs_b001s02.

start -> insert_into_stage -> insert_into_public -> end

1. insert_into_stage:  s3://.../raw/new_authors.csv -> stage.authors
                       (upsert on au_id)
2. insert_into_public: stage.authors    -> public.authors (new au_ids only),
                       by running sql/insert_into_public.sql

DB credentials come from the Airflow Connection "pubs_b001s02"
(Admin -> Connections in the Airflow UI), never from this file.
S3 access uses the "aws_default" connection; if it is not defined, boto3
falls back to the instance role / environment credentials.
Both tasks are idempotent, so the DAG can be re-run safely.
"""
import csv
import io
import logging
import os
from datetime import datetime

import psycopg2
from airflow.providers.amazon.aws.hooks.s3 import S3Hook
from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.providers.standard.operators.python import PythonOperator
from airflow.sdk import DAG, BaseHook

CONN_ID = "pubs_b001s02"
AWS_CONN_ID = "aws_default"
S3_BUCKET = "turingiq-training-pipeline-data-b001s02"
S3_KEY = "raw/new_authors.csv"
CSV_PATH = f"s3://{S3_BUCKET}/{S3_KEY}"  # used in log messages
DAG_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_SQL_PATH = os.path.join(DAG_DIR, "sql", "insert_into_public.sql")
COLUMNS = ["au_id", "au_lname", "au_fname", "phone", "address",
           "city", "state", "zip", "contract"]

log = logging.getLogger(__name__)  # Airflow writes this to each task's log


def get_db_connection():
    c = BaseHook.get_connection(CONN_ID)
    return psycopg2.connect(host=c.host, port=c.port or 5432, dbname=c.schema,
                            user=c.login, password=c.password)


def read_authors_csv(bucket=S3_BUCKET, key=S3_KEY):
    body = S3Hook(aws_conn_id=AWS_CONN_ID).read_key(key=key, bucket_name=bucket)
    reader = csv.DictReader(io.StringIO(body, newline=""))
    missing = set(COLUMNS) - set(reader.fieldnames or [])
    if missing:
        raise ValueError(f"s3://{bucket}/{key} is missing columns: {sorted(missing)}")
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


def publish_authors(conn, sql_path=PUBLIC_SQL_PATH):
    with open(sql_path) as f:
        sql = f.read()
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.rowcount


def insert_into_stage():
    log.info("insert_into_stage: reading %s", CSV_PATH)
    rows = read_authors_csv()
    log.info("Read %d rows from CSV", len(rows))
    if not rows:
        log.warning("CSV has no data rows; nothing to stage")
    with get_db_connection() as conn:  # commits on success, rolls back on error
        log.info("Connected via Airflow connection '%s'", CONN_ID)
        n = stage_authors(conn, rows)
    log.info("Upserted %d rows from %s into stage.authors", n, CSV_PATH)


def insert_into_public():
    log.info("insert_into_public: running %s", PUBLIC_SQL_PATH)
    with get_db_connection() as conn:
        log.info("Connected via Airflow connection '%s'", CONN_ID)
        n = publish_authors(conn)
    log.info("Inserted %d new rows into public.authors", n)
    if n == 0:
        log.info("No new au_ids in stage.authors (already published)")


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
