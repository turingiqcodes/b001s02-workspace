import io

from oncall_agent.cli import main
from oncall_agent.config import Settings
from oncall_agent.mcp_servers.airflow_server import AirflowTools
from oncall_agent.mcp_servers.linear_server import LinearTools
from oncall_agent.simulate import run_all

from tests.conftest import COLUMN_LOG, NON_RETRIABLE, RETRIABLE, RUN, add_failed_task, build, only_incident, real_clears


def writes_settings(**kw):
    return Settings(force_mocks=True, database_path=":memory:", mcp_allow_airflow_writes=True, **kw)


def test_mcp_reads_work_and_writes_disabled_by_default():
    s = build({"timed out": RETRIABLE})
    add_failed_task(s.airflow)
    tools = AirflowTools(s)
    assert tools.list_task_instances("sales_etl_dag", RUN)
    assert "timed out" in tools.get_task_log("sales_etl_dag", RUN, "load", 1)
    assert tools.clear_failed_task("sales_etl_dag", RUN, "load")["ok"] is False
    assert "disabled" in tools.rerun_dag("appr-x")["error"]
    assert not s.airflow.write_calls


def test_mcp_clear_enforces_policy_and_limits():
    s = build({"UndefinedColumn": NON_RETRIABLE}, settings=writes_settings())
    add_failed_task(s.airflow, log=COLUMN_LOG)
    s.agent.run_cycle()
    out = AirflowTools(s).clear_failed_task("sales_etl_dag", RUN, "load")
    assert out["ok"] is False and out["decision"] == "non_retriable"
    assert not real_clears(s.airflow)


def test_mcp_clear_allowed_when_policy_allows_and_counts_against_limits():
    s = build({"timed out": {**RETRIABLE, "confidence": 0.9}}, settings=writes_settings(max_auto_retries=1))
    add_failed_task(s.airflow)
    s.agent.run_cycle()  # agent retries once (limit 1)
    s.airflow.retry_outcomes[("sales_etl_dag", RUN, "load")] = []
    s.airflow.tis[("sales_etl_dag", RUN, "load", -1)].state = "failed"
    s.airflow.tis[("sales_etl_dag", RUN, "load", -1)].try_number = 2
    out = AirflowTools(s).clear_failed_task("sales_etl_dag", RUN, "load")
    assert out["ok"] is False and any("already used 1 of 1" in r for r in out["reasons"])


def test_mcp_risky_action_needs_human_approval_and_runs_once():
    s = build({"UndefinedColumn": {**NON_RETRIABLE, "proposed_action": "mark_success"}}, settings=writes_settings())
    add_failed_task(s.airflow, log=COLUMN_LOG)
    s.agent.run_cycle()
    appr = s.store.all_approvals()[0]
    tools = AirflowTools(s)
    assert "no authorised human approval" in tools.mark_task_success(appr["id"])["error"]
    s.linear.human_comment(appr["linear_issue_id"], "/approve", "lin-carol")
    assert tools.mark_task_success(appr["id"])["ok"] is True
    assert tools.mark_task_success(appr["id"])["ok"] is False  # second call refused
    s.agent.run_cycle()
    assert len([c for c in s.airflow.write_calls if c["op"] == "set_task_instance_state"]) == 1


def test_linear_mcp_guardrails():
    s = build({"UndefinedColumn": NON_RETRIABLE})
    add_failed_task(s.airflow, log=COLUMN_LOG)
    s.agent.run_cycle()
    tools = LinearTools(s)
    issue_id = only_incident(s)["linear_issue_id"]
    assert tools.search_incidents("sales_etl")[0]["linear_issue_identifier"] == "ONC-1"
    assert tools.assign_issue(issue_id, "random-user")["ok"] is False
    assert tools.assign_issue(issue_id, "lin-bob")["ok"] is True
    assert tools.add_label(issue_id, "oncall:approved")["ok"] is False
    assert tools.add_label(issue_id, "made-up-label")["ok"] is False
    assert tools.create_issue("t", "d", assignee_id="intruder")["ok"] is False
    tools.add_comment(issue_id, "note")
    assert tools.get_issue(issue_id)["comments"]
    tools.resolve_issue(issue_id)
    assert only_incident(s)["status"] == "resolved"
    assert isinstance(tools.read_approvals(), list)


def test_simulation_all_scenarios_pass():
    out = io.StringIO()
    results = run_all(verbose=True, out=out)
    assert len(results) >= 6 and all(r.passed for r in results), out.getvalue()


def test_cli_check_and_listing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path.parent)
    # Never read the developer's real .env (it may hold real keys) and never touch real services.
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **kw: False)
    monkeypatch.setenv("ONCALL_FORCE_MOCKS", "true")
    for key in ("GROQ_API_KEY", "ANTHROPIC_API_KEY", "ONCALL_LLM_PROVIDER", "GITHUB_TOKEN", "LINEAR_API_KEY", "AIRFLOW_USERNAME", "AIRFLOW_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    monkeypatch.setenv("ONCALL_DATABASE_PATH", str(tmp_path / "cli.db"))
    monkeypatch.setenv("ONCALL_DAG_REPOS_PATH", str(root / "config/dag_repos.json"))
    monkeypatch.setenv("ONCALL_OWNERS_PATH", str(root / "config/owners.json"))
    assert main(["check"]) == 0
    out = capsys.readouterr().out
    for needle in ("Airflow: mock", "GitHub:  mock", "Linear:  mock", "LLM:     groq - mock (no GROQ_API_KEY", "SQLite:  available"):
        assert needle in out
    assert main(["once"]) == 0
    assert main(["incidents"]) == 0
    assert main(["audit", "missing-id"]) == 1
