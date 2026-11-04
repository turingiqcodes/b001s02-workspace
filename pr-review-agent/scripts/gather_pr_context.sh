#!/usr/bin/env bash
# Collect the diff and metadata for a review target into .pr-review/<id>/.
#
# Usage: gather_pr_context.sh [target]
#   target: PR number, PR URL, branch name, or empty (current branch vs base).
# Env:
#   PR_REVIEW_BASE     base branch for local diffs (default: origin/main, main, master)
#   PR_REVIEW_NO_LOCK  set to 1 to skip creating the review lock
set -euo pipefail

target="${1:-}"
root="$(git rev-parse --show-toplevel)"
cd "$root"

out_root="$root/.pr-review"
mkdir -p "$out_root"
[ -f "$out_root/.gitignore" ] || echo '*' > "$out_root/.gitignore"

resolve_base() {
  if [ -n "${PR_REVIEW_BASE:-}" ]; then echo "$PR_REVIEW_BASE"; return; fi
  for b in origin/main main origin/master master; do
    if git rev-parse --verify --quiet "$b" >/dev/null; then echo "$b"; return; fi
  done
  echo "no base branch found; set PR_REVIEW_BASE" >&2
  exit 1
}

if [[ "$target" =~ ^[0-9]+$ || "$target" =~ ^https?://.*/pull/[0-9]+ ]]; then
  command -v gh >/dev/null || { echo "gh CLI is required for PR targets" >&2; exit 1; }
  pr_number="$(gh pr view "$target" --json number -q .number)"
  id="pr-${pr_number}-$(date +%Y%m%d-%H%M%S)"
  dir="$out_root/$id"
  mkdir -p "$dir"
  gh pr view "$target" --json number,title,url,baseRefName,headRefName,headRefOid,author,additions,deletions,changedFiles \
    -q '"pr_number=\(.number)\ntitle=\(.title)\nurl=\(.url)\nbase=\(.baseRefName)\nhead=\(.headRefName)\nhead_sha=\(.headRefOid)\nauthor=\(.author.login)\nadditions=\(.additions)\ndeletions=\(.deletions)\nchanged_files=\(.changedFiles)"' \
    > "$dir/meta.txt"
  # The PR body is author-written, untrusted text; it's stored separately so it stays out of the metadata.
  gh pr view "$target" --json body -q .body > "$dir/description.untrusted.md"
  gh pr view "$target" --json commits -q '.commits[] | "\(.oid[0:9]) \(.messageHeadline)"' > "$dir/commits.txt"
  gh pr diff "$target" > "$dir/diff.patch"
  gh pr diff "$target" --name-only > "$dir/files.txt"
else
  base="$(resolve_base)"
  head="${target:-HEAD}"
  git rev-parse --verify --quiet "$head" >/dev/null || { echo "unknown ref: $head" >&2; exit 1; }
  merge_base="$(git merge-base "$base" "$head")"
  head_name="$(git rev-parse --abbrev-ref "$head")"
  id="branch-$(echo "$head_name" | tr '/' '-')-$(date +%Y%m%d-%H%M%S)"
  dir="$out_root/$id"
  mkdir -p "$dir"
  read -r adds dels < <(git diff --numstat "$merge_base" "$head" | awk '{a+=$1; d+=$2} END {print a+0, d+0}')
  {
    echo "title=$(git log -1 --format=%s "$head")"
    echo "base=$base"
    echo "head=$head_name"
    echo "head_sha=$(git rev-parse "$head")"
    echo "merge_base=$merge_base"
    echo "additions=$adds"
    echo "deletions=$dels"
    echo "changed_files=$(git diff --name-only "$merge_base" "$head" | wc -l | tr -d ' ')"
  } > "$dir/meta.txt"
  git log --format='%h %s' "$merge_base..$head" > "$dir/commits.txt"
  git diff "$merge_base" "$head" > "$dir/diff.patch"
  git diff --name-only "$merge_base" "$head" > "$dir/files.txt"
fi

if [ "${PR_REVIEW_NO_LOCK:-0}" != "1" ]; then
  echo "$id" > "$out_root/active"
fi

echo "CONTEXT_DIR=$dir"
