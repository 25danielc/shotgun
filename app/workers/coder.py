"""Coder worker: delegate coding to the Claude Code GitHub Action on the demo repo.

Build step 3.1 (hero by default): job inserted by hand -> PR opens and the webhook marks the
job within 10 min.

Flow: job -> create an issue in GITHUB_DEMO_REPO that mentions @claude -> the Action pushes a
claude/* branch -> POST /github/hook (HMAC-verified X-Hub-Signature-256 with
GITHUB_WEBHOOK_SECRET) -> job moves on with a summary like "PR 4 fixes the login bug".

Caveat (docs/DECISIONS.md section 9.4): claude-code-action@v1 does not open the PR itself; it
posts a prefilled "create PR" link. So on the claude/* branch push/create event, this worker
opens the PR with the GitHub API, then sets the job to needs_approval.
Merging is irreversible: "yes" via approve_action -> merge -> done.
"""

from fastapi import APIRouter

router = APIRouter()
