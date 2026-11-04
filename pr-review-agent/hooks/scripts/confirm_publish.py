#!/usr/bin/env python3
"""PreToolUse: ask the user before anything is published to GitHub.

Covers posting reviews/comments, editing PR metadata and merging, whether through
`gh` in Bash or the GitHub MCP tools. Approving a PR is always denied: approval
is a human decision.
"""
import re

from _common import pre_tool_decision, read_input

APPROVE_BASH = [
    r"\bgh\s+pr\s+review\b.*(--approve|-a\b)",
    r"\bgh\s+api\b.*/reviews\b.*\"?event\"?\s*[:=]\s*\"?APPROVE",
]

PUBLISH_BASH = [
    (r"\bgh\s+pr\s+(review|comment)\b", "post a review/comment on the PR"),
    (r"\bgh\s+pr\s+edit\b", "edit the PR title/body/labels"),
    (r"\bgh\s+pr\s+merge\b", "MERGE the PR"),
    (r"\bgh\s+issue\s+comment\b", "comment on an issue"),
    (r"\bgh\s+api\b.*/(reviews|comments)\b", "post a review/comments via the GitHub API"),
]

PUBLISH_MCP = {
    "mcp__github__create_pull_request_review": "post a PR review",
    "mcp__github__add_issue_comment": "post a comment",
    "mcp__github__update_pull_request": "edit the PR",
    "mcp__github__merge_pull_request": "MERGE the PR",
}


def main():
    data = read_input()
    tool = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}

    if tool in PUBLISH_MCP:
        if tool == "mcp__github__create_pull_request_review" and str(tool_input.get("event", "")).upper() == "APPROVE":
            pre_tool_decision("deny", "pr-review-agent never approves PRs; approval is a human decision. Use event COMMENT.")
        pre_tool_decision("ask", f"pr-review-agent: about to {PUBLISH_MCP[tool]} on GitHub. Confirm?")

    if tool == "Bash":
        cmd = tool_input.get("command", "")
        # `gh api` is a read unless it sends a body or uses a write method.
        if re.search(r"\bgh\s+api\b", cmd) and not re.search(r"(--method|-X)\s*(POST|PUT|PATCH|DELETE)|--input|-f\s|-F\s|--field|--raw-field", cmd):
            return
        if any(re.search(p, cmd, re.IGNORECASE) for p in APPROVE_BASH):
            pre_tool_decision("deny", "pr-review-agent never approves PRs; approval is a human decision. Use COMMENT.")
        for pattern, what in PUBLISH_BASH:
            if re.search(pattern, cmd):
                pre_tool_decision("ask", f"pr-review-agent: about to {what} on GitHub. Confirm?")


if __name__ == "__main__":
    main()
