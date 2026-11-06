# Airflow Oncall Agent

An AI-assisted on-call engineer for **Apache Airflow 3.x**. It runs as its own Python process, watches Airflow through the REST API, and for every failure it:

1. detects the failure: failed tasks, failed DAG runs with no failed task (e.g. `dagrun_timeout`), and DAG import errors;
2. collects context: task log tail, DAG details and source, the last 10 runs, recent GitHub commits and patches;
3. asks an **LLM via the Groq API** (default model `openai/gpt-oss-120b`) for a structured diagnosis, or uses a rule-based analyzer if there is no API key;
4. passes the diagnosis to a **deterministic Python policy**. the LLM only recommends; the policy decides;
5. **automatically retries** only safe, transient task failures, within strict limits, and verifies the result;
6. opens **one Linear issue per unique failure**, assigned to the most likely responsible developer;
7. holds **risky actions** (`rerun_dag`, `mark_success`) until a human approves them, then runs them exactly once;
8. records everything in an **append-only SQLite audit log** and in JSON log lines.

You need no DAG changes, callbacks or plugins. The whole project also runs offline with mocks: no API keys, no Docker, no Airflow.

---

## Architecture

```
            Airflow REST API v2
                    │
┌───────────────────▼──────────────────────────────────────────────┐
│ OncallAgent.run_cycle()  (agent.py)                               │
│  1. RetryManager.verify_pending()     retry_manager.py            │
│  2. ApprovalManager.process_pending() approval_manager.py         │
│  3. IncidentManager.flush/sync        incident_manager.py         │
│  4. Detector.detect()                 detection.py  (watermark)   │
│  5. for each failure:                                             │
│       fingerprint → dedup → ContextCollector → Analyzer           │
│       → DecisionPolicy → retry / approval / Linear issue          │
│  6. advance watermark   7. audit the cycle                        │
└───────┬─────────────────────┬───────────────────┬────────────────┘
        │ GitHub REST          │ Linear GraphQL    │ Groq API (LLM)
        ▼                      ▼                   ▼
   integrations/github.py  integrations/linear.py  integrations/groq.py
        (each one has an offline twin in integrations/mocks/)

SQLite (store.py): incidents · occurrences · retries · approvals ·
                   approval_signals · pending_linear_ops · watermark · audit
```

| Module | Responsibility |
|---|---|
| `config.py` | Reads env vars and the JSON config files, validated with Pydantic at startup |
| `models.py` | Shared data types and the analysis JSON schema |
| `store.py` / `audit.py` | SQLite persistence and the append-only audit log with secret redaction; JSON logging |
| `detection.py` | Watermark plus overlap-window detection of all three failure types |
| `fingerprint.py` | `sha256(dag_id \| task_id \| normalized_error_line)` |
| `context.py` | Airflow and GitHub context collection; failures become *context gaps* |
| `analysis.py` | LLM prompt (untrusted-data blocks + JSON schema), schema validation, rule-based fallback |
| `policy.py` | The safety core: deterministic decision with an exact list of reasons |
| `retry_manager.py` | Guarded, idempotent retries with a dry-run check; verification and escalation |
| `approval_manager.py` | Approval detection, authorisation and exactly-once execution |
| `incident_manager.py` | Linear issues through an SQLite outbox (survives Linear outages, never duplicates) |
| `ownership.py` | Commit author → DAG owner → default owner |
| `reporting.py` | Issue and comment Markdown |
| `factory.py` | Wires everything. The CLI, MCP servers and simulation all share it |
| `mcp_servers/` | `oncall-airflow-mcp`, `oncall-linear-mcp` |
| `simulate.py` | Six offline end-to-end scenarios |

### Key design decisions (in simple terms)

* **The LLM never acts.** It returns JSON. The JSON is validated, and the policy checks every rule itself. Invalid output means the rule-based fallback is used. Its confidence is capped at 0.5, below the 0.7 retry threshold, so it can never trigger a retry.
* **A retry happens only if all ten conditions hold.** The checks are: task failure, `retriable`, confidence ≥ 0.7, `retry_task`, fewer than `ONCALL_MAX_AUTO_RETRIES` retries for this task instance, fewer than `ONCALL_MAX_RETRIES_PER_HOUR` in the last hour, the DAG is not on the no-retry list, retries are not disabled, the failure is not already being handled, and the live task state is still `failed`. Every passed or failed check is stored as a reason.
* **Successful tasks are never cleared.** Retries use `only_failed=true, include_downstream=true`, and a **dry run** goes first. If the dry run would touch anything other than `failed` / `upstream_failed`, the retry is aborted.
* **Writes go to SQLite first, then to the outside world.** The retry or approval row is committed *before* the Airflow call. UNIQUE keys make a second execution impossible. After a crash, interrupted actions are reconciled from live Airflow state and **never re-sent blindly**. If SQLite is down, nothing is executed.
* **Watermark plus overlap.** Each cycle re-reads the last `ONCALL_DETECTION_OVERLAP_MINUTES` of failures. Already-seen occurrences are skipped by key, so late state changes are not missed and nothing is processed twice. The watermark is held when Airflow is unavailable.
* **Linear outbox.** Issue creates, comments and resolves are queued in SQLite and retried every cycle. Before creating an issue, the agent searches Linear for an open issue with the same fingerprint. This avoids duplicates even after a crash or a lost database.

The decisions on points the SRS left open are in [`requirements_document/PROJECT_REQUIREMENTS.md`](requirements_document/PROJECT_REQUIREMENTS.md#2-decisions-for-open-points).

---

## Installation

Requires Python ≥ 3.10.

```bash
cd oncall_agent
source /opt/turingiq-venv/bin/activate      # or: python -m venv .venv && source .venv/bin/activate
pip install -e ".[mcp,dev]"
oncall-agent check
```

## Configuration

```bash
cp .env.example .env
cp config/dag_repos.example.json config/dag_repos.json
cp config/owners.example.json   config/owners.json
```

**Choosing real vs mock clients.** Each integration is *real* when its credentials are set, and a mock otherwise:

| Integration | Real when |
|---|---|
| Airflow | `AIRFLOW_USERNAME` and `AIRFLOW_PASSWORD` |
| GitHub | `GITHUB_TOKEN` |
| Linear | `LINEAR_API_KEY` and `LINEAR_TEAM_ID` |
| LLM (Groq) | `GROQ_API_KEY`. Without it the rule-based analyzer is used |

`ONCALL_FORCE_MOCKS=true` forces offline mode. While *all* integrations are mocks and the config files are missing, the `*.example.json` files are used automatically.

**Environment variables** (see `.env.example`): `ONCALL_POLL_INTERVAL_SECONDS` (60), `ONCALL_MAX_AUTO_RETRIES` (2; `0` disables all automatic retries), `ONCALL_MAX_RETRIES_PER_HOUR` (10), `ONCALL_RETRY_TIMEOUT_MINUTES` (30), `ONCALL_MCP_ALLOW_AIRFLOW_WRITES` (false) and `ONCALL_DATABASE_PATH`.

These were added beyond the SRS:
* `ONCALL_DETECTION_OVERLAP_MINUTES` (10): the re-read window.
* `ONCALL_INITIAL_LOOKBACK_HOURS` (24): how far back the first cycle looks.
* `ONCALL_FORCE_MOCKS`.
* `ONCALL_DAG_REPOS_PATH` and `ONCALL_OWNERS_PATH`.
* `ONCALL_MONITOR_ALL_DAGS` (false): only DAGs listed in `config/dag_repos.json` are monitored. Keep it false on a shared Airflow, otherwise the agent would open issues for, and could retry, other people's DAGs.

**`config/dag_repos.json`** maps each DAG to its repository, DAG file, extra paths (SQL, shared modules), owner (a key in `owners.json`) and `no_retry` flag. It also holds a global `no_retry_dags` list.

**`config/owners.json`** maps developers to GitHub usernames and Linear user ids. It also sets `default_owner` (a Linear user id) and `approvers` (the Linear user ids allowed to approve risky actions). The agent never assigns an issue to anyone who is not listed here.

## Running

```bash
oncall-agent check            # integrations real/mock, SQLite, config, connectivity, Linear labels
oncall-agent once             # one polling cycle (prints a JSON report)
oncall-agent run              # continuous loop; Ctrl-C / SIGTERM shuts down gracefully
oncall-agent incidents [--status open]
oncall-agent audit <incident-id> [--full]
```

Only one agent process can run against a database; a lock file next to the DB enforces this.

## Simulation

```bash
oncall-agent simulate -v
```

This runs six scenarios through the real agent code with mocks and makes no network calls:

1. transient timeout → retry → verified → resolved, no issue;
2. missing DB column → non-retriable → issue assigned to the commit author;
3. DAG import error → issue → auto-resolved when the file is fixed;
4. DAG run timeout with no failed task → no retry, needs-human issue;
5. duplicate failure → one issue plus comments, also after a restart with an empty DB;
6. risky `mark_success` → approval required, an unauthorised `/approve` is ignored, the action executes exactly once.

## Testing

```bash
pytest                         # unit + acceptance + simulation tests (offline)
```

The integration tests need a real, throwaway Airflow 3.x that loads `tests/integration/dags/`:

```bash
python -m venv /tmp/af3 && /tmp/af3/bin/pip install "apache-airflow==3.1.0" \
  --constraint https://raw.githubusercontent.com/apache/airflow/constraints-3.1.0/constraints-3.10.txt
export PATH=/tmp/af3/bin:$PATH AIRFLOW_HOME=/tmp/af3home \
  AIRFLOW__CORE__DAGS_FOLDER=$PWD/tests/integration/dags AIRFLOW__CORE__LOAD_EXAMPLES=False \
  AIRFLOW__API__PORT=18080 AIRFLOW__CORE__EXECUTION_API_SERVER_URL=http://localhost:18080/execution/ \
  AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_USERS="oncall:op"
airflow standalone &                                   # password: $AIRFLOW_HOME/simple_auth_manager_passwords.json.generated
ONCALL_IT_AIRFLOW_URL=http://localhost:18080 ONCALL_IT_AIRFLOW_USERNAME=oncall \
ONCALL_IT_AIRFLOW_PASSWORD=<generated> pytest tests/integration -v
```

These tests cover failed-task, failed-DAG-run and import-error detection, plus a real retry (only the failed task and its `upstream_failed` child are cleared), retry verification and incident resolution.

## Real Airflow setup

* The agent only uses the REST API v2. Airflow 3 issues JWTs, so the agent exchanges `AIRFLOW_USERNAME`/`AIRFLOW_PASSWORD` at `POST /auth/token` and refreshes on a 401.
* Give the agent's user the **Op** role. It can read DAGs, runs, logs and import errors and clear/set task state, but it cannot manage users or connections. The integration tests run with an `op` user.
* The agent never deletes Airflow data.

## API setup

* **Groq (default LLM):** set `GROQ_API_KEY` (from console.groq.com). `GROQ_MODEL` defaults to `openai/gpt-oss-120b`, and `GROQ_REASONING_EFFORT` (`medium`) is sent only for reasoning models; set it empty for models such as `llama-3.3-70b-versatile`. The client uses Groq's JSON mode, and the schema is spelled out in the prompt. The reply is validated with Pydantic. An error, truncation or invalid JSON all lead to the rule-based analyzer.
* **Claude (optional):** set `ONCALL_LLM_PROVIDER=claude` and `ANTHROPIC_API_KEY`, and run `pip install -e ".[claude]"`. It uses structured outputs with `claude-opus-5-5`.
* **GitHub:** use a fine-grained token with read-only `Contents` + `Metadata` on the DAG repositories.
* **Linear:** set a personal API key in `LINEAR_API_KEY` and the team id in `LINEAR_TEAM_ID`. Create these labels in the team, because the agent does **not** create labels: `oncall:non-retriable`, `oncall:needs-human`, `oncall:approval-required`, `oncall:approved`. `oncall-agent check` reports any that are missing.
* **Secrets** live in `.env` (git-ignored) or AWS SSM when deployed. They are never printed, and they are redacted from audit and log output.

### MCP servers

```bash
oncall-airflow-mcp     # stdio
oncall-linear-mcp      # stdio
```

Both build the **same** clients, store and policy as the agent through `factory.py`.

* Airflow read tools are always available.
* Airflow write tools are disabled unless `ONCALL_MCP_ALLOW_AIRFLOW_WRITES=true`. Even then:
  * `clear_failed_task` goes through the full policy and the retry limits;
  * `rerun_dag` and `mark_task_success` require an approval id that an authorised human approved in Linear, and they run at most once.
* The Linear server refuses unknown assignees and labels that don't exist, and it cannot add `oncall:approved`. Approvals only come from humans in Linear.

Example Claude Code MCP config:

```json
{"mcpServers": {"oncall-airflow": {"command": "oncall-airflow-mcp"},
                "oncall-linear":  {"command": "oncall-linear-mcp"}}}
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Configuration error: ... not found` | Copy the example config files, or set `ONCALL_DAG_REPOS_PATH` / `ONCALL_OWNERS_PATH` |
| `another oncall-agent process holds ...lock` | Another agent is running against the same database |
| Every analysis says `source: rule_based` | `GROQ_API_KEY` is missing, or Groq is failing (rate limit, bad model name). See the `llm_analysis:` context gap in the issue |
| Watermark not advancing | Airflow is unreachable or a failure couldn't be processed. Check `oncall-agent once` and the audit `failure` events |
| Issues have no labels | The labels are missing in the Linear team (see `oncall-agent check`) |
| Approvals are ignored | The approver is not in `owners.json → approvers`, the comment is not exactly `/approve`, or the label is not exactly `oncall:approved`. See `approval_signal_ignored` audit events |
| Integration-test runs stay `queued` | Set `AIRFLOW__CORE__EXECUTION_API_SERVER_URL` to the API server's port |
