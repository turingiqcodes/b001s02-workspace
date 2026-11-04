---
name: pr-review-post
description: Post a completed pr-review report to GitHub as a pull request review with inline comments. Use only when the user explicitly asks to post, publish, or submit review comments, or runs /pr-review-post. Args - optional review directory (default the latest under .pr-review/) and optional "request-changes".
---

# Post PR Review

Set `PROMPTS_DIR` to `<this skill's base directory>/../../prompts` and read
`PROMPTS_DIR/post-review.md`. It defines the mapping rules.

1. Find the review directory: the one given in the args, or the newest
   `.pr-review/*/` that contains `findings.json`. Read `meta.txt`, `report.md`,
   `findings.json` and `diff.patch`. If `meta.txt` has no `pr_number`, the review was
   of a local branch, so there is nothing to post. Tell the user and stop.
2. Fetch the existing review comments so you don't post duplicates:
   `gh api repos/{owner}/{repo}/pulls/<n>/comments --paginate`
   (or `mcp__github__list_pull_request_reviews` if `gh` is unavailable).
3. Build the review payload following `post-review.md`. Use `diff.patch` to check
   which lines can take an inline comment. Write the payload to
   `<review dir>/review-payload.json`.
4. Show the user the event, the inline comment count, the files, and the body.
   Ask for explicit confirmation.
5. On "yes", post it:

   ```bash
   gh api repos/{owner}/{repo}/pulls/<n>/reviews --method POST --input <review dir>/review-payload.json
   ```

   The `confirm_publish` hook asks for confirmation again at this step. That is
   expected.
6. Report the review URL from the response.
