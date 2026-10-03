"""Step 2.1 pass check: create job, legal transitions pass, illegal ones raise.

Pure state-machine tests cover all 36 (from, to) pairs. DB tests run the same rules through
Postgres: embedded by default, Neon with `make test-neon`.
"""

from datetime import UTC, datetime, timedelta
from itertools import product

import psycopg
import pytest

from app import jobs
from app.jobs import IllegalTransition, JobNotFound, JobState, JobType

LEGAL = {
    ("queued", "running"),
    ("queued", "failed"),
    ("running", "needs_approval"),
    ("running", "done"),
    ("running", "failed"),
    ("needs_approval", "approved"),
    ("needs_approval", "failed"),
    ("approved", "done"),
    ("approved", "failed"),
}
ALL_PAIRS = list(product([s.value for s in JobState], repeat=2))


# --- pure state machine -------------------------------------------------------------------


@pytest.mark.parametrize(("current", "new"), sorted(LEGAL))
def test_legal_transitions_pass(current, new):
    jobs.check_transition(current, new)


@pytest.mark.parametrize(("current", "new"), [p for p in ALL_PAIRS if p not in LEGAL])
def test_illegal_transitions_raise(current, new):
    with pytest.raises(IllegalTransition):
        jobs.check_transition(current, new)


def test_cannot_skip_spoken_approval():
    with pytest.raises(IllegalTransition):
        jobs.check_transition(JobState.NEEDS_APPROVAL, JobState.DONE)


def test_terminal_states():
    assert jobs.TERMINAL == {JobState.DONE, JobState.FAILED}


# --- through Postgres ---------------------------------------------------------------------


async def test_create_job(db):
    deadline = datetime.now(UTC) + timedelta(minutes=31)
    job = await jobs.create_job(
        db, "coder", {"issue": "login bug"}, request="fix the login bug", deadline=deadline
    )
    assert job.id > 0
    assert job.type is JobType.CODER
    assert job.state is JobState.QUEUED
    assert job.details == {"issue": "login bug"}
    assert job.deadline == deadline
    assert await jobs.get_job(db, job.id) == job
    assert await jobs.events(db, job.id) == [(None, "queued", "created")]


async def test_approval_path_through_db(db):
    job = await jobs.create_job(db, JobType.EMAIL, {"to": "alex@example.com"})
    for state in ["running", "needs_approval", "approved", "done"]:
        job = await jobs.transition(db, job.id, state, note=f"to {state}")
        assert job.state == state
    assert [to for _, to, _ in await jobs.events(db, job.id)] == [
        "queued",
        "running",
        "needs_approval",
        "approved",
        "done",
    ]


async def test_read_only_job_goes_straight_to_done(db):
    job = await jobs.create_job(db, "research")
    await jobs.transition(db, job.id, "running")
    job = await jobs.transition(
        db, job.id, "done", summary="Three ramen spots are open.", result={"places": 3}
    )
    assert job.summary == "Three ramen spots are open."
    assert job.result == {"places": 3}


async def test_spoken_no_fails_the_job(db):
    job = await jobs.create_job(db, "food")
    await jobs.transition(db, job.id, "running")
    await jobs.transition(db, job.id, "needs_approval", summary="Ramen is $21.40. Confirm?")
    job = await jobs.transition(db, job.id, "failed", summary="Cancelled", note="driver said no")
    assert job.state is JobState.FAILED
    assert job.summary == "Cancelled"
    assert (await jobs.events(db, job.id))[-1] == ("needs_approval", "failed", "driver said no")


@pytest.mark.parametrize(
    ("path", "bad"),
    [
        ([], "done"),
        ([], "approved"),
        (["running", "needs_approval"], "done"),
        (["running", "done"], "running"),
        (["failed"], "queued"),
    ],
)
async def test_illegal_transition_raises_and_leaves_row_alone(db, path, bad):
    job = await jobs.create_job(db, "coder")
    for state in path:
        await jobs.transition(db, job.id, state)
    before = await jobs.get_job(db, job.id)
    with pytest.raises(IllegalTransition):
        await jobs.transition(db, job.id, bad)
    assert await jobs.get_job(db, job.id) == before
    assert len(await jobs.events(db, job.id)) == len(path) + 1


async def test_unknown_job_raises(db):
    with pytest.raises(JobNotFound):
        await jobs.transition(db, 999_999_999, "running")


async def test_unknown_type_rejected(db):
    with pytest.raises(ValueError):
        await jobs.create_job(db, "nessie")


async def test_db_rejects_bad_state_written_directly(db):
    job = await jobs.create_job(db, "coder")
    with pytest.raises(psycopg.errors.CheckViolation):
        async with db.transaction():
            await db.execute("update jobs set state = 'paid' where id = %s", (job.id,))


async def test_claim_next_takes_most_urgent_matching_job(db):
    soon = datetime.now(UTC) + timedelta(minutes=5)
    later = await jobs.create_job(db, "food")
    urgent = await jobs.create_job(db, "food", deadline=soon)
    await jobs.create_job(db, "coder")
    claimed = await jobs.claim_next(db, ["food"])
    assert claimed.id == urgent.id
    assert claimed.state is JobState.RUNNING
    assert (await jobs.claim_next(db, ["food"])).id == later.id
    assert await jobs.claim_next(db, ["food", "research"]) is None


async def test_list_jobs_defaults_to_open(db):
    open_job = await jobs.create_job(db, "coder")
    closed = await jobs.create_job(db, "coder")
    await jobs.transition(db, closed.id, "failed")
    ids = [job.id for job in await jobs.list_jobs(db)]
    assert open_job.id in ids
    assert closed.id not in ids


async def test_init_schema_is_idempotent_and_accepts_plan_jobs(db):
    await jobs.init_schema(db)  # second run in the same database: constraints re-applied
    job = await jobs.create_job(db, JobType.PLAN, request="fix the bug and email Alex")
    assert job.type is JobType.PLAN
    assert JobType.PLAN not in jobs.WORKER_TYPES


async def test_expect_guards_the_current_state(db):
    job = await jobs.create_job(db, "email")
    with pytest.raises(IllegalTransition):
        await jobs.transition(db, job.id, "failed", expect=JobState.NEEDS_APPROVAL)
    assert (await jobs.get_job(db, job.id)).state is JobState.QUEUED
