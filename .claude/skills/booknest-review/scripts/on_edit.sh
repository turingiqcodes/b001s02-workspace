#!/usr/bin/env bash
# PostToolUse hook: after Claude edits or writes a file under BookNest/booknest/, run the
# booknest-review static checks and hand the result back to Claude as context.
# Reads the hook's JSON from stdin; prints nothing (and exits 0) for any other file.
set -u
PROJECT="${CLAUDE_PROJECT_DIR:-$(pwd)}"
SKILL_DIR="$PROJECT/.claude/skills/booknest-review"
APP_DIR="$PROJECT/BookNest/booknest"

exec python3 -I - "$PROJECT" "$SKILL_DIR" "$APP_DIR" 3<&0 <<'PY'
import json, os, subprocess, sys

project, skill_dir, app_dir = sys.argv[1:4]
try:
    event = json.load(os.fdopen(3))
except ValueError:
    sys.exit(0)
path = (event.get("tool_input") or {}).get("file_path") or (event.get("tool_response") or {}).get("filePath") or ""
if not path:
    sys.exit(0)
path = os.path.realpath(path if os.path.isabs(path) else os.path.join(event.get("cwd") or project, path))
app = os.path.realpath(app_dir)
if not path.startswith(app + os.sep) or "/.venv/" in path or "/__pycache__/" in path:
    sys.exit(0)

# Remember the file so the Stop hook (on_stop.sh) can ask for a full booknest-review.
pending = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"booknest-review-pending-{event.get('session_id', 'default')}")
with open(pending, "a") as f:
    f.write(path + "\n")

run = subprocess.run(["bash", os.path.join(skill_dir, "scripts", "static_checks.sh"), app],
                     capture_output=True, text=True, timeout=90)
report = (run.stdout + run.stderr).strip()
broken = "== Python compiles\nok" not in report
if len(report) > 6000:
    report = report[:6000] + "\n… (truncated)"
rel = os.path.relpath(path, project)
head = (f"booknest-review static checks after editing {rel}"
        + (" — PYTHON DOES NOT COMPILE, fix this first." if broken else " (candidates only; confirm any hit by reading the code)."))
print(json.dumps({
    "systemMessage": f"booknest-review checks ran for {rel}" + (" — compile error!" if broken else ""),
    "hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": head + "\n\n" + report},
}))
PY
