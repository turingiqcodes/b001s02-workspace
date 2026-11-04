#!/usr/bin/env python3
"""Tests for the pr-review-agent hooks. Run: python3 tests/test_hooks.py"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HOOKS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "hooks", "scripts")


def run_hook(name, payload):
    out = subprocess.run(
        [sys.executable, os.path.join(HOOKS, name)],
        input=json.dumps(payload), capture_output=True, text=True, timeout=10,
    )
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout) if out.stdout.strip() else None


def decision(result):
    return result and result["hookSpecificOutput"]["permissionDecision"]


class RepoCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.realpath(self.tmp.name)
        subprocess.run(["git", "init", "-q", self.repo], check=True)
        os.makedirs(os.path.join(self.repo, ".pr-review", "r1"))

    def tearDown(self):
        self.tmp.cleanup()

    def lock(self):
        with open(os.path.join(self.repo, ".pr-review", "active"), "w") as f:
            f.write("r1")

    def bash(self, cmd):
        return run_hook("review_guard.py", {"cwd": self.repo, "tool_name": "Bash", "tool_input": {"command": cmd}})


class ReviewGuardTest(RepoCase):
    def test_inactive_allows_everything(self):
        self.assertIsNone(self.bash("git push --force"))

    def test_blocks_mutations_when_active(self):
        self.lock()
        for cmd in ["git push origin x", "git checkout main", "rm -rf src", "sed -i s/a/b/ f.py",
                    "echo hi > src/a.py", "gh pr merge 3", "pip install foo",
                    "bash scripts/gather_pr_context.sh 5; rm -rf src"]:
            self.assertEqual(decision(self.bash(cmd)), "deny", cmd)

    def test_allows_reads_when_active(self):
        self.lock()
        for cmd in ["git diff main...HEAD", "git log --oneline", "grep -rn foo src", "gh pr view 3",
                    "ls 2>&1", "cat x > /dev/null", "bash /p/scripts/gather_pr_context.sh 12",
                    "PR_REVIEW_NO_LOCK=1 bash \"/p/scripts/gather_pr_context.sh\" feature/x",
                    "bash /p/scripts/finish_review.sh"]:
            self.assertIsNone(self.bash(cmd), cmd)

    def test_write_only_under_review_dir(self):
        self.lock()
        bad = run_hook("review_guard.py", {"cwd": self.repo, "tool_name": "Write",
                                           "tool_input": {"file_path": os.path.join(self.repo, "src/a.py")}})
        good = run_hook("review_guard.py", {"cwd": self.repo, "tool_name": "Write",
                                            "tool_input": {"file_path": os.path.join(self.repo, ".pr-review/r1/report.md")}})
        sneaky = run_hook("review_guard.py", {"cwd": self.repo, "tool_name": "Edit",
                                              "tool_input": {"file_path": os.path.join(self.repo, ".pr-review/../src/a.py")}})
        self.assertEqual(decision(bad), "deny")
        self.assertIsNone(good)
        self.assertEqual(decision(sneaky), "deny")


class ConfirmPublishTest(unittest.TestCase):
    def bash(self, cmd):
        return decision(run_hook("confirm_publish.py", {"tool_name": "Bash", "tool_input": {"command": cmd}}))

    def test_asks_before_publishing(self):
        self.assertEqual(self.bash("gh pr comment 3 --body hi"), "ask")
        self.assertEqual(self.bash("gh api repos/o/r/pulls/3/reviews --method POST --input p.json"), "ask")
        self.assertEqual(decision(run_hook("confirm_publish.py", {
            "tool_name": "mcp__github__create_pull_request_review", "tool_input": {"event": "COMMENT"}})), "ask")

    def test_denies_approve(self):
        self.assertEqual(self.bash("gh pr review 3 --approve"), "deny")
        self.assertEqual(decision(run_hook("confirm_publish.py", {
            "tool_name": "mcp__github__create_pull_request_review", "tool_input": {"event": "APPROVE"}})), "deny")

    def test_reads_pass(self):
        self.assertIsNone(self.bash("gh api repos/o/r/pulls/3/comments --paginate"))
        self.assertIsNone(self.bash("gh pr view 3"))
        self.assertIsNone(self.bash("git status"))


GOOD_REPORT = """# PR Review: x

## Verdict

REQUEST_CHANGES

Text.

## Summary

| Severity | Count |
|---|---|

## Findings

### 1. [HIGH] Bug

- **Location:** `src/a.py:4`

### 2. [LOW] Name

- **Location:** `src/b.py:9`
"""


class ValidateReportTest(RepoCase):
    def validate(self, name, content):
        path = os.path.join(self.repo, ".pr-review", "r1", name)
        with open(path, "w") as f:
            f.write(content)
        return run_hook("validate_report.py", {"cwd": self.repo, "tool_name": "Write", "tool_input": {"file_path": path}})

    def test_good_report(self):
        self.assertIsNone(self.validate("report.md", GOOD_REPORT))
        self.assertIsNone(self.validate("report.md", "## Verdict\n\nAPPROVE\n\n## Summary\n\n## Findings\n\n_No issues found._\n"))

    def test_bad_reports(self):
        cases = {
            "no verdict": GOOD_REPORT.replace("REQUEST_CHANGES", "LGTM"),
            "no location": GOOD_REPORT.replace("`src/a.py:4`", "somewhere"),
            "unsorted": GOOD_REPORT.replace("[HIGH]", "[TMP]").replace("[LOW]", "[HIGH]").replace("[TMP]", "[LOW]"),
            "approve with high": GOOD_REPORT.replace("REQUEST_CHANGES", "APPROVE"),
        }
        for label, text in cases.items():
            result = self.validate("report.md", text)
            self.assertEqual(result and result["decision"], "block", label)

    def test_findings_json(self):
        good = [{"dimension": "security", "severity": "high", "title": "t", "file": "a.py", "line": 3,
                 "failure_scenario": "f", "suggestion": "s"}]
        self.assertIsNone(self.validate("findings.json", json.dumps(good)))
        self.assertEqual(self.validate("findings.json", "{not json")["decision"], "block")
        self.assertEqual(self.validate("findings.json", json.dumps([{"severity": "huge"}]))["decision"], "block")

    def test_ignores_other_files(self):
        self.assertIsNone(run_hook("validate_report.py", {"cwd": self.repo, "tool_input": {"file_path": "/x/report.md"}}))


class StopCheckTest(RepoCase):
    def stop(self, **extra):
        return run_hook("review_complete_check.py", {"cwd": self.repo, **extra})

    def test_no_review(self):
        self.assertIsNone(self.stop())

    def test_blocks_unfinished_once(self):
        self.lock()
        self.assertEqual(self.stop()["decision"], "block")
        self.assertIsNone(self.stop(stop_hook_active=True))


if __name__ == "__main__":
    unittest.main(verbosity=1)
