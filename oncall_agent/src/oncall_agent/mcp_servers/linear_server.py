"""oncall-linear-mcp: exposes the agent's Linear client and incident store over MCP.

Guardrails: issues can only be assigned to users listed in owners.json, labels
are never auto-created, and there is deliberately **no** tool to grant an
approval - approvals come only from humans in Linear.
"""
from __future__ import annotations

from typing import Any, Optional

from ..factory import Services
from ..mcp_servers.airflow_server import _plain


class LinearTools:
    def __init__(self, services: Services):
        self.s = services

    def search_incidents(self, query: str = "", status: Optional[str] = None, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.s.store.list_incidents(status=status, limit=min(limit, 200))
        q = query.lower()
        keep = ("id", "status", "decision", "dag_id", "task_id", "run_id", "fingerprint", "classification",
                "confidence", "root_cause", "linear_issue_identifier", "occurrence_count", "created_at")
        return [{k: r.get(k) for k in keep} for r in rows
                if not q or q in " ".join(str(r.get(k) or "") for k in keep).lower()]

    def get_issue(self, issue_id: str) -> dict[str, Any]:
        issue = self.s.linear.get_issue(issue_id)
        return _plain(issue) | {"comments": _plain(self.s.linear.list_comments(issue_id))}

    def create_issue(self, title: str, description: str, label: Optional[str] = None,
                     assignee_id: Optional[str] = None) -> dict[str, Any]:
        if assignee_id and assignee_id not in self.s.owners.known_linear_ids():
            return {"ok": False, "error": "assignee is not listed in owners.json"}
        label_ids: list[str] = []
        if label:
            found = self.s.linear.get_label_ids([label])
            if label not in found:
                return {"ok": False, "error": f"label {label!r} does not exist in Linear"}
            label_ids = [found[label]]
        issue = self.s.linear.create_issue(title, description, label_ids=label_ids, assignee_id=assignee_id)
        self.s.audit.record("issue_create", "mcp_create_issue", input={"title": title, "label": label},
                            output=issue.identifier)
        return {"ok": True, "issue": _plain(issue)}

    def add_comment(self, issue_id: str, body: str) -> dict[str, Any]:
        c = self.s.linear.add_comment(issue_id, body)
        self.s.audit.record("issue_update", "mcp_add_comment", input=issue_id, output=c.id)
        return {"ok": True, "comment_id": c.id}

    def add_label(self, issue_id: str, label: str) -> dict[str, Any]:
        if label == "oncall:approved":
            return {"ok": False, "error": "the approval label can only be added by a human in Linear"}
        found = self.s.linear.get_label_ids([label])
        if label not in found:
            return {"ok": False, "error": f"label {label!r} does not exist in Linear"}
        self.s.linear.add_label(issue_id, found[label])
        self.s.audit.record("issue_update", "mcp_add_label", input={"issue": issue_id, "label": label})
        return {"ok": True}

    def assign_issue(self, issue_id: str, linear_user_id: str) -> dict[str, Any]:
        if linear_user_id not in self.s.owners.known_linear_ids():
            return {"ok": False, "error": "user is not listed in owners.json"}
        self.s.linear.assign_issue(issue_id, linear_user_id)
        self.s.audit.record("issue_update", "mcp_assign_issue", input={"issue": issue_id, "user": linear_user_id})
        return {"ok": True}

    def resolve_issue(self, issue_id: str) -> dict[str, Any]:
        inc = self.s.store.find_incident_by_issue(issue_id)
        if inc is not None:
            self.s.incidents.resolve(inc["id"], "Resolved via oncall-linear-mcp.")
        else:
            self.s.linear.resolve_issue(issue_id)
            self.s.audit.record("resolution", "mcp_resolve_issue", input=issue_id)
        return {"ok": True}

    def read_approvals(self, incident_id: Optional[str] = None) -> list[dict[str, Any]]:
        rows = self.s.store.approvals_for_incident(incident_id) if incident_id else self.s.store.all_approvals()
        return [r | {"signals": self.s.store.signals_for_approval(r["id"])} for r in rows]


def create_server(services: Services):  # pragma: no cover - thin MCP wiring
    from mcp.server.mcpserver import MCPServer

    tools = LinearTools(services)
    server = MCPServer(name="oncall-linear-mcp",
                       instructions="Linear incident access for the oncall agent. Approvals cannot be granted here.")
    descriptions = {
        "search_incidents": "Search oncall incidents (local store) by text and/or status.",
        "get_issue": "Read a Linear issue with its comments.",
        "create_issue": "Create a Linear issue. Assignee must be in owners.json; label must already exist.",
        "add_comment": "Add a comment to a Linear issue.",
        "add_label": "Add an existing label to an issue (the approval label cannot be added here).",
        "assign_issue": "Assign an issue to a developer listed in owners.json.",
        "resolve_issue": "Resolve an issue (and its oncall incident, if any).",
        "read_approvals": "Read approval requests, their status and every approval signal evaluated.",
    }
    for fn in (tools.search_incidents, tools.get_issue, tools.create_issue, tools.add_comment, tools.add_label,
               tools.assign_issue, tools.resolve_issue, tools.read_approvals):
        server.add_tool(fn, name=fn.__name__, description=descriptions[fn.__name__])
    return server


def main() -> None:  # pragma: no cover
    from ..audit import setup_json_logging
    from ..config import load_settings
    from ..factory import build_services

    setup_json_logging()
    create_server(build_services(load_settings())).run("stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
