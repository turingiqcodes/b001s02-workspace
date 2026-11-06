import json
import sqlite3
from pathlib import Path

import pytest

from oncall_agent.audit import AuditLogger, Redactor
from oncall_agent.config import ConfigError, load_dag_repos, load_owners, load_settings, validate_cross_references
from oncall_agent.store import Store, StoreError

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def store(tmp_path):
    return Store(str(tmp_path / "t.db"))


def _incident(store, fp="fp1", key="k1"):
    return store.create_incident({"fingerprint": fp, "kind": "task_instance", "dag_id": "d", "task_id": "t",
                                  "run_id": "r"}, key, {})


def test_incident_and_occurrence_persist_across_reopen(tmp_path):
    path = str(tmp_path / "p.db")
    s = Store(path)
    iid = _incident(s)
    s.close()
    s2 = Store(path)
    assert s2.get_incident(iid)["fingerprint"] == "fp1"
    assert s2.occurrence_seen("k1")
    assert s2.find_open_incident("fp1")["id"] == iid


def test_audit_is_append_only(store):
    AuditLogger(store).record("detection", "x")
    with pytest.raises((StoreError, sqlite3.IntegrityError), match="append-only"):
        with store.transaction() as conn:
            conn.execute("UPDATE audit SET action='tampered'")
    with pytest.raises((StoreError, sqlite3.IntegrityError), match="append-only"):
        with store.transaction() as conn:
            conn.execute("DELETE FROM audit")
    assert store.list_audit()[0]["action"] == "x"


def test_audit_record_fields_and_redaction(store):
    audit = AuditLogger(store, secrets=["supersecretvalue"])
    audit.record("external_api", "call", incident_id="inc-1",
                 input={"token": "supersecretvalue", "hdr": "Bearer abcdefghijklmnopqrstuvwxyz"},
                 output="key sk-ant-api03-ABCDEFGHIJKLMNOP and ghp_abcdefghijklmnopqrstuvwx", error="password=hunter22")
    row = store.audit_for_incident("inc-1")[0]
    for field in ("id", "ts", "incident_id", "event_type", "action", "input_summary", "output_summary", "result"):
        assert row[field]
    blob = json.dumps(row)
    for secret in ("supersecretvalue", "abcdefghijklmnopqrstuvwxyz", "sk-ant-api03", "ghp_abc", "hunter22"):
        assert secret not in blob


def test_retry_rows_are_idempotent_per_failed_try(store):
    iid = _incident(store)
    first = store.create_retry(iid, "d", "r", "t", -1, 1, ["ok"])
    assert first is not None
    assert store.create_retry(iid, "d", "r", "t", -1, 1, ["again"]) is None  # same try -> refused
    assert store.create_retry(iid, "d", "r", "t", -1, 2, ["next try"]) is not None
    assert store.count_task_retries("d", "r", "t", -1) == 2
    assert store.count_retries_last_hour() == 2


def test_approval_can_only_be_claimed_once(store):
    iid = _incident(store)
    aid = store.create_approval(iid, "issue-1", "rerun_dag", ["risky"])
    assert store.create_approval(iid, "issue-1", "rerun_dag", ["dup"]) == aid
    assert store.claim_approval(aid, "label", "u1") is True
    assert store.claim_approval(aid, "comment", "u2") is False
    assert store.get_approval(aid)["approved_by"] == "u1"


def test_watermark_and_outbox(store):
    assert store.get_watermark()["value"] is None
    store.set_watermark("2026-11-06T09:00:00Z", {"x": 1})
    assert store.get_watermark()["value"] == "2026-11-06T09:00:00Z"
    iid = _incident(store)
    assert store.enqueue_linear_op(iid, "create_issue", {"t": 1}) is True
    assert store.enqueue_linear_op(iid, "create_issue", {"t": 2}) is False  # deduplicated
    assert len(store.pending_linear_ops()) == 1


def test_unopenable_database_raises_store_error(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(StoreError):
        Store(str(blocker / "sub" / "db.sqlite"))


def test_redactor_handles_none():
    assert Redactor(["abcd1234"])(None) is None


# ---- config -----------------------------------------------------------------------------
def test_settings_from_env_and_validation():
    s = load_settings({"ONCALL_MAX_AUTO_RETRIES": "0", "ANTHROPIC_API_KEY": "sk-test-SECRET-123", "AIRFLOW_USERNAME": "u",
                       "AIRFLOW_PASSWORD": "p"})
    assert s.max_auto_retries == 0 and s.airflow_is_real
    assert s.llm_provider == "groq" and not s.llm_is_real  # Groq is the default; no GROQ_API_KEY given
    assert s.poll_interval_seconds == 60
    assert "SECRET-123" not in repr(s)
    with pytest.raises(ConfigError):
        load_settings({"ONCALL_POLL_INTERVAL_SECONDS": "soon"})
    g = load_settings({"GROQ_API_KEY": "gsk_test_SECRET-456"})
    assert g.llm_is_real and g.groq_model == "openai/gpt-oss-120b" and g.groq_reasoning_effort == "medium"
    assert "SECRET-456" not in repr(g) and "gsk_test_SECRET-456" in g.secrets()
    assert not load_settings({"GROQ_API_KEY": "k", "ONCALL_FORCE_MOCKS": "true"}).llm_is_real
    c = load_settings({"ONCALL_LLM_PROVIDER": "claude", "ANTHROPIC_API_KEY": "k"})
    assert c.llm_is_real and c.llm_model == "claude-opus-5-5"
    with pytest.raises(ConfigError):
        load_settings({"ONCALL_LLM_PROVIDER": "openai"})
    with pytest.raises(ConfigError):
        load_settings({"GROQ_REASONING_EFFORT": "extreme"})


def test_example_config_files_are_valid():
    repos = load_dag_repos(str(ROOT / "config/dag_repos.example.json"))
    owners = load_owners(str(ROOT / "config/owners.example.json"))
    assert validate_cross_references(repos, owners) == []
    assert repos.is_no_retry("production_payment_dag")


def test_invalid_config_files_are_rejected(tmp_path):
    bad = tmp_path / "o.json"
    bad.write_text(json.dumps({"developers": {}, "default_owner": ""}))
    with pytest.raises(ConfigError):
        load_owners(str(bad))
    bad.write_text("{oops")
    with pytest.raises(ConfigError):
        load_owners(str(bad))
    repos = tmp_path / "r.json"
    repos.write_text(json.dumps({"dags": {"d": {"repo": "no-slash", "dag_file_path": "x.py"}}}))
    with pytest.raises(ConfigError):
        load_dag_repos(str(repos))
