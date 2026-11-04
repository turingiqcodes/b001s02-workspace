"""Shared helpers for the pr-review-agent hooks."""
import json
import os
import subprocess
import sys


def read_input():
    try:
        return json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return {}


def repo_root(cwd):
    try:
        out = subprocess.run(
            ["git", "-C", cwd or ".", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return cwd or os.getcwd()


def active_review(root):
    """Return the active review id, or None when no review lock is held."""
    lock = os.path.join(root, ".pr-review", "active")
    try:
        with open(lock) as f:
            return f.read().strip() or None
    except OSError:
        return None


def pre_tool_decision(decision, reason):
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)
