"""Real Linear GraphQL API client."""
from __future__ import annotations

from typing import Any, Optional

import httpx

from ..models import LabelEvent, LinearComment, LinearIssue, parse_dt, utcnow
from . import IntegrationError, LinearClient, LinearUnavailable

ISSUE_FIELDS = """
  id identifier title description url priority
  state { type }
  labels { nodes { name } }
  assignee { id }
"""


def _issue(d: dict[str, Any]) -> LinearIssue:
    return LinearIssue(
        id=d["id"],
        identifier=d.get("identifier", d["id"]),
        title=d.get("title", ""),
        description=d.get("description") or "",
        state_type=(d.get("state") or {}).get("type", "unstarted"),
        labels=[n["name"] for n in (d.get("labels") or {}).get("nodes", [])],
        assignee_id=(d.get("assignee") or {}).get("id"),
        priority=int(d.get("priority") or 0),
        url=d.get("url"),
    )


class GraphQLLinearClient(LinearClient):
    mode = "real"

    def __init__(self, api_key: str, team_id: str, url: str = "https://api.linear.app/graphql",
                 timeout: float = 20.0, transport: Optional[httpx.BaseTransport] = None):
        self.team_id = team_id
        self._http = httpx.Client(timeout=timeout, transport=transport,
                                  headers={"Authorization": api_key, "Content-Type": "application/json"})
        self._url = url
        self._completed_state_id: Optional[str] = None

    def _gql(self, query: str, variables: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        try:
            resp = self._http.post(self._url, json={"query": query, "variables": variables or {}})
        except httpx.HTTPError as exc:
            raise LinearUnavailable(f"Linear request failed: {exc}") from exc
        if resp.status_code >= 500 or resp.status_code == 429:
            raise LinearUnavailable(f"Linear returned HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise IntegrationError(f"Linear returned HTTP {resp.status_code}: {resp.text[:300]}")
        payload = resp.json()
        if payload.get("errors"):
            raise IntegrationError(f"Linear GraphQL error: {payload['errors'][0].get('message')}")
        return payload.get("data") or {}

    def health(self) -> bool:
        try:
            self._gql("query { viewer { id } }")
            return True
        except IntegrationError:
            return False

    def create_issue(self, title: str, description: str, *, label_ids: list[str],
                     assignee_id: Optional[str], priority: int = 0) -> LinearIssue:
        inp: dict[str, Any] = {"teamId": self.team_id, "title": title, "description": description,
                               "labelIds": label_ids, "priority": priority}
        if assignee_id:
            inp["assigneeId"] = assignee_id
        data = self._gql(
            f"mutation($input: IssueCreateInput!) {{ issueCreate(input: $input) {{ success issue {{ {ISSUE_FIELDS} }} }} }}",
            {"input": inp})
        result = data.get("issueCreate") or {}
        if not result.get("success"):
            raise IntegrationError("Linear issueCreate was not successful")
        return _issue(result["issue"])

    def get_issue(self, issue_id: str) -> LinearIssue:
        data = self._gql(f"query($id: String!) {{ issue(id: $id) {{ {ISSUE_FIELDS} }} }}", {"id": issue_id})
        if not data.get("issue"):
            raise IntegrationError(f"Linear issue {issue_id} not found")
        return _issue(data["issue"])

    def search_issues(self, text: str) -> list[LinearIssue]:
        data = self._gql(
            f"query($term: String!) {{ searchIssues(term: $term, first: 20) {{ nodes {{ {ISSUE_FIELDS} }} }} }}",
            {"term": text})
        return [_issue(n) for n in (data.get("searchIssues") or {}).get("nodes", [])]

    def add_comment(self, issue_id: str, body: str) -> LinearComment:
        data = self._gql(
            "mutation($input: CommentCreateInput!) { commentCreate(input: $input) "
            "{ success comment { id body createdAt user { id } } } }",
            {"input": {"issueId": issue_id, "body": body}})
        c = (data.get("commentCreate") or {}).get("comment")
        if not c:
            raise IntegrationError("Linear commentCreate failed")
        return LinearComment(id=c["id"], body=c["body"], author_id=(c.get("user") or {}).get("id"),
                             created_at=parse_dt(c.get("createdAt")) or utcnow())

    def list_comments(self, issue_id: str) -> list[LinearComment]:
        data = self._gql(
            "query($id: String!) { issue(id: $id) { comments(first: 100) "
            "{ nodes { id body createdAt user { id } } } } }", {"id": issue_id})
        nodes = ((data.get("issue") or {}).get("comments") or {}).get("nodes", [])
        return [LinearComment(id=n["id"], body=n.get("body") or "", author_id=(n.get("user") or {}).get("id"),
                              created_at=parse_dt(n.get("createdAt")) or utcnow()) for n in nodes]

    def add_label(self, issue_id: str, label_id: str) -> None:
        data = self._gql("mutation($id: String!, $labelId: String!) { issueAddLabel(id: $id, labelId: $labelId) "
                         "{ success } }", {"id": issue_id, "labelId": label_id})
        if not (data.get("issueAddLabel") or {}).get("success"):
            raise IntegrationError("Linear issueAddLabel failed")

    def get_label_ids(self, names: list[str]) -> dict[str, str]:
        data = self._gql(
            "query($names: [String!]) { issueLabels(first: 250, filter: { name: { in: $names } }) "
            "{ nodes { id name team { id } } } }", {"names": names})
        out: dict[str, str] = {}
        for n in (data.get("issueLabels") or {}).get("nodes", []):
            team = (n.get("team") or {}).get("id")
            if team in (None, self.team_id):  # workspace label or this team's label
                out.setdefault(n["name"], n["id"])
        return out

    def assign_issue(self, issue_id: str, user_id: str) -> None:
        self._gql("mutation($id: String!, $input: IssueUpdateInput!) { issueUpdate(id: $id, input: $input) "
                  "{ success } }", {"id": issue_id, "input": {"assigneeId": user_id}})

    def resolve_issue(self, issue_id: str) -> None:
        if self._completed_state_id is None:
            data = self._gql(
                "query($team: ID!) { workflowStates(filter: { team: { id: { eq: $team } }, type: { eq: \"completed\" } }) "
                "{ nodes { id } } }", {"team": self.team_id})
            nodes = (data.get("workflowStates") or {}).get("nodes", [])
            if not nodes:
                raise IntegrationError("no 'completed' workflow state found for the Linear team")
            self._completed_state_id = nodes[0]["id"]
        self._gql("mutation($id: String!, $input: IssueUpdateInput!) { issueUpdate(id: $id, input: $input) "
                  "{ success } }", {"id": issue_id, "input": {"stateId": self._completed_state_id}})

    def team_name(self) -> str:
        data = self._gql("query($id: String!) { team(id: $id) { key name } }", {"id": self.team_id})
        team = data.get("team")
        if not team:
            raise IntegrationError(f"team {self.team_id} not found for this API key")
        return f"{team['key']} ({team['name']})"

    def missing_users(self, user_ids: list[str]) -> list[str]:
        missing = []
        for uid in dict.fromkeys(user_ids):
            try:
                if not self._gql("query($id: String!) { user(id: $id) { id } }", {"id": uid}).get("user"):
                    missing.append(uid)
            except IntegrationError:
                missing.append(uid)
        return missing

    def label_events(self, issue_id: str) -> list[LabelEvent]:
        data = self._gql(
            "query($id: String!) { issue(id: $id) { history(first: 100) "
            "{ nodes { id createdAt actor { id } addedLabels { name } } } } }", {"id": issue_id})
        events = []
        for n in ((data.get("issue") or {}).get("history") or {}).get("nodes", []):
            for label in n.get("addedLabels") or []:
                events.append(LabelEvent(id=f"{n['id']}:{label['name']}", label_name=label["name"],
                                         actor_id=(n.get("actor") or {}).get("id"),
                                         created_at=parse_dt(n.get("createdAt")) or utcnow()))
        return events
