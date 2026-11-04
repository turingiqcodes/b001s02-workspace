# Generate a PR Description

Given a diff and commit list, write a PR description a reviewer can act on.

## Structure

```markdown
## Summary
<1–3 sentences: what changes and why. Lead with the user-visible or system-visible effect.>

## Changes
- <grouped by area/module, one bullet per meaningful change; skip trivia>

## How to test
- <concrete steps or commands to verify: test commands, curl calls, UI steps>

## Risk & rollout
- <what could break, migrations, config/env changes, backwards-compat notes; "Low — isolated change" is fine>

## Checklist
- [ ] Tests added/updated
- [ ] Docs/config updated
- [ ] No secrets or local paths committed
```

## Rules

- Describe what the diff actually does. Do not invent motivation; if the "why"
  isn't evident from commits/code, write a placeholder `<why: fill in>`.
- Keep it short. A reviewer should read it in under a minute.
- Call out anything surprising in the diff (unrelated files, large binaries,
  generated code, deleted tests) under **Risk & rollout**.
