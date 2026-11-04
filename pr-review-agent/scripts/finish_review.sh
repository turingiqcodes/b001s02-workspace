#!/usr/bin/env bash
# Release the review lock created by gather_pr_context.sh.
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
lock="$root/.pr-review/active"
if [ -f "$lock" ]; then
  echo "review $(cat "$lock") finished"
  rm -f "$lock"
else
  echo "no active review"
fi
