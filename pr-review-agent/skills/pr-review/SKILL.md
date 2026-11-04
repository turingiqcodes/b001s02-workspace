---
name: pr-review
description: Review a pull request, branch, or the current branch's changes across correctness, security, performance, maintainability and tests, verify each finding, and write a ranked report. Use when the user asks to review a PR, check a branch before merge, or runs /pr-review. Args - optional target (PR number, PR URL, or branch; default current branch vs main) and optional dimensions list (e.g. "security,tests").
---

# PR Review (orchestrator)

All reviewer instructions live in the plugin's `prompts/` folder. Set
`PROMPTS_DIR` to `<this skill's base directory>/../../prompts` (resolve it to an
absolute path) and `SCRIPTS_DIR` to `<this skill's base directory>/../../scripts`.

## 1. Gather context

Run:

```bash
bash "$SCRIPTS_DIR/gather_pr_context.sh" <target>
```

It prints `CONTEXT_DIR=<path>` and writes `meta.txt`, `commits.txt`, `files.txt`
and `diff.patch` there. It also creates the review lock `.pr-review/active`, which
switches on the read-only guard hook. If it fails (no `gh` auth, unknown branch),
tell the user what failed and stop.

Read `meta.txt` and `files.txt`. If the diff is empty, say so, run
`bash "$SCRIPTS_DIR/finish_review.sh"`, and stop.

Skip generated or vendored files (lockfiles, `dist/`, minified assets, binaries,
notebook checkpoints) and list them under "Not reviewed".

## 2. Dimension reviews (parallel)

Dimensions: `correctness`, `security`, `performance`, `maintainability`, `tests`.
The user can narrow this list in the args.

Spawn one `pr-reviewer` subagent per dimension **in a single message** so they run
concurrently. Prompt for each:

```
PROMPTS_DIR=<abs path>
CONTEXT_DIR=<abs path>
Task: review dimension <name>. Read PROMPTS_DIR/reviewer-system.md and
PROMPTS_DIR/dimensions/<name>.md, then review the diff in CONTEXT_DIR.
Return the JSON findings array.
```

For a small diff (under ~150 changed lines), you can review all the dimensions
yourself in one pass instead of spawning subagents. Read the same prompt files
first.

## 3. Verify

Merge the findings and drop duplicates (same file, nearby line, same root cause).
Then send every finding of `medium` severity or above to a `pr-reviewer` subagent
with the task "verify this finding using PROMPTS_DIR/verify-finding.md" and the
finding JSON. Run these concurrently. Drop `rejected` findings. Apply any adjusted
severity or line.

## 4. Report

Read `PROMPTS_DIR/report-template.md` and write:

- `CONTEXT_DIR/report.md`
- `CONTEXT_DIR/findings.json`

The `validate_report` hook checks both files when you write them. If it reports a
problem, fix the file and write it again.

## 5. Finish

Run `bash "$SCRIPTS_DIR/finish_review.sh"` to release the review lock. Then reply
with:

- the verdict and the severity counts
- the top findings, each as one line with `path:line`
- the report path
- an offer to post the review to GitHub with `/pr-review-post` (do not post
  automatically)
