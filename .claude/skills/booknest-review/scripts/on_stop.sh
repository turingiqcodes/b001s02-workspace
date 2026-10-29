#!/usr/bin/env bash
# Stop hook: if Claude created or edited files under BookNest/booknest/ this turn (recorded by
# on_edit.sh), keep Claude going once and ask it to run the booknest-review skill on them.
# Prints nothing (and exits 0) when nothing changed, or when this stop is already the follow-up
# to a review request, so a review that edits code can't loop forever.
set -u
PROJECT="${CLAUDE_PROJECT_DIR:-$(pwd)}"

exec python3 -I - "$PROJECT" 3<&0 <<'PY'
import json, os, sys

project = sys.argv[1]
try:
    event = json.load(os.fdopen(3))
except ValueError:
    sys.exit(0)
pending = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"booknest-review-pending-{event.get('session_id', 'default')}")
try:
    with open(pending) as f:
        paths = sorted({line.strip() for line in f if line.strip()})
    os.remove(pending)
except FileNotFoundError:
    sys.exit(0)
if not paths or event.get("stop_hook_active"):
    sys.exit(0)

rel = [os.path.relpath(p, project) for p in paths]
print(json.dumps({
    "systemMessage": f"booknest-review requested for {len(rel)} changed file(s)",
    "decision": "block",
    "reason": "BookNest files were created or changed this turn:\n"
              + "\n".join(f"- {p}" for p in rel)
              + "\n\nInvoke the booknest-review skill (Skill tool, skill \"booknest-review\") scoped to these files"
              + f" (args: {' '.join(rel)}) and report the findings before finishing.",
}))
PY
