GitHub webhook payloads (trimmed to the fields app/workers/coder.py reads). The comment body
follows claude-code-action@v1's update-comment-link.ts: a "[Create a PR](.../compare/base...branch
?quick_pull=1&title=...&body=...)" link appended when the Action finishes on an issue.

`check_run_completed.json`: the `check_run` "completed" event a repository webhook gets when the demo repo's Tests workflow (job `tests`) finishes on a PR (step 4.2). Trimmed to name, head_sha, conclusion and pull_requests.
