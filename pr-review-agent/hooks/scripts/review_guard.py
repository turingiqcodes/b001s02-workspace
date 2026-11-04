#!/usr/bin/env python3
"""PreToolUse: keep the working tree read-only while a PR review is active.

While .pr-review/active exists:
  - Edit/Write are allowed only inside .pr-review/ (report output).
  - Bash commands that mutate the repo, the working tree or the remote are denied.
With no active review, this hook does nothing.
"""
import os
import re

from _common import active_review, pre_tool_decision, read_input, repo_root

MUTATING_BASH = [
    r"\bgit\s+(push|commit|merge|rebase|reset|checkout|switch|restore|stash|cherry-pick|revert|clean|rm|mv|am|apply|tag|branch\s+-[dDmM])\b",
    r"\bgh\s+pr\s+(merge|close|ready|edit|review\s+.*--approve)\b",
    r"\bgh\s+(repo|release)\s+(delete|create|edit)\b",
    r"(^|[;&|]\s*)\s*(rm|mv|cp|chmod|chown|truncate|dd)\s",
    r"\bsed\s+(-[a-zA-Z]*i|--in-place)",
    r"(^|[^<>&])>{1,2}\s*(?!/dev/null|&)(?!\S*\.pr-review/)\S",
    r"\b(pip|npm|yarn|pnpm|poetry|apt|apt-get|brew)\s+(install|add|remove|uninstall|upgrade)\b",
    r"\bterraform\s+(apply|destroy|import)\b",
]

# Review tooling is allowed even though it writes (under .pr-review/ only), but
# only as a bare invocation, so it can't be chained with other commands.
ALLOWED_BASH = re.compile(
    r"\s*(\w+=\S+\s+)*bash\s+\"?[^\s;&|`$()]*(gather_pr_context|finish_review)\.sh\"?(\s+[\w./:#-]+)?\s*"
)


def main():
    data = read_input()
    root = repo_root(data.get("cwd"))
    review_id = active_review(root)
    if not review_id:
        return

    tool = data.get("tool_name", "")
    tool_input = data.get("tool_input") or {}
    reason_tail = f" (review '{review_id}' is active; run finish_review.sh to end it)"

    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        abs_path = os.path.realpath(os.path.join(root, path))
        review_dir = os.path.realpath(os.path.join(root, ".pr-review"))
        if not (abs_path == review_dir or abs_path.startswith(review_dir + os.sep)):
            pre_tool_decision(
                "deny",
                f"PR review is read-only: writes are only allowed under .pr-review/, not {path}" + reason_tail,
            )
        return

    if tool == "Bash":
        cmd = tool_input.get("command", "")
        if ALLOWED_BASH.fullmatch(cmd):
            return
        for pattern in MUTATING_BASH:
            if re.search(pattern, cmd):
                pre_tool_decision(
                    "deny",
                    "PR review is read-only: this command would modify the repo, working tree or remote" + reason_tail,
                )


if __name__ == "__main__":
    main()
