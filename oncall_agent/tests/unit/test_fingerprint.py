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
