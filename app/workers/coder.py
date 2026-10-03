"""Coder worker: delegate coding to the Claude Code GitHub Action on the demo repo.

Build step 3.1 (hero by default): job inserted by hand -> PR opens and the webhook marks the
job within 10 min. Try it: `make coder-demo` against a deployed server.

Flow (docs/DECISIONS.md §9.3 and §9.4):
1. `run_coder()` claims a queued coder job and opens an issue in GITHUB_DEMO_REPO that ends with
   "@claude please fix this and open a pull request." The job stays `running` with the issue
   number in `result`.
2. The Action (anthropics/claude-code-action@v1) pushes a `claude/...` branch but does not open
   the PR. When it finishes it edits its issue comment to add
   "[Create a PR](https://github.com/{owner}/{repo}/compare/{base}...{branch}?quick_pull=1&...)"
   (src/entrypoints/update-comment-link.ts, read 2026-10-03).
3. POST /github/hook receives that `issue_comment` event (HMAC-checked) and opens the PR through
   the API. A `pull_request` "opened" event for a claude/ branch does the same, if a PR appears
   another way. Then, by pre-approval (step 4.2, D17, app/approvals.py):
   - no preapproval: `needs_approval`, "I opened a pull request: <label>. Merge it?", held for
     the arrival call;
   - preapproval without require_tests_pass: `approved` at once;
   - require_tests_pass ("merge it if the tests pass"): the job stays `running` with
     result.tests = "pending" until the demo repo's Tests workflow reports. A `check_run`
     "completed" event named TESTS_CHECK on the PR moves it to `approved` (success) or
     `exception` ("The tests failed on the pull request for <label>. Merge it anyway?").
     No result within TESTS_TIMEOUT_MINUTES -> `exception` too.
4. `approved` (pre-approval or a spoken yes through approve_action): `run_coder()` squash-merges
   the PR and marks the job `done`. Merging is the irreversible part, so it never happens before
   a yes.
5. No PR after PR_TIMEOUT_MINUTES -> `failed`, so the driver hears about it.

Test results come by webhook (`check_run`, added to the demo repo hook in step 4.2) because the
fine-grained GITHUB_TOKEN has no Checks or Actions read permission (403 on both, 2026-10-03).

GitHub REST (api.github.com, X-GitHub-Api-Version 2022-11-28): POST /repos/{repo}/issues,
POST /repos/{repo}/pulls, GET /repos/{repo}/pulls?head=owner:branch,
PUT /repos/{repo}/pulls/{n}/merge. Webhook signature: X-Hub-Signature-256 = "sha256=" +
HMAC-SHA256(raw body, GITHUB_WEBHOOK_SECRET).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status
from psycopg import AsyncConnection
from psycopg.rows import class_row

from app import approvals, db, jobs
from app.config import settings
from app.jobs import Job, JobState, JobType

log = logging.getLogger(__name__)
router = APIRouter()

API = "https://api.github.com"
ISSUE_FOOTER = "@claude please fix this and open a pull request."
BRANCH_PREFIX = "claude/"
PR_TIMEOUT_MINUTES = 15
TESTS_CHECK = "tests"  # job name in the demo repo's .github/workflows/tests.yml
TESTS_TIMEOUT_MINUTES = 10
POLL_SECONDS = 5.0
TIMEOUT = 15.0


class GitHubError(RuntimeError):
    pass


class GitHub:
    """Minimal GitHub REST client for one repository."""

    def __init__(self, token: str, repo: str, client: httpx.AsyncClient | None = None):
        if not token or not repo:
            raise GitHubError("GITHUB_TOKEN and GITHUB_DEMO_REPO must be set")
        self.repo = repo
        self.owner = repo.split("/")[0]
        self.http = client or httpx.AsyncClient(timeout=TIMEOUT)
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def _call(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            return await self.http.request(
                method, f"{API}/repos/{self.repo}{path}", headers=self.headers, **kwargs
            )
        except httpx.HTTPError as exc:
            raise GitHubError(f"{method} {path} failed: {type(exc).__name__}") from exc

    @staticmethod
    def _check(response: httpx.Response, what: str) -> dict[str, Any]:
        if response.status_code >= 400:
            raise GitHubError(f"{what}: HTTP {response.status_code} {response.text[:200]}")
        return response.json()

    async def create_issue(self, title: str, body: str) -> dict[str, Any]:
        response = await self._call("POST", "/issues", json={"title": title, "body": body})
        return self._check(response, "create issue")

    async def find_pull(self, branch: str) -> dict[str, Any] | None:
        response = await self._call(
            "GET", "/pulls", params={"head": f"{self.owner}:{branch}", "state": "open"}
        )
        pulls = self._check(response, "list pulls")
        return pulls[0] if pulls else None

    async def open_pull(self, *, title: str, head: str, base: str, body: str) -> dict[str, Any]:
        """Open a PR, or return the open one for this branch if it already exists."""
        response = await self._call(
            "POST", "/pulls", json={"title": title, "head": head, "base": base, "body": body}
        )
        if response.status_code == 422:  # "A pull request already exists" (or no commits)
            existing = await self.find_pull(head)
            if existing:
                return existing
        return self._check(response, "open pull request")

    async def merge_pull(self, number: int) -> dict[str, Any]:
        response = await self._call(
            "PUT", f"/pulls/{number}/merge", json={"merge_method": "squash"}
        )
        return self._check(response, "merge pull request")

    async def aclose(self) -> None:
        await self.http.aclose()


def make_github() -> GitHub:
    return GitHub(settings.github_token, settings.github_demo_repo)


# --- issue text and spoken summaries -------------------------------------------------------------


def label_of(job: Job) -> str:
    return job.details.get("label") or job.details.get("title") or job.request or "the code fix"


def issue_for(job: Job) -> tuple[str, str]:
    """(title, body) for the issue, following the demo repo's bug-report template."""
    title = job.details.get("title") or label_of(job)
    description = job.details.get("description") or job.request or title
    body = (
        f"## What happens\n{description}\n\n"
        f"## Requested by voice\n> {job.request or label_of(job)}\n\n"
        "## Done when\n"
        "- [ ] The bug is fixed and the existing tests pass (`pytest -q`)\n"
        "- [ ] New tests cover this case\n\n"
        f"Filed by Shotgun (job {job.id}).\n\n{ISSUE_FOOTER}"
    )
    return f"Bug: {title}" if not title.lower().startswith("bug") else title, body


def ready_summary(job: Job) -> str:
    return f"I opened a pull request: {label_of(job)}. Merge it?"


def tests_failed_summary(job: Job, reason: str) -> str:
    return (
        f"{reason[0].upper()}{reason[1:]} on the pull request for {label_of(job)}. Merge it anyway?"
    )


# --- finding the Action's branch ----------------------------------------------------------------


def parse_create_pr_link(body: str, repo: str) -> dict[str, str] | None:
    """Pull base, branch and suggested title out of the Action's "Create a PR" link."""
    match = re.search(rf"https://github\.com/{re.escape(repo)}/compare/([^\s)]+)", body or "")
    if not match:
        return None
    url = urlparse(match.group(1))
    if "..." not in url.path:
        return None
    base, head = (unquote(part) for part in url.path.split("...", 1))
    query = parse_qs(url.query)
    return {
        "base": base,
        "head": head,
        "title": query.get("title", [""])[0],
        "body": query.get("body", [""])[0],
    }


async def job_for_issue(conn: AsyncConnection, issue_number: int) -> Job | None:
    """The running coder job waiting on this issue (no PR yet), locked for update."""
    cur = conn.cursor(row_factory=class_row(Job))
    await cur.execute(
        """select * from jobs
           where type = 'coder' and state = 'running'
             and (result ->> 'issue_number')::int = %s
             and result ->> 'pr_number' is null
           order by id limit 1 for update skip locked""",
        (issue_number,),
    )
    return await cur.fetchone()


def needs_tests(job: Job) -> bool:
    return bool((job.preapproval or {}).get("require_tests_pass"))


async def pr_ready(conn: AsyncConnection, job: Job, pr: dict[str, Any]) -> Job:
    """The PR exists: hold it, approve it, or wait for the tests (see the module docstring)."""
    patch = {
        "pr_number": pr["number"],
        "pr_url": pr["html_url"],
        "head_sha": pr.get("head", {}).get("sha"),
        "pr_opened_at": datetime.now(UTC).isoformat(),
    }
    if needs_tests(job):
        log.info("coder job %s: PR #%s opened, waiting for the tests", job.id, pr["number"])
        return await jobs.update_result(conn, job.id, patch | {"tests": "pending"})
    job = await jobs.update_result(conn, job.id, patch)
    return await approvals.settle(conn, job, question=ready_summary(job))


async def job_waiting_for_tests(
    conn: AsyncConnection, head_sha: str | None, pr_numbers: list[int]
) -> Job | None:
    """The running coder job whose PR (by number or head commit) is waiting for its tests."""
    cur = conn.cursor(row_factory=class_row(Job))
    await cur.execute(
        """select * from jobs
           where type = 'coder' and state = 'running' and result ->> 'tests' = 'pending'
             and ((result ->> 'pr_number')::int = any(%s) or result ->> 'head_sha' = %s)
           order by id limit 1 for update skip locked""",
        (pr_numbers, head_sha),
    )
    return await cur.fetchone()


async def tests_reported(conn: AsyncConnection, job: Job, conclusion: str | None) -> Job:
    passed = conclusion == "success"
    job = await jobs.update_result(conn, job.id, {"tests": conclusion or "unknown"})
    reason = "the tests passed" if passed else "the tests failed"
    return await approvals.settle(
        conn,
        job,
        question=ready_summary(job),
        exception_summary=None if passed else tests_failed_summary(job, reason),
        tests_passed=passed,
    )


async def handle_event(
    conn: AsyncConnection, gh: GitHub, event: str, payload: dict[str, Any]
) -> Job | None:
    """Act on one webhook delivery. Returns the job it moved, if any. Idempotent."""
    if event == "issue_comment" and payload.get("action") in ("created", "edited"):
        issue = payload.get("issue", {})
        if "pull_request" in issue:
            return None
        link = parse_create_pr_link(payload.get("comment", {}).get("body", ""), gh.repo)
        if not link or not link["head"].startswith(BRANCH_PREFIX):
            return None
        async with conn.transaction():
            job = await job_for_issue(conn, issue["number"])
            if job is None:
                return None
            pr = await gh.open_pull(
                title=link["title"] or f"Fix #{issue['number']}: {issue.get('title', '')}",
                head=link["head"],
                base=link["base"],
                body=f"Fixes #{issue['number']}\n\nOpened by Shotgun for job {job.id}.",
            )
            return await pr_ready(conn, job, pr)

    if event == "check_run" and payload.get("action") == "completed":
        run = payload.get("check_run", {})
        if run.get("name") != TESTS_CHECK:
            return None
        numbers = [pr["number"] for pr in run.get("pull_requests") or [] if "number" in pr]
        async with conn.transaction():
            job = await job_waiting_for_tests(conn, run.get("head_sha"), numbers)
            if job is None:
                return None
            return await tests_reported(conn, job, run.get("conclusion"))

    if event == "pull_request" and payload.get("action") == "opened":
        pr = payload.get("pull_request", {})
        head = pr.get("head", {}).get("ref", "")
        text = f"{head} {pr.get('title', '')} {pr.get('body') or ''}"
        numbers = {int(n) for n in re.findall(r"(?:#|issue-)(\d+)", text)}
        if not head.startswith(BRANCH_PREFIX) or not numbers:
            return None
        async with conn.transaction():
            for number in sorted(numbers):
                job = await job_for_issue(conn, number)
                if job is not None:
                    return await pr_ready(conn, job, pr)
    return None


# --- the worker loop --------------------------------------------------------------------------


async def start_job(conn: AsyncConnection, gh: GitHub, job: Job) -> Job:
    """For a claimed (running) job: open the issue that triggers the Action."""
    title, body = issue_for(job)
    try:
        issue = await gh.create_issue(title, body)
    except GitHubError as exc:
        log.error("coder job %s: %s", job.id, exc)
        return await jobs.transition(
            conn,
            job.id,
            JobState.FAILED,
            summary=f"I couldn't file the issue for {label_of(job)}.",
            error=str(exc),
        )
    log.info("coder job %s: opened issue #%s", job.id, issue["number"])
    return await jobs.update_result(
        conn, job.id, {"issue_number": issue["number"], "issue_url": issue["html_url"]}
    )


async def merge_approved(conn: AsyncConnection, gh: GitHub) -> Job | None:
    """Merge the PR of one approved coder job (only ever after a spoken yes)."""
    async with conn.transaction():
        cur = conn.cursor(row_factory=class_row(Job))
        await cur.execute(
            """select * from jobs where type = 'coder' and state = 'approved'
               order by updated_at, id limit 1 for update skip locked"""
        )
        job = await cur.fetchone()
        if job is None:
            return None
        number = (job.result or {}).get("pr_number")
        try:
            if number is None:
                raise GitHubError("no pull request recorded")
            await gh.merge_pull(number)
        except GitHubError as exc:
            log.error("coder job %s: merge failed: %s", job.id, exc)
            return await jobs.transition(
                conn,
                job.id,
                JobState.FAILED,
                summary=f"I couldn't merge the pull request for {label_of(job)}. It's still open.",
                error=str(exc),
            )
        summary = f"Merged: {label_of(job)}."
        if (job.result or {}).get("tests") == "success":
            summary += " The tests passed."
        return await jobs.transition(conn, job.id, JobState.DONE, summary=summary, note="merged")


async def expire_stale(conn: AsyncConnection, now: datetime | None = None) -> list[Job]:
    """Fail running coder jobs that have had no PR for PR_TIMEOUT_MINUTES."""
    cutoff = (now or datetime.now(UTC)) - timedelta(minutes=PR_TIMEOUT_MINUTES)
    cur = conn.cursor(row_factory=class_row(Job))
    await cur.execute(
        """select * from jobs where type = 'coder' and state = 'running'
             and updated_at < %s and (result is null or result ->> 'pr_number' is null)""",
        (cutoff,),
    )
    expired = []
    for job in await cur.fetchall():
        expired.append(
            await jobs.transition(
                conn,
                job.id,
                JobState.FAILED,
                summary=f"The code fix for {label_of(job)} didn't come back in time.",
                error=f"no pull request after {PR_TIMEOUT_MINUTES} minutes",
            )
        )
    return expired


async def expire_tests(conn: AsyncConnection, now: datetime | None = None) -> list[Job]:
    """Pre-approved PRs whose tests haven't reported in TESTS_TIMEOUT_MINUTES: ask the driver."""
    cutoff = (now or datetime.now(UTC)) - timedelta(minutes=TESTS_TIMEOUT_MINUTES)
    cur = conn.cursor(row_factory=class_row(Job))
    await cur.execute(
        """select * from jobs where type = 'coder' and state = 'running'
             and result ->> 'tests' = 'pending'
             and (result ->> 'pr_opened_at')::timestamptz < %s""",
        (cutoff,),
    )
    expired = []
    for job in await cur.fetchall():
        job = await jobs.update_result(conn, job.id, {"tests": "timed_out"})
        expired.append(
            await approvals.settle(
                conn,
                job,
                question=ready_summary(job),
                exception_summary=tests_failed_summary(job, "the tests never reported"),
                tests_passed=None,
            )
        )
    return expired


async def tick(conn: AsyncConnection, gh: GitHub) -> None:
    job = await jobs.claim_next(conn, [JobType.CODER])
    if job is not None:
        await start_job(conn, gh, job)
    await merge_approved(conn, gh)
    await expire_stale(conn)
    await expire_tests(conn)


async def run_coder(pool) -> None:
    """Background loop started by the app lifespan."""
    gh = make_github()
    try:
        while True:
            try:
                async with pool.connection() as conn:
                    await tick(conn, gh)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("coder loop error")
            await asyncio.sleep(POLL_SECONDS)
    finally:
        await gh.aclose()


# --- webhook -------------------------------------------------------------------------------------


def verify_signature(body: bytes, signature: str | None, secret: str) -> None:
    if not secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "webhook secret not configured")
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(signature, expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad signature")


async def process_event(event: str, payload: dict[str, Any]) -> None:
    """Background task for one delivery: errors are logged, never returned to GitHub."""
    pool = db.get_pool()
    if pool is None:
        log.error("github hook: no database")
        return
    gh = make_github()
    try:
        async with pool.connection() as conn:
            job = await handle_event(conn, gh, event, payload)
        if job is not None:
            log.info("github hook: job %s -> %s", job.id, job.state)
    except Exception:
        log.exception("github hook: %s event failed", event)
    finally:
        await gh.aclose()


@router.post("/github/hook", status_code=status.HTTP_202_ACCEPTED)
async def github_hook(
    request: Request,
    background: BackgroundTasks,
    x_github_event: str | None = Header(default=None),
    x_hub_signature_256: str | None = Header(default=None),
) -> dict[str, bool]:
    body = await request.body()
    verify_signature(body, x_hub_signature_256, settings.github_webhook_secret)
    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "body is not JSON") from exc
    background.add_task(process_event, x_github_event or "", payload)
    return {"accepted": True}


# --- step 3.1 by hand ----------------------------------------------------------------------------


async def _demo(wait_minutes: float, preapproved: bool = False) -> Job:
    """Insert a coder job for the planted bug, then watch it until it needs approval.

    preapproved: the job carries "merge it if the tests pass" (step 4.2), so it should go
    running -> approved -> done with no question once the demo repo's tests pass.
    """
    pool = await db.open_pool()
    try:
        async with pool.connection() as conn:
            await jobs.init_schema(conn)
            job = await jobs.create_job(
                conn,
                JobType.CODER,
                {
                    "label": "Fix the login bug",
                    "title": "Login fails when the email has capital letters or spaces",
                    "description": (
                        "Typing Sarah@Example.com or 'sarah@example.com ' with the right password "
                        "gives 'Invalid email or password.' Emails should match case-insensitively "
                        "with surrounding whitespace ignored. See authenticate() in auth.py."
                    ),
                },
                request="Fix the login bug Sarah filed.",
                source="demo",
                preapproval=(
                    {"condition": "merge it if the tests pass", "require_tests_pass": True}
                    if preapproved
                    else None
                ),
            )
        print(f"job {job.id} queued; the deployed app's coder loop picks it up")
        start = datetime.now(UTC)
        last = None
        while datetime.now(UTC) - start < timedelta(minutes=wait_minutes):
            async with pool.connection() as conn:
                job = await jobs.get_job(conn, job.id)
            seen = (
                job.state,
                (job.result or {}).get("issue_url"),
                (job.result or {}).get("pr_url"),
            )
            if seen != last:
                elapsed = (datetime.now(UTC) - start).total_seconds() / 60
                print(f"{elapsed:4.1f} min  {job.state}  issue={seen[1]}  pr={seen[2]}")
                last = seen
            if job.state in (*jobs.WAITING, JobState.DONE, JobState.FAILED):
                print(f"summary: {job.summary}")
                return job
            await asyncio.sleep(10)
        print(f"still {job.state} after {wait_minutes} minutes")
        return job
    finally:
        await db.close_pool()


if __name__ == "__main__":
    import argparse

    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Step 3.1: insert a coder job and watch it.")
    parser.add_argument("--demo", action="store_true", required=True)
    parser.add_argument("--wait", type=float, default=10.0, help="minutes to watch (default 10)")
    parser.add_argument(
        "--preapproved", action="store_true", help='step 4.2: "merge it if the tests pass"'
    )
    args = parser.parse_args()
    asyncio.run(_demo(args.wait, args.preapproved))
