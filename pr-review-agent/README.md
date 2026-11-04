# pr-review-agent

A Claude Code plugin that reviews pull requests. It has three skills, a read-only
reviewer subagent, and hooks that keep the review safe. All the prompts live in
`prompts/`. The skills and the agent only point at them, so you can tune the
review without touching any wiring.

```
pr-review-agent/
├── .claude-plugin/
│   ├── plugin.json            plugin manifest
│   └── marketplace.json       local marketplace, for /plugin install
├── prompts/                   ← every prompt the agent uses
│   ├── reviewer-system.md     core rules, severity scale, finding JSON format
│   ├── dimensions/
│   │   ├── correctness.md
│   │   ├── security.md
│   │   ├── performance.md
│   │   ├── maintainability.md
│   │   └── tests.md
│   ├── verify-finding.md      adversarial false-positive check
│   ├── report-template.md     report.md / findings.json format + verdict rules
│   ├── post-review.md         mapping findings → GitHub review
│   └── pr-description.md      PR description template
├── agents/
│   └── pr-reviewer.md         read-only subagent (Read, Grep, Glob, Bash)
├── skills/
│   ├── pr-review/             /pr-review       orchestrates the full review
│   ├── pr-review-post/        /pr-review-post  posts the review to GitHub
│   └── pr-describe/           /pr-describe     drafts a PR description
├── hooks/
│   ├── hooks.json
│   └── scripts/
│       ├── review_guard.py           PreToolUse   read-only while a review is active
│       ├── confirm_publish.py        PreToolUse   ask before posting; never approve
│       ├── validate_report.py        PostToolUse  enforce report/findings format
│       └── review_complete_check.py  Stop         don't stop mid-review
├── scripts/
│   ├── gather_pr_context.sh   diff + metadata → .pr-review/<id>/, takes the lock
│   └── finish_review.sh       releases the lock
└── tests/test_hooks.py
```

## Install

Requirements: `git`, `python3`, and the [`gh`](https://cli.github.com) CLI
(authenticated with `gh auth login`) for reviewing GitHub PRs by number.

Try it for one session:

```bash
claude --plugin-dir /home/training/workspace/pr-review-agent
```

Or install it permanently from inside Claude Code:

```
/plugin marketplace add /home/training/workspace/pr-review-agent
/plugin install pr-review-agent@pr-review-local
```

## Usage

| Command | What it does |
|---|---|
| `/pr-review` | Reviews the current branch against `main` |
| `/pr-review 42` | Reviews GitHub PR #42 |
| `/pr-review feature/login security,tests` | Reviews a branch, on selected dimensions only |
| `/pr-review-post` | Posts the latest review to its PR (asks first; never approves) |
| `/pr-describe` | Drafts a title and description for the current branch |

Output goes to `.pr-review/<id>/`: `meta.txt`, `commits.txt`, `files.txt`,
`diff.patch`, `report.md` and `findings.json`. That directory ignores itself in
git.

## How a review runs

1. **Gather.** `gather_pr_context.sh` saves the diff and metadata and creates
   `.pr-review/active`, the review lock.
2. **Review.** One `pr-reviewer` subagent per dimension runs in parallel. Each one
   reads `reviewer-system.md` plus its own dimension prompt.
3. **Verify.** Each finding of medium severity or above goes to a fresh subagent
   that uses `verify-finding.md` to try to disprove it. Rejected findings are
   dropped.
4. **Report.** Findings are merged, ranked and written using
   `report-template.md`. The verdict is `APPROVE`, `COMMENT` or
   `REQUEST_CHANGES`.
5. **Finish.** `finish_review.sh` releases the lock.

## Hooks

| Hook | Event | Behavior |
|---|---|---|
| `review_guard` | PreToolUse (Bash, Edit, Write, …) | While the lock is held, denies edits outside `.pr-review/` and denies mutating commands (`git push/checkout/reset/commit`, `rm`, `sed -i`, output redirects, `gh pr merge`, package installs, `terraform apply`). Does nothing when no review is active. |
| `confirm_publish` | PreToolUse (Bash, GitHub MCP tools) | Asks before anything is published to GitHub: `gh pr review/comment/edit/merge`, `gh api` writes, MCP review/comment/merge tools. **Always denies approving a PR.** Reads pass through. |
| `validate_report` | PostToolUse (Write, Edit) | Checks `report.md` (sections, verdict, `path:line` locations, severity order, no APPROVE with high findings) and `findings.json` (schema). On failure, it blocks with the reason so Claude fixes the file. |
| `review_complete_check` | Stop | While a review is active and unfinished, blocks Claude from stopping once and tells it to finish the report or release the lock. |

A review that was interrupted can leave the lock behind. Remove it with
`bash scripts/finish_review.sh` or `rm .pr-review/active`.

## Customizing

- **Change what gets checked:** edit the files in `prompts/dimensions/`.
- **Add a dimension:** add `prompts/dimensions/<name>.md` and add `<name>` to the
  list in `skills/pr-review/SKILL.md`.
- **Change the severity scale or finding format:** edit `prompts/reviewer-system.md`
  and the matching checks in `hooks/scripts/validate_report.py`.
- **Allow or deny more commands:** edit `MUTATING_BASH` in `review_guard.py`.

## Tests

```bash
python3 tests/test_hooks.py
```
