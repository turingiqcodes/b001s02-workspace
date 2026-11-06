"""Linear incident management: create / comment / label / assign / resolve,
with an SQLite-backed outbox so Linear outages never lose or duplicate work.

Every Linear write is first stored as a *pending operation*, then attempted.
If Linear is down the operation stays queued and is retried on the next
polling cycle. Issue creation is idempotent:

1. the incident row already has ``linear_issue_id`` -> nothing to do;
2. otherwise Linear is searched for an *open* issue containing this
   fingerprint (covers "created, then crashed before saving the id");
3. only then is a new issue created.
"""
from __future__ import annotations

from typing import Any, Optional

from .audit import AuditLogger
from .config import OwnersConfig
from .integrations import IntegrationError, LinearClient, LinearUnavailable
from .models import Decision, IncidentStatus, LinearIssue, iso, utcnow
from .reporting import fingerprint_marker
from .store import Store

LABEL_FOR_DECISION = {
    Decision.NON_RETRIABLE.value: "oncall:non-retriable",
    Decision.NEEDS_HUMAN.value: "oncall:needs-human",
    Decision.APPROVAL_REQUIRED.value: "oncall:approval-required",
}
PRIORITY_HIGH = 2  # Linear: 0 none, 1 urgent, 2 high, 3 medium, 4 low


class IncidentManager:
    def __init__(self, linear: LinearClient, store: Store, audit: AuditLogger, owners: OwnersConfig):
        self.linear = linear
        self.store = store
        self.audit = audit
        self.owners = owners

    # ---- public API -------------------------------------------------------------------
    def request_issue(self, incident_id: str, title: str, description: str, decision: str,
                      assignee_id: Optional[str]) -> Optional[str]:
        """Queue + attempt creation of the Linear issue. Returns the issue id if created now."""
        label = LABEL_FOR_DECISION.get(decision)
        payload = {
            "title": title,
            "description": description,
            "labels": [label] if label else [],
            "assignee_id": assignee_id,
            "priority": PRIORITY_HIGH if decision == Decision.NEEDS_HUMAN.value else 0,
        }
        self.store.enqueue_linear_op(incident_id, "create_issue", payload)
        self.flush_pending(incident_id=incident_id)
        inc = self.store.get_incident(incident_id)
        return inc.get("linear_issue_id") if inc else None

    def comment(self, incident_id: str, body: str, dedupe_key: str) -> None:
        self.store.enqueue_linear_op(incident_id, "comment", {"body": body}, dedupe_key)
        self.flush_pending(incident_id=incident_id)

    def escalate(self, incident_id: str, reasons: list[str]) -> None:
        """Hand an incident to a human (retry failed, timed out, interrupted ...)."""
        inc = self.store.get_incident(incident_id)
        if inc is None:
            return
        reasons_md = "\n".join(f"- {r}" for r in reasons)
        self.store.update_incident(incident_id, status=IncidentStatus.OPEN.value,
                                   decision=Decision.NEEDS_HUMAN.value)
        self.store.append_incident_action(incident_id, "escalated: " + "; ".join(reasons))
        self.audit.record("decision", "escalate", incident_id=incident_id, input=reasons, output="needs_human")
        if inc.get("linear_issue_id") or self.store.has_pending_op(incident_id, "create_issue"):
            self.store.enqueue_linear_op(incident_id, "add_label", {"label": "oncall:needs-human"}, "needs-human")
            self.comment(incident_id, f"**Escalated to a human.**\n\n{reasons_md}", f"escalate:{iso(utcnow())}")
            return
        draft = inc.get("issue_draft") or {}
        description = (draft.get("description") or f"Incident {incident_id}") + \
            f"\n\n### Escalation\nThe automatic retry did not resolve this failure.\n\n{reasons_md}"
        self.request_issue(incident_id, draft.get("title") or f"[oncall] {inc['dag_id']}", description,
                           Decision.NEEDS_HUMAN.value, draft.get("assignee_id"))

    def resolve(self, incident_id: str, reason: str) -> None:
        inc = self.store.get_incident(incident_id)
        if inc is None or inc["status"] == IncidentStatus.RESOLVED.value:
            return
        self.store.update_incident(incident_id, status=IncidentStatus.RESOLVED.value, resolved_at=iso(utcnow()))
        self.store.append_incident_action(incident_id, f"resolved: {reason}")
        self.audit.record("resolution", "resolve_incident", incident_id=incident_id, input=reason, output="resolved")
        if inc.get("linear_issue_id") or self.store.has_pending_op(incident_id, "create_issue"):
            self.store.enqueue_linear_op(incident_id, "comment", {"body": f"**Resolved by the oncall agent.** {reason}"},
                                         "resolve-comment")
            self.store.enqueue_linear_op(incident_id, "resolve", {}, "resolve")
            self.flush_pending(incident_id=incident_id)

    def find_open_issue_by_fingerprint(self, fingerprint: str) -> Optional[LinearIssue]:
        marker = fingerprint_marker(fingerprint)
        for issue in self.linear.search_issues(fingerprint):
            if issue.is_open and marker in issue.description:
                return issue
        return None

    def sync_open_incidents(self) -> None:
        """Pick up issues a human closed in Linear and mark those incidents resolved."""
        for inc in self.store.open_incidents():
            issue_id = inc.get("linear_issue_id")
            if not issue_id or inc["status"] in (IncidentStatus.RETRYING.value, IncidentStatus.VERIFYING.value):
                continue
            try:
                issue = self.linear.get_issue(issue_id)
            except LinearUnavailable as exc:
                self.audit.record("external_api", "linear_get_issue", incident_id=inc["id"], result="error", error=exc)
                return
            except IntegrationError as exc:
                self.audit.record("external_api", "linear_get_issue", incident_id=inc["id"], result="error", error=exc)
                continue
            if not issue.is_open:
                self.store.update_incident(inc["id"], status=IncidentStatus.RESOLVED.value, resolved_at=iso(utcnow()))
                self.store.append_incident_action(inc["id"], f"issue {issue.identifier} closed in Linear")
                self.audit.record("resolution", "issue_closed_in_linear", incident_id=inc["id"],
                                  input=issue.identifier, output=issue.state_type)
                for appr in self.store.approvals_for_incident(inc["id"]):
                    if appr["status"] == "pending":
                        self.store.finish_approval(appr["id"], "cancelled", {"reason": "issue closed"})

    # ---- outbox -----------------------------------------------------------------------
    def flush_pending(self, incident_id: Optional[str] = None) -> int:
        """Attempt queued Linear operations (oldest first). Returns how many succeeded."""
        done = 0
        for op in self.store.pending_linear_ops():
            if incident_id and op["incident_id"] != incident_id:
                continue
            try:
                if self._apply(op):
                    self.store.complete_linear_op(op["id"])
                    done += 1
            except LinearUnavailable as exc:
                self.store.fail_linear_op(op["id"], str(exc))
                self.audit.record("failure", f"linear_{op['op_type']}", incident_id=op["incident_id"],
                                  result="error", error=f"Linear unavailable, will retry next cycle: {exc}")
                return done  # Linear is down; stop for this cycle
            except IntegrationError as exc:
                self.store.fail_linear_op(op["id"], str(exc))
                self.audit.record("failure", f"linear_{op['op_type']}", incident_id=op["incident_id"],
                                  result="error", error=exc)
        return done

    def _apply(self, op: dict[str, Any]) -> bool:
        """Run one queued op. Returns False when it must wait (e.g. issue not created yet)."""
        inc = self.store.get_incident(op["incident_id"])
        if inc is None:
            return True  # nothing to do; drop
        payload = op["payload"]
        kind = op["op_type"]
        if kind == "create_issue":
            return self._create_issue(inc, payload)
        issue_id = inc.get("linear_issue_id")
        if not issue_id:
            return False  # wait for create_issue
        if kind == "comment":
            self.linear.add_comment(issue_id, payload["body"])
            self.audit.record("issue_update", "linear_comment", incident_id=inc["id"],
                              input={"issue": issue_id, "dedupe": op["dedupe_key"]}, output="commented")
        elif kind == "add_label":
            ids = self.linear.get_label_ids([payload["label"]])
            if payload["label"] in ids:
                self.linear.add_label(issue_id, ids[payload["label"]])
                self.audit.record("issue_update", "linear_add_label", incident_id=inc["id"],
                                  input=payload["label"], output="labelled")
            else:
                self.audit.record("issue_update", "linear_add_label", incident_id=inc["id"], input=payload["label"],
                                  result="skipped", error="label does not exist in Linear; not created automatically")
        elif kind == "resolve":
            self.linear.resolve_issue(issue_id)
            self.audit.record("resolution", "linear_resolve_issue", incident_id=inc["id"], input=issue_id,
                              output="completed")
        return True

    def _create_issue(self, inc: dict[str, Any], payload: dict[str, Any]) -> bool:
        if inc.get("linear_issue_id"):
            return True
        existing = self.find_open_issue_by_fingerprint(inc["fingerprint"])
        if existing is not None:
            self.store.update_incident(inc["id"], linear_issue_id=existing.id,
                                       linear_issue_identifier=existing.identifier)
            self.audit.record("issue_create", "linear_adopt_existing_issue", incident_id=inc["id"],
                              input=inc["fingerprint"], output=existing.identifier)
            return True

        description = payload["description"]
        wanted = payload.get("labels") or []
        label_ids = self.linear.get_label_ids(wanted) if wanted else {}
        missing = [name for name in wanted if name not in label_ids]
        if missing:
            description += (f"\n\n_Warning: Linear label(s) {', '.join(missing)} do not exist in this team; "
                            f"they were not applied. Create them in Linear._")
            self.audit.record("issue_create", "linear_missing_labels", incident_id=inc["id"], input=missing,
                              result="skipped", error="labels missing; not created automatically")

        assignee = payload.get("assignee_id")
        if assignee and assignee not in self.owners.known_linear_ids():
            self.audit.record("issue_create", "linear_unknown_assignee", incident_id=inc["id"], input=assignee,
                              result="skipped", error="assignee is not in owners.json; left unassigned")
            assignee = None

        issue = self.linear.create_issue(payload["title"], description, label_ids=list(label_ids.values()),
                                         assignee_id=assignee, priority=int(payload.get("priority") or 0))
        self.store.update_incident(inc["id"], linear_issue_id=issue.id, linear_issue_identifier=issue.identifier)
        self.store.append_incident_action(inc["id"], f"created Linear issue {issue.identifier}")
        self.audit.record("issue_create", "linear_create_issue", incident_id=inc["id"],
                          input={"title": payload["title"], "labels": list(label_ids), "assignee": assignee,
                                 "priority": payload.get("priority")},
                          output={"issue": issue.identifier, "id": issue.id})
        for appr in self.store.approvals_for_incident(inc["id"]):
            if not appr.get("linear_issue_id"):
                self.store.set_approval_issue(appr["id"], issue.id)
        return True
