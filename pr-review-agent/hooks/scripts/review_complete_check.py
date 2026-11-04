#!/usr/bin/env python3
"""Stop: don't let Claude end its turn halfway through a review.

If a review lock is held and the review has no report.md yet, block the stop
once and tell Claude to finish (or release the lock). `stop_hook_active`
prevents an infinite loop.
"""
import json
import os

from _common import active_review, read_input, repo_root


def main():
    data = read_input()
    if data.get("stop_hook_active"):
        return
    root = repo_root(data.get("cwd"))
    review_id = active_review(root)
    if not review_id:
        return
    review_dir = os.path.join(root, ".pr-review", review_id)
    if not os.path.exists(os.path.join(review_dir, "report.md")):
        reason = (f"PR review '{review_id}' is still active and has no report. Finish the pr-review steps "
                  f"(write {review_dir}/report.md and findings.json), or if the review is abandoned "
                  f"run finish_review.sh and tell the user why.")
    else:
        reason = (f"Report for '{review_id}' is written but the review lock is still held. "
                  f"Run finish_review.sh, then summarize the verdict for the user.")
    print(json.dumps({"decision": "block", "reason": reason}))


if __name__ == "__main__":
    main()
