#!/usr/bin/env python3
"""PostToolUse: validate report.md / findings.json written under .pr-review/.

On a problem, returns decision=block with the reason, which Claude sees and
fixes. Other files are ignored.
"""
import json
import os
import re
import sys

from _common import read_input

SEVERITIES = ["critical", "high", "medium", "low", "nit"]
VERDICTS = {"APPROVE", "COMMENT", "REQUEST_CHANGES"}
REQUIRED_FIELDS = ["dimension", "severity", "title", "file", "line", "failure_scenario", "suggestion"]


def check_report(text):
    problems = []
    for section in ("## Verdict", "## Summary", "## Findings"):
        if section not in text:
            problems.append(f"missing section '{section}'")
    m = re.search(r"## Verdict\s*\n+\s*\**([A-Z_]+)", text)
    verdict = m.group(1) if m else None
    if verdict not in VERDICTS:
        problems.append(f"verdict must be one of {sorted(VERDICTS)} on the first line under '## Verdict'")
    headings = re.findall(r"^### \d+\. \[([A-Z]+)\]", text, re.MULTILINE)
    if not headings and "_No issues found._" not in text and "## Findings" in text:
        problems.append("no findings in the form '### N. [SEVERITY] title' and no '_No issues found._' marker")
    blocks = re.split(r"^### \d+\. ", text, flags=re.MULTILINE)[1:]
    for i, block in enumerate(blocks, 1):
        if not re.search(r"\*\*Location:\*\*\s*`[^`]+:\d+`", block):
            problems.append(f"finding {i} has no '**Location:** `path:line`'")
    order = [SEVERITIES.index(h.lower()) for h in headings if h.lower() in SEVERITIES]
    if order != sorted(order):
        problems.append("findings are not sorted by severity (critical → nit)")
    if verdict == "APPROVE" and any(h.lower() in ("critical", "high") for h in headings):
        problems.append("verdict APPROVE with critical/high findings; use REQUEST_CHANGES")
    return problems


def check_findings(text):
    try:
        findings = json.loads(text)
    except json.JSONDecodeError as e:
        return [f"findings.json is not valid JSON: {e}"]
    if not isinstance(findings, list):
        return ["findings.json must be a JSON array"]
    problems = []
    for i, f in enumerate(findings):
        if not isinstance(f, dict):
            problems.append(f"finding {i} is not an object")
            continue
        missing = [k for k in REQUIRED_FIELDS if k not in f]
        if missing:
            problems.append(f"finding {i} missing fields: {', '.join(missing)}")
        if f.get("severity") not in SEVERITIES:
            problems.append(f"finding {i} severity must be one of {SEVERITIES}")
        if not isinstance(f.get("line"), int) or f.get("line", 0) < 1:
            problems.append(f"finding {i} line must be a positive integer")
    return problems


def main():
    data = read_input()
    path = (data.get("tool_input") or {}).get("file_path", "")
    norm = path.replace("\\", "/")
    if "/.pr-review/" not in "/" + norm.lstrip("/"):
        return
    name = os.path.basename(norm)
    if name not in ("report.md", "findings.json"):
        return
    try:
        with open(path if os.path.isabs(path) else os.path.join(data.get("cwd", "."), path)) as f:
            text = f.read()
    except OSError:
        return
    problems = check_report(text) if name == "report.md" else check_findings(text)
    if problems:
        print(json.dumps({
            "decision": "block",
            "reason": f"{name} failed validation (see prompts/report-template.md):\n- " + "\n- ".join(problems),
        }))
    sys.exit(0)


if __name__ == "__main__":
    main()
