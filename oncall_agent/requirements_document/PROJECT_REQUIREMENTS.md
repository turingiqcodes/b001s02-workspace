# Airflow Oncall Agent — Project Requirements (v1.0)

The primary source of requirements is the Software Requirements Specification (SRS) v1.0 for the Airflow Oncall Agent (target: Apache Airflow 3.x, model `claude-opus-5-5`, Python). This document:

1. summarises every requirement ID,
2. records the decisions made where the SRS was open ("ask the developer"), and
3. maps every requirement to code and tests (traceability).

---

## 1. Requirement summary

| ID | Requirement (summary) |
|---|---|
| FR-001 | Polling cycle every `ONCALL_POLL_INTERVAL_SECONDS` (default 60): verify retries → approvals → open-incident follow-ups → detect → process → watermark → audit. Graceful shutdown. |
| FR-002 | Detect failed task instances, failed DAG runs without failed tasks (e.g. `dagrun_timeout`) and DAG import errors using only the Airflow REST API. No callbacks, plugins or host changes. |
| FR-003 | Persistent watermark in SQLite. Only advanced after work is persisted, held during Airflow outages, resumed after restart, overlap-safe. |
| FR-004 | Collect the Airflow context (ids, state, timestamps, error, log tail, DAG details/source, last 10 runs) and the GitHub context (commits touching the DAG file and `extra_paths`, authors, messages, patches) using `dag_repos.json`. Failures become *context gaps*. |
| FR-005 | Claude analysis returns schema-validated JSON (`classification`, `confidence`, `root_cause`, `evidence`, `suggested_fix`, `proposed_action`, `suspected_commit`). Invalid output is handled safely and never causes a retry. |
| FR-006 | Rule-based analyzer when Claude is unavailable. Same format, confidence ≤ 0.5, so it never triggers a retry. Issues are still created. |
| FR-007 | Fingerprint = `sha256(dag_id | task_id | normalized_error_line)`. Deduplicate: comment on the open issue, record repeats, and find issues in Linear after a restart. |
| FR-008 | Deterministic decision policy: `retry` / `non_retriable` / `needs_human` / `approval_required`. Auto-retry only if ALL ten conditions hold. Exact reasons are recorded. |
| FR-009 | Retry only the failed task plus its `upstream_failed` children. Never clear succeeded tasks. Idempotent across restarts. |
| FR-010 | Verify retries: resolve on success, escalate on failure or limit, and escalate if still running after 30 minutes (configurable). |
| FR-011 | Human approval for `rerun_dag` / `mark_success` via the `oncall:approved` label or a `/approve` comment. Each approval executes exactly once and approval state is persisted. |
| FR-012 | Linear issues with all required fields, labels `oncall:non-retriable` / `oncall:needs-human` (High priority) / `oncall:approval-required`, safe handling of missing labels, and idempotent creation with an outbox for Linear outages. |
| FR-013 | Ownership: suspected commit author → DAG owner → default owner (`config/owners.json`). The selection and the reason for it are explained in every issue. No invented identities. |
| FR-014 | SQLite entities: incidents, retries, approvals, watermark and an append-only audit log with the required fields. No secrets. JSON log lines. |
| FR-015 | MCP servers `oncall-airflow-mcp` and `oncall-linear-mcp` sharing the agent's clients. Airflow writes are off by default and never bypass the policy or approvals. |
| FR-016 | Configuration through `.env` plus JSON files, validated at startup. `.env.example` and `*.example.json` provided. |
| FR-017 | CLI: `check`, `run`, `once`, `incidents`, `audit <id>`, `simulate -v`. |
| FR-018 | Six offline simulation scenarios. |
| NFR-001 | Resilience to Airflow, GitHub, Linear, Claude and SQLite failures. |
| NFR-002 | Security: secrets only in env/SSM, read-only GitHub, Airflow Op role, prompt-injection defences, validated Claude output, no permanent deletion. |
| NFR-003 | Tests run with no keys, Docker, network or Airflow. Integration tests use a throwaway Airflow 3.x and DAGs in `tests/integration/dags/`. |

Out of scope (not implemented, by design): automatic code edits or pushes, Slack/email, Airflow plugins/callbacks, user or connection management, a web UI, automatic approval, and permanent deletion.

---

## 2. Decisions for open points

The SRS asked the developer to decide these points. The developer asked for implementation "according to the SRS" without choosing, so the recommended option was used in each case. Each one can be changed easily.

| # | Open point | Decision |
|---|---|---|
| D1 | Lifecycle of resolved incidents | A recurrence after resolution creates a **new** incident and a new Linear issue that links to the previous issue. The agent resolves incidents automatically only when (a) an automatic retry succeeds, (b) an approved action succeeds, or (c) an import error disappears from Airflow. Otherwise a human closes the issue in Linear and the agent picks that up. |
| D2 | Who may approve | Only Linear user ids listed in `owners.json → approvers`. For `/approve` the comment author is checked; for `oncall:approved` the actor who added the label is read from the issue history. Signals from anyone else are ignored and audited. |
| D3 | Meaning of `rerun_dag` | Clear the whole failed DAG run (`POST /dags/{id}/dagRuns/{run}/clear`). This resets succeeded tasks as well, which is acceptable only because a human approved it. `mark_success` sets the failed task instance to `success` (or the DAG run, for DAG-run failures). |
| D4 | Scope of the "2 automatic retries" limit | Counted **per task instance** (dag, run, task, map_index) and equal to `ONCALL_MAX_AUTO_RETRIES`. The hourly cap (`ONCALL_MAX_RETRIES_PER_HOUR`) applies globally. |
| D5 | Integration-test Airflow | A throwaway local Airflow 3.x (`airflow standalone`). The tests skip automatically unless `ONCALL_IT_AIRFLOW_URL` is set. |
| D6 | Detection strategy | Query by `end_date >= watermark − overlap` (10 minutes by default). Each occurrence key is stored, so overlaps are never processed twice. The watermark is the cycle start time, and it advances only when every detected failure was persisted. |
| D7 | Interrupted actions | A retry or approval interrupted mid-execution (crash or restart) is **never** re-sent automatically. It is reconciled from live Airflow state or escalated to a human. |
| D9 | LLM provider (requirement change by the developer) | The developer asked to **use the Groq API instead of Claude**. Groq is the default provider (`GROQ_API_KEY`, `GROQ_MODEL=openai/gpt-oss-120b`). Every SRS rule that mentions "Claude" now applies to the configured LLM: recommendations only, schema-validated JSON, rule-based fallback capped at 0.5. The Claude client remains as an optional provider (`ONCALL_LLM_PROVIDER=claude`). |
| D8 | Missing Linear labels | Labels are never created automatically. The issue is created without the missing label, carries a visible warning, and the event is audited. `oncall-agent check` lists missing labels. |

Additional settings beyond the SRS (justified):
* `ONCALL_DETECTION_OVERLAP_MINUTES`: the size of the re-read window used by D6.
* `ONCALL_INITIAL_LOOKBACK_HOURS`: how far back the very first cycle looks.
* `ONCALL_FORCE_MOCKS`: forces offline mode for demos.
* `ONCALL_DAG_REPOS_PATH` / `ONCALL_OWNERS_PATH`: config file locations.

---

## 3. Traceability

| ID | Code | Tests |
|---|---|---|
| FR-001 | `agent.py` (`run_cycle`, `run_forever`, `CycleLock`) | `test_acceptance.py`, `test_mcp_cli_sim.py::test_cli_check_and_listing` |
| FR-002 | `detection.py`, `integrations/airflow.py` | acceptance tests 1–3, integration tests |
| FR-003 | `detection.py`, `store.py` (watermark) | `test_hold_watermark_during_airflow_outage`, `test_detect_failure_after_restart`, `test_failure_that_ended_long_before_restart_is_found_via_overlap_window` |
| FR-004 | `context.py` | `test_github_outage_records_gap_and_uses_dag_owner`, simulation scenario 2 |
| FR-005 | `analysis.py`, `models.py` (`Analysis`, schema), `integrations/groq.py` (default), `integrations/claude.py` (optional) | `test_analysis.py`, `test_analyze_with_llm`, `test_real_clients.py` (Groq + Claude) |
| FR-006 | `analysis.py` (`RuleBasedAnalyzer`) | `test_rule_based_transient_and_permanent`, `test_fallback_analyzer_never_retries_but_creates_issue` |
| FR-007 | `fingerprint.py`, `agent.py` (`_handle_repeat`, `_adopt_linear_incident`) | `test_fingerprint.py`, duplicate tests, scenario 5 |
| FR-008 | `policy.py` | `test_policy.py` (every condition), acceptance tests 10–12 |
| FR-009 | `retry_manager.py` | `test_retry_clears_only_failed_and_upstream_failed`, `test_retry_aborted_if_dry_run_would_touch_successful_task`, `test_retry_is_not_repeated_after_restart_mid_retry` |
| FR-010 | `retry_manager.py` (`verify_pending`) | `test_verify_successful_retry_resolves_without_issue`, `test_escalate_*`, integration retry test |
| FR-011 | `approval_manager.py`, `store.claim_approval` | `test_approved_action_executes_exactly_once`, `test_invalid_approvals_are_ignored`, `test_interrupted_approval_is_not_rerun`, scenario 6 |
| FR-012 | `incident_manager.py`, `reporting.py`, `integrations/linear.py` | `test_issue_contains_all_required_fields`, `test_linear_outage_queues_issue_and_creates_it_once_later`, `test_issue_created_but_id_lost_is_adopted_not_duplicated`, `test_missing_linear_label_is_not_created_and_is_reported` |
| FR-013 | `ownership.py` | `test_ownership.py`, scenario 2 |
| FR-014 | `store.py`, `audit.py` | `test_store_audit_config.py` |
| FR-015 | `mcp_servers/` | `test_mcp_cli_sim.py` |
| FR-016 | `config.py`, `factory.load_configs` | `test_store_audit_config.py` (config) |
| FR-017 | `cli.py` | `test_cli_check_and_listing` |
| FR-018 | `simulate.py` | `test_simulation_all_scenarios_pass` |
| NFR-001 | `agent.py`, `incident_manager.py` (outbox), `analysis.py` | outage tests, `test_sqlite_unavailable_means_no_retry` |
| NFR-002 | `analysis.py` (untrusted blocks), `audit.Redactor`, `approval_manager.py` | `test_untrusted_content_cannot_close_its_block`, `test_log_text_cannot_authorize_an_action`, `test_audit_record_fields_and_redaction` |
| NFR-003 | `integrations/mocks/`, `tests/` | entire suite (offline), `tests/integration/` |
