from oncall_agent.fingerprint import fingerprint, normalize_error_line, pick_error_line


def test_same_error_with_changing_values_gives_same_fingerprint():
    a = ("[2026-11-04T08:01:02Z] ERROR - run manual__2026-11-04T08:00:00+00:00\n"
         "psycopg2.OperationalError: connection to server at \"10.0.3.15\", port 5432 failed after 30 seconds")
    b = ("[2026-11-06T09:41:55.123Z] ERROR - run scheduled__2026-11-06T09:00:00+00:00\n"
         "psycopg2.OperationalError: connection to server at \"10.9.8.7\", port 6543 failed after 12 seconds")
    assert fingerprint("dag", "task", a)[0] == fingerprint("dag", "task", b)[0]


def test_different_errors_dags_or_tasks_give_different_fingerprints():
    base = fingerprint("dag", "task", "KeyError: 'a'")[0]
    assert fingerprint("dag", "task", "ValueError: bad")[0] != base
    assert fingerprint("dag2", "task", "KeyError: 'a'")[0] != base
    assert fingerprint("dag", "task2", "KeyError: 'a'")[0] != base


def test_fingerprint_is_deterministic_sha256():
    fp, norm = fingerprint("d", None, "ValueError: x")
    assert len(fp) == 64 and fp == fingerprint("d", None, "ValueError: x")[0]
    assert norm == "ValueError: x"


def test_picks_exception_line_from_traceback():
    tb = ("Traceback (most recent call last):\n  File \"x.py\", line 3, in <module>\n    foo()\n"
          "KeyError: 'region_code'\nINFO - Marking task as FAILED.")
    assert pick_error_line(tb) == "KeyError: 'region_code'"


def test_normalization_removes_volatile_values():
    line = normalize_error_line(
        "TimeoutError: request 3f2a1b4c-1111-2222-3333-444455556666 to 192.168.1.20:8080 at "
        "2026-11-06 10:00:00 took 31.5s (0xdeadbeef)")
    for volatile in ("192.168", "2026", "31.5", "deadbeef", "3f2a1b4c"):
        assert volatile not in line
    assert line.startswith("TimeoutError: request <UUID> to <IP>")


def test_empty_error_is_handled():
    assert normalize_error_line("") == ""
    assert len(fingerprint("d", "t", "")[0]) == 64


def test_numbers_with_units_are_normalized():
    # dagrun_timeout messages carry the run duration, which differs on every run.
    a = "DAG run failed: dagrun_timeout exceeded (task states: skipped=1); duration 21s; dagrun_timeout 20s"
    b = "DAG run failed: dagrun_timeout exceeded (task states: skipped=1); duration 3660s; dagrun_timeout 3600s"
    assert fingerprint("d", None, a) == fingerprint("d", None, b)
    assert normalize_error_line("Timeout after 30.5ms") == "Timeout after <N>ms"
    assert normalize_error_line("md5 mismatch on v2") == "md5 mismatch on v2"


def test_lowercase_airflow3_log_level_is_stripped():
    # Airflow 3 structured logs are rendered as "<timestamp> <level> <event>" with a lower-case level.
    log = ("2026-11-06T07:03:58.820126Z info QC authour_stage_to_public: diff=20 -> FAIL\n"
           "2026-11-06T07:03:58.888600Z info Failure caused by QC failed: ['authour_stage_to_public']")
    assert pick_error_line(log) == "Failure caused by QC failed: ['authour_stage_to_public']"
    # The level no longer hides an exception line from the exception-first preference.
    log = ("2026-11-06T07:03:58.1Z error ValueError: bad input\n"
           "2026-11-06T07:03:58.2Z info Task failed with exception")
    assert pick_error_line(log) == "ValueError: bad input"
    # Airflow 2 upper-case format still works; mixed-case message words are kept.
    assert pick_error_line("[2026-11-06 07:03:58,820] {x.py:12} ERROR - boom failed") == "boom failed"
    assert pick_error_line("2026-11-06T07:03:58Z Error connecting to db") == "Error connecting to db"
    assert pick_error_line("2026-11-06T07:03:58Z information unavailable") == "information unavailable"
