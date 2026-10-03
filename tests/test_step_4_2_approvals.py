"""Step 4.2 pass check (D17): pre-approval at dispatch, held approvals and exceptions.

- A pre-approved job reaches done with no question: running -> approved -> done.
- A job whose result breaks its pre-approval goes to `exception` and waits for a spoken yes/no.
- A job with no pre-approval that reaches its irreversible step is held in `needs_approval`.
Which of these ring the phone (at most one exception call, the arrival call) is the 4.1 call
scheduler's job; its tests count the calls. Offline: the fake GitHub API from the 3.1 tests and
the check_run payload in tests/fixtures/github/.
"""

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app import approvals, jobs
from app.jobs import IllegalTransition, JobState, JobType
from app.workers import coder
from tests.test_step_2_2_voice_tools import call, client, sample  # noqa: F401 (client is a fixture)
from tests.test_step_3_1_coder_worker import COMMENT, FakeGitHub

CHECK_RUN = json.loads(
    (Path(__file__).parent / "fixtures" / "github" / "check_run_completed.json").read_text()
)
TESTS_YES = {"condition": "merge it if the tests pass", "require_tests_pass": True}


@pytest.fixture
def fake():
    return FakeGitHub()


@pytest.fixture
def gh(fake):
    return fake.client()


def check_run(conclusion="success", name="tests", pr=8):
    payload = copy.deepcopy(CHECK_RUN)
    payload["check_run"].update(conclusion=conclusion, name=name)
    payload["check_run"]["pull_requests"][0]["number"] = pr
    return payload


async def pr_open(db, gh, preapproval=None):
    """A coder job (optionally pre-approved) whose PR #8 has just been opened."""
    await jobs.create_job(
        db,
        JobType.CODER,
        {"label": "Fix the login bug", "title": "Login fails", "description": "Caps rejected."},
        request="Fix the login bug Sarah filed.",
        preapproval=preapproval,
    )
    job = await coder.start_job(db, gh, await jobs.claim_next(db, [JobType.CODER]))
    return await coder.handle_event(db, gh, "issue_comment", COMMENT) or job


async def states(db, job_id):
    return [to for _, to, _ in await jobs.events(db, job_id)]


# --- the rule itself ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("preapproval", "facts", "reason"),
    [
        (TESTS_YES, {"tests_passed": True}, None),
        (TESTS_YES, {"tests_passed": False}, "the tests failed"),
        (TESTS_YES, {}, "the tests never reported"),
        ({"condition": "if it's under 30", "max_usd": 30}, {"total_usd": 21.4}, None),
        (
            {"condition": "if it's under 30", "max_usd": 30},
            {"total_usd": 34.1},
            "it comes to 34.10 dollars, over your 30 dollar limit",
        ),
        ({"condition": "if it's under 30", "max_usd": 30}, {}, "I couldn't get a price"),
        ({"condition": "just do it"}, {}, None),  # free-text condition: never judged (D17)
    ],
)
def test_only_structured_fields_are_enforced(preapproval, facts, reason):
    assert approvals.broken_by(preapproval, **facts) == reason


async def test_running_to_approved_needs_a_preapproval(db):
    plain = await jobs.create_job(db, JobType.CODER)
    await jobs.transition(db, plain.id, "running")
    with pytest.raises(IllegalTransition):
        await jobs.transition(db, plain.id, "approved")
    yes = await jobs.create_job(db, JobType.CODER, preapproval=TESTS_YES)
    await jobs.transition(db, yes.id, "running")
    assert (await jobs.transition(db, yes.id, "approved")).state is JobState.APPROVED


async def test_exception_waits_for_a_spoken_answer(db):
    job = await jobs.create_job(db, JobType.CODER, preapproval=TESTS_YES)
    await jobs.transition(db, job.id, "running")
    await jobs.transition(db, job.id, "exception")
    with pytest.raises(IllegalTransition):
        await jobs.transition(db, job.id, "done")  # never done without a yes
    assert (await jobs.transition(db, job.id, "approved")).state is JobState.APPROVED


# --- pass check: pre-approved -> done with no question --------------------------------------


async def test_pre_approved_pr_merges_once_the_tests_pass(db, gh, fake):
    job = await pr_open(db, gh, TESTS_YES)
    assert job.state is JobState.RUNNING  # waiting for the tests, not for the driver
    assert job.result["tests"] == "pending"
    assert await coder.merge_approved(db, gh) is None  # nothing merges before the tests pass

    moved = await coder.handle_event(db, gh, "check_run", check_run("success"))
    assert moved.state is JobState.APPROVED
    done = await coder.merge_approved(db, gh)
    assert (done.state, done.summary) == (
        JobState.DONE,
        "Merged: Fix the login bug. The tests passed.",
    )
    assert fake.paths("PUT") == ["/pulls/8/merge"]
    assert await states(db, job.id) == ["queued", "running", "approved", "done"]
    assert (await jobs.events(db, job.id))[2][2] == "pre-approved: merge it if the tests pass"


async def test_pre_approval_without_a_test_condition_approves_at_once(db, gh):
    job = await pr_open(db, gh, {"condition": "merge it when it's ready"})
    assert job.state is JobState.APPROVED


# --- pass check: a broken pre-approval goes to exception --------------------------------------


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out"])
async def test_failed_tests_put_the_job_in_exception(db, gh, fake, conclusion):
    job = await pr_open(db, gh, TESTS_YES)
    moved = await coder.handle_event(db, gh, "check_run", check_run(conclusion))
    assert moved.id == job.id
    assert moved.state is JobState.EXCEPTION
    assert moved.summary == (
        "The tests failed on the pull request for Fix the login bug. Merge it anyway?"
    )
    assert await coder.merge_approved(db, gh) is None
    assert fake.paths("PUT") == []


async def test_tests_that_never_report_put_the_job_in_exception(db, gh):
    job = await pr_open(db, gh, TESTS_YES)
    later = datetime.now(UTC) + timedelta(minutes=coder.TESTS_TIMEOUT_MINUTES + 1)
    [moved] = await coder.expire_tests(db, now=later)
    assert (moved.id, moved.state) == (job.id, JobState.EXCEPTION)
    assert moved.summary.startswith("The tests never reported on the pull request")
    assert await coder.expire_tests(db, now=later) == []


async def test_other_checks_and_other_prs_are_ignored(db, gh):
    job = await pr_open(db, gh, TESTS_YES)
    assert await coder.handle_event(db, gh, "check_run", check_run(name="claude")) is None
    other = check_run(pr=99)
    other["check_run"]["head_sha"] = "0" * 40
    assert await coder.handle_event(db, gh, "check_run", other) is None
    assert (await jobs.get_job(db, job.id)).state is JobState.RUNNING


async def test_check_run_for_a_held_job_changes_nothing(db, gh):
    job = await pr_open(db, gh)  # no pre-approval: already held
    assert await coder.handle_event(db, gh, "check_run", check_run("failure")) is None
    assert (await jobs.get_job(db, job.id)).state is JobState.NEEDS_APPROVAL


# --- pass check: no pre-approval -> held for the arrival call ---------------------------------


async def test_without_preapproval_the_pr_is_held_for_a_yes(db, gh, fake):
    job = await pr_open(db, gh)
    assert job.state is JobState.NEEDS_APPROVAL
    assert job.summary == "I opened a pull request: Fix the login bug. Merge it?"
    assert fake.paths("PUT") == []


# --- the spoken answer to an exception (approve_action) ---------------------------------------


@pytest.mark.parametrize(
    ("approved", "state", "message"),
    [(True, JobState.APPROVED, "Done, going ahead."), (False, JobState.FAILED, "OK, cancelled.")],
)
async def test_spoken_answer_settles_an_exception(db, gh, client, approved, state, message):  # noqa: F811
    job = await pr_open(db, gh, TESTS_YES)
    await coder.handle_event(db, gh, "check_run", check_run("failure"))
    body = sample("approve_action", job_id=str(job.id), approved=approved)
    response, elapsed = await call(client, "approve_action", body)
    assert elapsed < 0.5
    assert response.json()["message"] == message
    assert (await jobs.get_job(db, job.id)).state is state
