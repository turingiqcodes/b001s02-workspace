---
name: pr-describe
description: Draft a pull request title and description from the current branch's diff and commits, or a given PR/branch. Use when the user asks to write or improve a PR description, or runs /pr-describe. Args - optional target (PR number or branch).
---

# Describe PR

1. Set `PROMPTS_DIR` to `<this skill's base directory>/../../prompts` and
   `SCRIPTS_DIR` to `<this skill's base directory>/../../scripts`. Read
   `PROMPTS_DIR/pr-description.md`.
2. Gather context without taking the review lock:
   `PR_REVIEW_NO_LOCK=1 bash "$SCRIPTS_DIR/gather_pr_context.sh" <target>`.
   Read `commits.txt`, `files.txt` and `diff.patch` from the printed `CONTEXT_DIR`.
3. Write a title (under 72 characters, imperative mood) and a description that
   follows the template. Save them to `CONTEXT_DIR/description.md`.
4. Show the title and description to the user. Update the PR
   (`gh pr edit <n> --body-file ...`) only if the user asks. The
   `confirm_publish` hook asks for confirmation first.
