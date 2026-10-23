"""
Week 6/7 pipeline lab - load the new pubs CSVs into pubs_b001s02.

start -> read_control_table
      -> insert_into_stage_1 -> qc_stage_1   (authors, publishers, jobs, stores)
      -> insert_into_stage_2 -> qc_stage_2   (titles, pub_info, employee, discounts)
      -> insert_into_stage_3 -> qc_stage_3   (titleauthor, sales, roysched)
      -> insert_into_public  -> qc_public
      -> update_control_table -> end

read_control_table:   tables in public.controltable with is_true = true.
                      Every later task only touches those tables.
insert_into_stage_N:  s3://.../raw/new_<table>.csv -> stage.<table>
                      (rows already in stage are skipped; rows with an
                      existing primary key are updated)
insert_into_public:   stage.<table> -> public.<table> (rows not yet in public)
qc_*:                 runs the src_query / tgt_query stored in public.qctable
                      for each table's qc_id; no QC SQL lives in this file.
update_control_table: sets last_inserted_ts for the loaded tables.

The groups run in foreign-key order (e.g. publishers before titles,
titles before sales).

Everything lives in this one file because submit_dag.sh deploys a single
file to Airflow; a separate sql/ file would not reach the server.

DB credentials come from the Airflow Connection "pubs_b001s02"
(Admin -> Connections in the Airflow UI), never from this file.
S3 access uses the "aws_default" connection; if it is not defined, boto3
falls back to the instance role / environment credentials.
All tasks are idempotent, so the DAG can be re-run safely.
"""
import csv
import io
import logging
from datetime import datetime

import psycopg2
from airflow.providers.amazon.aws.hooks.s3 import S3Hook
from airflow.providers.standard.operators.empty import EmptyOperator
from airflow.providers.standard.operators.python import PythonOperator
from airflow.sdk import DAG, BaseHook

CONN_ID = "pubs_b001s02"
AWS_CONN_ID = "aws_default"
S3_BUCKET = "turingiq-training-pipeline-data-b001s02"
S3_PREFIX = "raw/"

# table -> CSV file, primary key (None = no key, duplicates are skipped by
# comparing whole rows) and the qc_id prefix used in public.qctable.
TABLES = {
    "authors":     {"csv": "new_authors.csv",     "key": ["au_id"],   "qc": "authour"},
    "publishers":  {"csv": "new_publishers.csv",  "key": ["pub_id"],  "qc": "publishers"},
    "jobs":        {"csv": "new_jobs.csv",        "key": None,        "qc": "jobs"},
    "stores":      {"csv": "new_stores.csv",      "key": ["stor_id"], "qc": "stores"},
    "titles":      {"csv": "new_titles.csv",      "key": ["title_id"], "qc": "titles"},
    "pub_info":    {"csv": "new_pub_info.csv",    "key": ["pub_id"],  "qc": "pub_info"},
    "employee":    {"csv": "new_employees.csv",   "key": ["emp_id"],  "qc": "employee"},
    "discounts":   {"csv": "new_discounts.csv",   "key": None,        "qc": "discounts"},
    "titleauthor": {"csv": "new_titleauthor.csv", "key": ["au_id", "title_id"], "qc": "titleauthor"},
    "sales":       {"csv": "new_sales.csv",       "key": ["stor_id", "ord_num", "title_id"], "qc": "sales"},
    "roysched":    {"csv": "new_roysched.csv",    "key": None,        "qc": "roysches"},
}
GROUPS = [
    ["authors", "publishers", "jobs", "stores"],
    ["titles", "pub_info", "employee", "discounts"],
    ["titleauthor", "sales", "roysched"],
]

log = logging.getLogger(__name__)  # Airflow writes this to each task's log


def get_db_connection():
    c = BaseHook.get_connection(CONN_ID)
    return psycopg2.connect(host=c.host, port=c.port or 5432, dbname=c.schema,
                            user=c.login, password=c.password)


def enabled_tables(ti):
    return ti.xcom_pull(task_ids="read_control_table") or []


def read_csv_from_s3(csv_name):
    return S3Hook(aws_conn_id=AWS_CONN_ID).read_key(key=S3_PREFIX + csv_name,
                                                    bucket_name=S3_BUCKET)


def table_columns(cur, schema, table):
    cur.execute("SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
                (schema, table))
    return [r[0] for r in cur.fetchall()]


def stage_table(conn, table, body):
    """Load CSV text into stage.<table>; returns rows inserted or updated."""
    key = TABLES[table]["key"]
    header = next(csv.reader(io.StringIO(body, newline="")), None)
    if not header:
        raise ValueError(f"{TABLES[table]['csv']} is empty")
    cols = ", ".join(header)
    with conn.cursor() as cur:
        missing = set(header) - set(table_columns(cur, "stage", table))
        if missing:
            raise ValueError(f"{TABLES[table]['csv']} has columns not in stage.{table}: "
                             f"{sorted(missing)}")
        # Typed scratch table, so COPY turns empty CSV fields into NULLs.
        cur.execute(f"CREATE TEMP TABLE csv_rows ON COMMIT DROP AS "
                    f"SELECT {cols} FROM stage.{table} WITH NO DATA")
        cur.copy_expert(f"COPY csv_rows ({cols}) FROM STDIN WITH (FORMAT csv, HEADER true)",
                        io.StringIO(body))
        sql = (f"INSERT INTO stage.{table} ({cols}) "
               f"SELECT {cols} FROM csv_rows EXCEPT SELECT {cols} FROM stage.{table}")
        if key:
            updates = [f"{c} = EXCLUDED.{c}" for c in header if c not in key]
            sql += f" ON CONFLICT ({', '.join(key)}) DO "
            sql += f"UPDATE SET {', '.join(updates)}" if updates else "NOTHING"
        cur.execute(sql)
        n = cur.rowcount
        cur.execute("DROP TABLE csv_rows")
    return n


def publish_table(conn, table):
    """Copy stage.<table> rows that are not yet in public.<table>."""
    with conn.cursor() as cur:
        cols = ", ".join(table_columns(cur, "stage", table))
        cur.execute(f"INSERT INTO public.{table} ({cols}) "
                    f"SELECT {cols} FROM stage.{table} "
                    f"EXCEPT SELECT {cols} FROM public.{table} "
                    f"ON CONFLICT DO NOTHING")
        return cur.rowcount


def qc_value(cur, query):
    # A plain number in qctable (e.g. "1") is used as the value itself.
    if query.strip().lstrip("-").isdigit():
        return int(query)
    cur.execute(query)
    return cur.fetchone()[0]


def run_qc(conn, qc_id):
    """Run one public.qctable check.

    Passes when tgt >= src and, if threshold is set, tgt - src <= threshold
    (threshold = rows public may hold beyond stage, e.g. pre-existing data).
    """
    with conn.cursor() as cur:
        cur.execute("SELECT src_query, tgt_query, threshold FROM public.qctable "
                    "WHERE qc_id = %s", (qc_id,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"qc_id '{qc_id}' not found in public.qctable")
        src_query, tgt_query, threshold = row
        src, tgt = qc_value(cur, src_query), qc_value(cur, tgt_query)
    diff = tgt - src
    ok = diff >= 0 and (threshold is None or diff <= threshold)
    log.info("QC %s: src=%s tgt=%s diff=%s threshold=%s -> %s",
             qc_id, src, tgt, diff, threshold, "PASS" if ok else "FAIL")
    return ok


def run_qc_checks(tables, suffix):
    failed = []
    with get_db_connection() as conn:
        for table in tables:
            qc_id = f"{TABLES[table]['qc']}_{suffix}"
            try:
                ok = run_qc(conn, qc_id)
            except (psycopg2.Error, ValueError) as e:
                log.error("QC %s errored: %s", qc_id, e)
                conn.rollback()
                ok = False
            if not ok:
                failed.append(qc_id)
    if failed:
        raise ValueError(f"QC failed: {failed}")


def read_control_table():
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT table_name FROM public.controltable WHERE is_true")
        tables = [r[0] for r in cur.fetchall()]
    unknown = sorted(set(tables) - set(TABLES))
    if unknown:
        raise ValueError(f"controltable has tables this DAG cannot load: {unknown}")
    log.info("Enabled tables: %s", tables)
    return tables


def insert_into_stage(group, ti):
    tables = [t for t in GROUPS[group] if t in enabled_tables(ti)]
    if not tables:
        log.info("No enabled tables in this group")
    with get_db_connection() as conn:  # commits on success, rolls back on error
        for table in tables:
            csv_name = TABLES[table]["csv"]
            log.info("Reading s3://%s/%s%s", S3_BUCKET, S3_PREFIX, csv_name)
            n = stage_table(conn, table, read_csv_from_s3(csv_name))
            log.info("Inserted/updated %d rows in stage.%s", n, table)


def qc_stage(group, ti):
    run_qc_checks([t for t in GROUPS[group] if t in enabled_tables(ti)], "csv_to_stage")


def insert_into_public(ti):
    tables = [t for g in GROUPS for t in g if t in enabled_tables(ti)]
    with get_db_connection() as conn:
        for table in tables:  # GROUPS order keeps foreign keys satisfied
            n = publish_table(conn, table)
            log.info("Inserted %d new rows into public.%s", n, table)


def qc_public(ti):
    run_qc_checks([t for g in GROUPS for t in g if t in enabled_tables(ti)],
                  "stage_to_public")


def update_control_table(ti):
    tables = enabled_tables(ti)
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("UPDATE public.controltable SET last_inserted_ts = CURRENT_TIMESTAMP "
                    "WHERE table_name = ANY(%s)", (tables,))
        log.info("Updated last_inserted_ts for %d tables", cur.rowcount)


with DAG(
    dag_id="b001s02_pipeline_dag",
    description="new_*.csv -> stage -> public, with QC from public.qctable",
    start_date=datetime(2026, 1, 1),
    schedule=None,
    catchup=False,
    tags=["b001s02", "pubs"],
) as dag:
    start = EmptyOperator(task_id="start")
    control = PythonOperator(task_id="read_control_table",
                             python_callable=read_control_table)
    steps = [start, control]
    for i in range(len(GROUPS)):
        steps.append(PythonOperator(task_id=f"insert_into_stage_{i + 1}",
                                    python_callable=insert_into_stage,
                                    op_kwargs={"group": i}))
        steps.append(PythonOperator(task_id=f"qc_stage_{i + 1}",
                                    python_callable=qc_stage,
                                    op_kwargs={"group": i}))
    steps += [
        PythonOperator(task_id="insert_into_public", python_callable=insert_into_public),
        PythonOperator(task_id="qc_public", python_callable=qc_public),
        PythonOperator(task_id="update_control_table", python_callable=update_control_table),
        EmptyOperator(task_id="end"),
    ]
    for upstream, downstream in zip(steps, steps[1:]):
        upstream >> downstream
