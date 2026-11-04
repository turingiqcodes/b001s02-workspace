# Post a Review to GitHub

Turn `findings.json` + `report.md` into a GitHub pull request review.

## Mapping

- **Review body:** the `## Verdict` paragraph and the `## Summary` table from
  `report.md`, followed by any findings that cannot be anchored inline (file not
  in the diff, line outside a hunk). End with:
  `_Automated review by pr-review-agent — findings are suggestions; please verify._`
- **Inline comments:** one per finding whose `file`/`line` is on the new side of
  a diff hunk. Comment body:

  ```markdown
  **[SEVERITY] title** (dimension, confidence)

  failure_scenario

  **Suggestion:** suggestion
  ```

- **Event:** always `COMMENT` unless the user explicitly asks for
  `REQUEST_CHANGES`. Never use `APPROVE` — approval is a human decision.

## Rules

- Skip `nit` findings inline unless the user asked for them; mention the count in
  the body instead.
- Do not post duplicates: list existing review comments first and skip any finding
  already raised at the same `path:line` with the same title.
- Show the user the full list of comments (count, paths, event) and wait for an
  explicit "yes" before posting. The `confirm_publish` hook will also ask.
