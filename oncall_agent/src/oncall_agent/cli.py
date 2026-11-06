"""Command line interface: check | run | once | incidents | audit <id> | simulate [-v]"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from typing import Optional

from .audit import setup_json_logging
from .config import ConfigError, load_settings


def cmd_check(_: argparse.Namespace) -> int:
    from .factory import build_clients, load_configs
    from .store import Store, StoreError

    settings = load_settings()
    ok = True
    print("Integrations:")
    print(f"  Airflow: {'real (' + settings.airflow_base_url + ')' if settings.airflow_is_real else 'mock'}")
    print(f"  GitHub:  {'real' if settings.github_is_real else 'mock'}")
    print(f"  Linear:  {'real (team ' + settings.linear_team_id + ')' if settings.linear_is_real else 'mock'}")
    key_name = "ANTHROPIC_API_KEY" if settings.llm_provider == "claude" else "GROQ_API_KEY"
    llm_state = (f"real ({settings.llm_model})" if settings.llm_is_real
                 else f"mock (no {key_name}: rule-based analyzer)")
    print(f"  LLM:     {settings.llm_provider} - {llm_state}")
    try:
        store = Store(settings.database_path)
        print(f"  SQLite:  {'available' if store.ping() else 'unavailable'} ({settings.database_path})")
        store.close()
    except StoreError as exc:
        ok = False
        print(f"  SQLite:  unavailable ({exc})")

    print("Configuration:")
    try:
        dag_repos, owners, notes = load_configs(settings)
        print(f"  dag_repos: {len(dag_repos.dags)} DAG(s), no-retry: {sorted(dag_repos.no_retry_dags)}")
        print(f"  owners:    {len(owners.developers)} developer(s), {len(owners.approvers)} approver(s)")
        for n in notes:
            print(f"  note: {n}")
    except ConfigError as exc:
        ok = False
        print(f"  INVALID: {exc}")
    print(f"  auto-retries: max {settings.max_auto_retries}/task, {settings.max_retries_per_hour}/hour"
          f"{' (DISABLED)' if settings.max_auto_retries == 0 else ''}")

    real = [n for n, flag in (("airflow", settings.airflow_is_real), ("github", settings.github_is_real),
                              ("linear", settings.linear_is_real)) if flag]
    if real:
        print("Connectivity (real services only):")
        airflow, github, linear, _ = build_clients(settings)
        for name, client in (("airflow", airflow), ("github", github), ("linear", linear)):
            if name in real:
                healthy = client.health()
                ok = ok and healthy
                print(f"  {name}: {'reachable' if healthy else 'UNREACHABLE'}")
        if settings.linear_is_real:
            try:
                print(f"  linear team: {linear.team_name()}")
            except Exception as exc:  # noqa: BLE001
                ok = False
                print(f"  linear team: NOT FOUND - {exc}. Check LINEAR_TEAM_ID (it must belong to the API key's workspace)")
            try:
                _, owners_cfg, _ = load_configs(settings)
                missing_users = linear.missing_users(sorted(owners_cfg.known_linear_ids() | set(owners_cfg.approvers)))
                if missing_users:
                    ok = False
                    print(f"  linear users: UNKNOWN in this workspace: {', '.join(missing_users)} (fix config/owners.json)")
                else:
                    print("  linear users: all owners/approvers exist")
            except ConfigError:
                pass  # already reported above
            from .incident_manager import LABEL_FOR_DECISION
            from .reporting import APPROVED_LABEL

            wanted = [*LABEL_FOR_DECISION.values(), APPROVED_LABEL]
            try:
                found = linear.get_label_ids(wanted)
                missing = [w for w in wanted if w not in found]
                print(f"  linear labels: {'all present' if not missing else 'MISSING ' + ', '.join(missing)}")
            except Exception as exc:  # noqa: BLE001
                print(f"  linear labels: could not check ({exc})")
    return 0 if ok else 1


def _services():
    from .factory import build_services

    settings = load_settings()
    return build_services(settings)


def cmd_once(_: argparse.Namespace) -> int:
    from .agent import CycleLock

    setup_json_logging()
    services = _services()
    with CycleLock(services.settings.database_path):
        report = services.agent.run_cycle()
    print(json.dumps(report.__dict__, indent=2, default=str))
    return 0 if report.ok else 1


def cmd_run(_: argparse.Namespace) -> int:
    from .agent import CycleLock

    setup_json_logging()
    services = _services()
    with CycleLock(services.settings.database_path):
        asyncio.run(services.agent.run_forever())
    return 0


def cmd_incidents(args: argparse.Namespace) -> int:
    from .store import Store

    store = Store(load_settings().database_path)
    rows = store.list_incidents(status=args.status)
    if not rows:
        print("No incidents.")
        return 0
    print(f"{'ID':<17} {'STATUS':<18} {'DECISION':<18} {'DAG.TASK':<40} {'ISSUE':<10} {'SEEN':>4} CREATED")
    for i in rows:
        target = i["dag_id"] + (f".{i['task_id']}" if i.get("task_id") else "")
        print(f"{i['id']:<17} {i['status']:<18} {(i.get('decision') or '-'):<18} {target[:40]:<40} "
              f"{(i.get('linear_issue_identifier') or '-'):<10} {i.get('occurrence_count') or 1:>4} {i['created_at']}")
    return 0


def cmd_audit(args: argparse.Namespace) -> int:
    from .store import Store

    store = Store(load_settings().database_path)
    inc = store.get_incident(args.id)
    if inc is None:
        print(f"Incident {args.id} not found.")
        return 1
    print(f"Incident {inc['id']}  status={inc['status']}  decision={inc.get('decision')}")
    print(f"  {inc['dag_id']}.{inc.get('task_id') or '-'}  run={inc.get('run_id') or '-'}")
    print(f"  fingerprint={inc['fingerprint']}")
    print(f"  root cause: {inc.get('root_cause')}")
    print(f"  issue: {inc.get('linear_issue_identifier') or '-'}  owner: {inc.get('assigned_developer') or '-'}")
    for r in store.retries_for_incident(args.id):
        print(f"  retry #{r['attempt']}: {r['status']} ({r['verification_status']}) at {r['executed_at']}")
    for a in store.approvals_for_incident(args.id):
        print(f"  approval {a['action']}: {a['status']} by {a.get('approved_by') or '-'} via {a.get('approval_source') or '-'}")
    print("\nAudit trail:")
    for row in store.audit_for_incident(args.id):
        line = f"  {row['ts']}  {row['event_type']:<13} {row['action']:<36} {row['result']}"
        print(line)
        if args.full:
            for key in ("input_summary", "output_summary", "error"):
                if row.get(key):
                    print(f"      {key.split('_')[0]}: {row[key]}")
    return 0


def cmd_simulate(args: argparse.Namespace) -> int:
    from .simulate import run_all

    results = run_all(verbose=args.verbose)
    return 0 if all(r.passed for r in results) else 1


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="oncall-agent", description="Airflow Oncall Agent")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="check configuration and service availability").set_defaults(func=cmd_check)
    sub.add_parser("run", help="start the continuous polling agent").set_defaults(func=cmd_run)
    sub.add_parser("once", help="run a single polling cycle").set_defaults(func=cmd_once)
    p = sub.add_parser("incidents", help="list incidents")
    p.add_argument("--status", default=None)
    p.set_defaults(func=cmd_incidents)
    p = sub.add_parser("audit", help="show the audit trail for an incident")
    p.add_argument("id")
    p.add_argument("--full", action="store_true", help="include input/output summaries")
    p.set_defaults(func=cmd_audit)
    p = sub.add_parser("simulate", help="run the six offline scenarios")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_simulate)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
