"""Markdown rendering for Linear issues and comments."""
from __future__ import annotations

from typing import Any, Optional

from .models import AnalysisResult, FailureContext, OwnerAssignment, PolicyResult, iso, utcnow

FINGERPRINT_MARKER = "oncall-fingerprint:"
APPROVE_COMMAND = "/approve"
APPROVED_LABEL = "oncall:approved"


def fingerprint_marker(fp: str) -> str:
    return f"{FINGERPRINT_MARKER} {fp}"


def _fence(text: str, limit: int = 6000) -> str:
    text = (text or "(not available)")[-limit:].replace("```", "'''")
    return f"```\n{text}\n```"


def _bullets(items: list[str], empty: str = "None") -> str:
    return "\n".join(f"- {i}" for i in items) if items else f"- {empty}"


def issue_title(incident: dict[str, Any]) -> str:
    target = incident["dag_id"] + (f".{incident['task_id']}" if incident.get("task_id") else "")
    kind = {"task_instance": "Task failed", "dag_run": "DAG run failed", "import_error": "DAG import error"}
    line = (incident.get("normalized_error") or incident.get("error") or "")[:80]
    return f"[oncall] {kind.get(incident['kind'], 'Failure')}: {target} - {line}"[:250]


def issue_description(
    incident: dict[str, Any],
    ctx: FailureContext,
    result: AnalysisResult,
    policy: PolicyResult,
    assignment: OwnerAssignment,
    actions: list[str],
    *,
    previous_issue: Optional[str] = None,
    missing_labels: Optional[list[str]] = None,
    approval_action: Optional[str] = None,
) -> str:
    a = result.analysis
    f = ctx.failure
    suspected = ctx.commit_by_sha(a.suspected_commit)
    commits = [
        f"`{c.sha[:10]}` {c.message.splitlines()[0][:100] if c.message else ''} - "
        f"{c.author_login or c.author_name or 'unknown'} ({iso(c.timestamp) or 'unknown time'})"
        + (" **<- suspected**" if suspected and c.sha == suspected.sha else "")
        for c in ctx.commits
    ]
    sections = [
        f"**Decision:** `{policy.decision.value}`",
        "### Failure",
        _bullets([
            f"Fingerprint: `{incident['fingerprint']}`",
            f"DAG ID: `{f.dag_id}`",
            f"Task ID: `{f.task_id or '-'}`",
            f"DAG run ID: `{f.run_id or '-'}`",
            f"Failure type: {f.kind.value}" + (f" (file `{f.filename}`)" if f.filename else ""),
            f"Try number: {f.try_number}" if f.task_id else "Try number: -",
            f"Detected at: {iso(utcnow())}",
        ]),
        "### Error message",
        _fence(ctx.error_message, 3000),
        "### Analysis",
        _bullets([
            f"Classification: **{a.classification.value}** (confidence {a.confidence:.2f}, source: {result.source})",
            f"Root cause: {a.root_cause}",
            f"Suggested fix: {a.suggested_fix}",
            f"Proposed action: `{a.proposed_action.value}`",
        ]),
        "**Evidence**",
        _bullets(a.evidence),
        "### Decision reasons",
        _bullets(policy.reasons),
        "### Actions attempted",
        _bullets(actions, "No automatic action was taken"),
        "### Responsible developer",
        _bullets([
            f"Selected: `{assignment.developer or assignment.linear_user_id}` (Linear user `{assignment.linear_user_id}`)",
            f"Selected by: **{assignment.source.replace('_', ' ')}**",
            f"Why: {assignment.reason}",
        ]),
        "### Relevant commits",
        _bullets(commits, "No recent commits found (or GitHub unavailable)"),
        "### Log tail",
        _fence(ctx.log_tail, 5000),
        "### Context gaps",
        _bullets(ctx.gaps, "None - all context sources were collected"),
    ]
    if approval_action:
        sections += [
            "### Approval required",
            f"The agent proposes **`{approval_action}`**. This is a risky action, so it will not run automatically.",
            f"To approve, an authorised approver can either add the label `{APPROVED_LABEL}` or comment exactly "
            f"`{APPROVE_COMMAND}` on this issue. The action runs once; further approvals are ignored.",
        ]
    if previous_issue:
        sections.append(f"_This failure was seen before in {previous_issue} (resolved). This is a new occurrence._")
    if missing_labels:
        sections.append(f"_Warning: Linear label(s) {', '.join(missing_labels)} do not exist; they were not applied._")
    sections.append(f"\n<!-- {fingerprint_marker(incident['fingerprint'])} -->\n{fingerprint_marker(incident['fingerprint'])}")
    return "\n\n".join(sections)


def repeat_comment(failure_summary: dict[str, Any], error: str, count: int) -> str:
    return "\n\n".join([
        f"**Repeat occurrence #{count}** of this failure (same fingerprint).",
        _bullets([f"{k}: `{v}`" for k, v in failure_summary.items() if v not in (None, "", -1)]),
        _fence(error, 1500),
    ])
