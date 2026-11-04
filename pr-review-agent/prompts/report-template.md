# Report Template

Write the final report to `.pr-review/<review-id>/report.md` using exactly this
structure (the `validate_report` hook checks it), and write the same findings as
a JSON array to `.pr-review/<review-id>/findings.json`.

````markdown
# PR Review: <PR title or branch>

**Target:** <PR #N / branch> · **Base:** <base ref> · **Head:** <head sha short>
**Files changed:** <n> · **Lines:** +<added> / -<removed>
**Dimensions reviewed:** correctness, security, performance, maintainability, tests

## Verdict

<one of: APPROVE | COMMENT | REQUEST_CHANGES>

<2–4 sentences: what the PR does, overall quality, and the main reason for the verdict.>

## Summary

| Severity | Count |
|----------|-------|
| critical | 0 |
| high     | 0 |
| medium   | 0 |
| low      | 0 |
| nit      | 0 |

## Findings

### 1. [HIGH] <title>

- **Location:** `path/to/file.py:42`
- **Dimension:** correctness · **Confidence:** high · **Verification:** confirmed
- **Problem:** <what is wrong, failure scenario>
- **Evidence:** <short code quote>
- **Suggestion:** <concrete fix>

### 2. [MEDIUM] <title>
...

## What looks good

- <1–3 genuine positives, brief. Skip the section if there are none worth noting.>

## Not reviewed / limitations

- <files skipped (generated, binary, lockfiles, too large), anything you could not verify>
````

## Verdict rules

- Any confirmed `critical` or `high` → `REQUEST_CHANGES`.
- Only `medium` and below → `COMMENT`.
- Only `low`/`nit` or no findings → `APPROVE`.

## Ordering

Sort findings by severity (critical → nit), then by confidence. Include only
findings whose verification verdict is `confirmed` or `plausible`; list rejected
ones nowhere. If there are no findings, write `_No issues found._` under
`## Findings`.

## findings.json schema

Same objects as in `reviewer-system.md`, plus `"verification": "confirmed" | "plausible"`.
