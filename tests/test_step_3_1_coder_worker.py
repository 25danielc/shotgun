"""Step 3.1: coder worker (issue -> Claude Code Action -> PR -> spoken yes -> merge).

Pass check (live): job inserted by hand -> PR opens and the webhook marks the job within 10 min
(`make coder-demo` against the deployed app). Offline: a fake GitHub API and the real webhook
payload shape from tests/fixtures/github/.
"""

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from app import jobs
from app.config import settings
from app.jobs import JobState, JobType
from app.main import app
from app.workers import coder

REPO = "25danielc/shotgun-demo-app"
COMMENT = json.loads(
    (Path(__file__).parent / "fixtures" / "github" / "issue_comment_create_pr.json").read_text()
)
BRANCH = "claude/issue-7-20261003-1530"


class FakeGitHub:
    """Records calls; answers like api.github.com."""

    def __init__(self):
        self.calls = []
        self.fail = {}  # (method, path suffix) -> status
        self.next_issue = 7

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix(f"/repos/{REPO}")
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, body))
        assert request.headers["authorization"] == "Bearer test-token"
        for (method, suffix), code in self.fail.items():
            if request.method == method and path.endswith(suffix):
                return httpx.Response(code, json={"message": "nope"})
        if request.method == "POST" and path == "/issues":
            number = self.next_issue
            return httpx.Response(
                201,
                json={"number": number, "html_url": f"https://github.com/{REPO}/issues/{number}"},
            )
        if request.method == "POST" and path == "/pulls":
            return httpx.Response(
                201, json={"number": 8, "html_url": f"https://github.com/{REPO}/pull/8"}
            )
        if request.method == "GET" and path == "/pulls":
            return httpx.Response(
                200, json=[{"number": 5, "html_url": f"https://github.com/{REPO}/pull/5"}]
            )
        if request.method == "PUT" and path.endswith("/merge"):
            return httpx.Response(200, json={"merged": True})
        return httpx.Response(404)

    def client(self) -> coder.GitHub:
        transport = httpx.MockTransport(self.handler)
        return coder.GitHub("test-token", REPO, client=httpx.AsyncClient(transport=transport))

    def paths(self, method):
        return [path for m, path, _ in self.calls if m == method]


@pytest.fixture
def fake():
    return FakeGitHub()


@pytest.fixture
def gh(fake):
    return fake.client()


async def coder_job(db, **details):
    details = {
        "label": "Fix the login bug",
        "title": "Login fails when the email has capital letters or spaces",
        "description": "Sarah@Example.com is rejected with the right password.",
        **details,
    }
    return await jobs.create_job(
        db, JobType.CODER, details, request="Fix the login bug Sarah filed."
    )


async def waiting_for_pr(db, gh):
    """A coder job that has filed issue #7 and is waiting for the Action."""
    await coder_job(db)
    job = await jobs.claim_next(db, [JobType.CODER])
    return await coder.start_job(db, gh, job)


# --- issue ------------------------------------------------------------------------------------


async def test_claimed_job_files_an_issue_that_triggers_the_action(db, gh, fake):
    job = await waiting_for_pr(db, gh)
    [(method, path, body)] = fake.calls
    assert (method, path) == ("POST", "/issues")
    assert body["title"] == "Bug: Login fails when the email has capital letters or spaces"
    assert body["body"].endswith("@claude please fix this and open a pull request.")
    assert "Sarah@Example.com is rejected" in body["body"]
    assert job.state is JobState.RUNNING
    assert job.result == {"issue_number": 7, "issue_url": f"https://github.com/{REPO}/issues/7"}


async def test_issue_failure_fails_the_job_with_a_spoken_reason(db, gh, fake):
    fake.fail[("POST", "/issues")] = 403
    await coder_job(db)
    job = await coder.start_job(db, gh, await jobs.claim_next(db, [JobType.CODER]))
    assert job.state is JobState.FAILED
    assert job.summary == "I couldn't file the issue for Fix the login bug."


# --- webhook: PR ready ------------------------------------------------------------------------


def test_parse_create_pr_link():
    link = coder.parse_create_pr_link(COMMENT["comment"]["body"], REPO)
    assert link["base"] == "main"
    assert link["head"] == BRANCH
    assert link["title"] == "Issue #7: Changes from Claude"
    assert coder.parse_create_pr_link("no link here", REPO) is None
    assert coder.parse_create_pr_link(COMMENT["comment"]["body"], "someone/else") is None


async def test_action_comment_opens_pr_and_job_needs_approval(db, gh, fake):
    job = await waiting_for_pr(db, gh)
    moved = await coder.handle_event(db, gh, "issue_comment", COMMENT)
    assert moved.id == job.id
    assert moved.state is JobState.NEEDS_APPROVAL
    assert (
        moved.summary
        == "The fix for the login bug is ready as a pull request. Want me to merge it?"
    )
    assert moved.result["pr_number"] == 8
    _, _, body = fake.calls[-1]
    assert body == {
        "title": "Issue #7: Changes from Claude",
        "head": BRANCH,
        "base": "main",
        "body": f"Fixes #7\n\nOpened by Shotgun for job {job.id}.",
    }
    assert "PUT" not in [m for m, _, _ in fake.calls]  # nothing merged before a spoken yes


async def test_repeated_comment_edits_open_one_pr(db, gh, fake):
    await waiting_for_pr(db, gh)
    await coder.handle_event(db, gh, "issue_comment", COMMENT)
    assert await coder.handle_event(db, gh, "issue_comment", COMMENT) is None
    assert fake.paths("POST").count("/pulls") == 1


async def test_existing_pr_is_reused(db, gh, fake):
    await waiting_for_pr(db, gh)
    fake.fail[("POST", "/pulls")] = 422
    moved = await coder.handle_event(db, gh, "issue_comment", COMMENT)
    assert moved.result["pr_number"] == 5


@pytest.mark.parametrize(
    "change",
    [
        {"issue": {"number": 99, "title": "other"}},  # no job for this issue
        {"comment": {"body": "Claude is working… [View job](https://github.com/x)"}},  # no link yet
        {"action": "deleted"},
        {"issue": {"number": 7, "pull_request": {}}},  # a comment on a PR, not an issue
    ],
)
async def test_irrelevant_comments_are_ignored(db, gh, fake, change):
    await waiting_for_pr(db, gh)
    assert await coder.handle_event(db, gh, "issue_comment", COMMENT | change) is None
    assert fake.paths("POST") == ["/issues"]


async def test_pull_request_opened_elsewhere_also_counts(db, gh, fake):
    job = await waiting_for_pr(db, gh)
    payload = {
        "action": "opened",
        "pull_request": {
            "number": 12,
            "html_url": f"https://github.com/{REPO}/pull/12",
            "head": {"ref": BRANCH},
            "title": "Fix login email matching",
            "body": "This PR addresses issue #7",
        },
    }
    moved = await coder.handle_event(db, gh, "pull_request", payload)
    assert (moved.id, moved.state, moved.result["pr_number"]) == (
        job.id,
        JobState.NEEDS_APPROVAL,
        12,
    )
    assert fake.paths("POST") == ["/issues"]  # we didn't open another PR


# --- approval gate and merge ------------------------------------------------------------------


async def test_no_merge_without_approval(db, gh, fake):
    await waiting_for_pr(db, gh)
    await coder.handle_event(db, gh, "issue_comment", COMMENT)
    assert await coder.merge_approved(db, gh) is None
    assert fake.paths("PUT") == []


async def test_spoken_yes_merges_and_finishes(db, gh, fake):
    job = await waiting_for_pr(db, gh)
    await coder.handle_event(db, gh, "issue_comment", COMMENT)
    await jobs.transition(db, job.id, "approved", expect="needs_approval")
    done = await coder.merge_approved(db, gh)
    assert (done.state, done.summary) == (JobState.DONE, "I merged the fix for the login bug.")
    method, path, body = fake.calls[-1]
    assert (method, path, body) == ("PUT", "/pulls/8/merge", {"merge_method": "squash"})


async def test_merge_failure_is_reported(db, gh, fake):
    job = await waiting_for_pr(db, gh)
    await coder.handle_event(db, gh, "issue_comment", COMMENT)
    await jobs.transition(db, job.id, "approved")
    fake.fail[("PUT", "/merge")] = 405
    failed = await coder.merge_approved(db, gh)
    assert failed.state is JobState.FAILED
    assert (
        failed.summary
        == "I couldn't merge the pull request for Fix the login bug. It's still open."
    )


# --- timeout ----------------------------------------------------------------------------------


async def test_no_pr_in_time_fails_the_job(db, gh):
    job = await waiting_for_pr(db, gh)
    now = datetime.now(UTC)
    assert await coder.expire_stale(db, now) == []
    later = now + timedelta(minutes=coder.PR_TIMEOUT_MINUTES + 1)
    [expired] = await coder.expire_stale(db, later)
    assert expired.id == job.id
    assert expired.summary == "The code fix for Fix the login bug didn't come back in time."


async def test_tick_runs_the_whole_loop_once(db, gh, fake):
    await coder_job(db)
    await coder.tick(db, gh)
    assert fake.paths("POST") == ["/issues"]


# --- the HTTP endpoint ------------------------------------------------------------------------


def sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
async def http(monkeypatch):
    monkeypatch.setattr(settings, "github_webhook_secret", "hook-secret")
    seen = []

    async def fake_process(event, payload):
        seen.append((event, payload))

    monkeypatch.setattr(coder, "process_event", fake_process)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        client.seen = seen
        yield client


async def post_hook(http, body: bytes, signature: str | None, event="issue_comment"):
    headers = {"X-GitHub-Event": event, "Content-Type": "application/json"}
    if signature is not None:
        headers["X-Hub-Signature-256"] = signature
    return await http.post("/github/hook", content=body, headers=headers)


async def test_signed_delivery_is_accepted_and_processed(http):
    body = json.dumps(COMMENT).encode()
    response = await post_hook(http, body, sign(body, "hook-secret"))
    assert response.status_code == 202
    assert http.seen == [("issue_comment", COMMENT)]


@pytest.mark.parametrize("signature", [None, "sha256=deadbeef", "wrong"])
async def test_bad_signature_is_401(http, signature):
    response = await post_hook(http, json.dumps(COMMENT).encode(), signature)
    assert response.status_code == 401
    assert http.seen == []


async def test_unconfigured_secret_fails_closed(http, monkeypatch):
    monkeypatch.setattr(settings, "github_webhook_secret", "")
    body = b"{}"
    assert (await post_hook(http, body, sign(body, ""))).status_code == 503


# --- the pass check (live) --------------------------------------------------------------------


@pytest.mark.live
async def test_live_job_by_hand_gets_a_pr_within_10_minutes():
    """Needs the deployed app (coder loop + webhook), Neon, and the Action installed on the repo."""
    job = await coder._demo(wait_minutes=10)
    assert job.state is JobState.NEEDS_APPROVAL
    assert job.result["pr_url"]
