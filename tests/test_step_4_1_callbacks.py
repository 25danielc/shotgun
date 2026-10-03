"""Step 4.1: the callback watcher rings the car with a summary.

Pass check (live, Daniel confirms): job flipped to done by hand -> phone rings and reads the
summary (`uv run python -m app.callbacks --demo "..."`). Offline: what gets announced, when, and
with which dynamic variables, with a fake telephony layer and a fake clock.
"""

import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app import callbacks, jobs, telephony, voice_tools
from app.config import settings
from app.jobs import JobState, JobType


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Placed(list):
    busy: list[bool]


@pytest.fixture
def calls(monkeypatch):
    placed = Placed()

    async def fake_place_call(variables, **kwargs):
        placed.append(variables)
        return telephony.PlacedCall(conversation_id=f"c{len(placed)}", call_sid="CA")

    async def line_free():
        return busy[0]

    busy = [False]
    monkeypatch.setattr(telephony, "place_call", fake_place_call)
    monkeypatch.setattr(telephony, "call_in_progress", line_free)
    placed.busy = busy
    return placed


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def watcher(clock):
    return callbacks.Watcher(clock=clock)


async def job_in(db, state, job_type=JobType.CODER, summary=None, request="fix the login bug"):
    job = await jobs.create_job(db, job_type, request=request)
    path = {
        JobState.QUEUED: [],
        JobState.RUNNING: ["running"],
        JobState.DONE: ["running", "done"],
        JobState.NEEDS_APPROVAL: ["running", "needs_approval"],
        JobState.APPROVED: ["running", "needs_approval", "approved"],
        JobState.FAILED: ["running", "failed"],
    }[state]
    for i, step in enumerate(path):
        last = i == len(path) - 1
        job = await jobs.transition(db, job.id, step, summary=summary if last else None)
    return job


# --- the pass check, offline ------------------------------------------------------------------


async def test_job_flipped_to_done_rings_with_its_summary(db, watcher, calls):
    job = await job_in(db, JobState.DONE, summary="I opened a pull request for Sarah's login bug.")
    assert [j.id for j in await watcher.tick(db)] == [job.id]
    assert calls == [
        {
            "greeting": "Shotgun here. I opened a pull request for Sarah's login bug.",
            "summary": "I opened a pull request for Sarah's login bug.",
            "eta_minutes": "",
            "pending_job_id": "",
            "drive_id": "",
            "call_kind": "",
            "caller_allowed": "yes",
        }
    ]


async def test_each_state_is_announced_once(db, watcher, clock, calls):
    await job_in(db, JobState.DONE, summary="Done.")
    await watcher.tick(db)
    clock.now += 600
    assert await watcher.tick(db) == []
    assert len(calls) == 1


# --- what gets announced -------------------------------------------------------------------------


async def test_question_carries_pending_job_id(db, watcher, calls):
    job = await job_in(db, JobState.NEEDS_APPROVAL, summary="Ramen is $21.40 with tip. Confirm?")
    await watcher.tick(db)
    assert calls[0]["pending_job_id"] == str(job.id)
    assert calls[0]["greeting"] == "Shotgun here. Ramen is $21.40 with tip. Confirm?"


@pytest.mark.parametrize("state", [JobState.QUEUED, JobState.RUNNING, JobState.APPROVED])
async def test_in_flight_states_are_not_announced(db, watcher, calls, state):
    await job_in(db, state)
    assert await watcher.tick(db) == []
    assert calls == []


async def test_finished_plan_is_silent_but_failed_plan_rings(db, watcher, clock, calls):
    await job_in(db, JobState.DONE, job_type=JobType.PLAN, summary="On it: three things.")
    assert await watcher.tick(db) == []
    await job_in(
        db, JobState.FAILED, job_type=JobType.PLAN, summary="I couldn't work out that request."
    )
    await watcher.tick(db)
    assert calls[0]["greeting"] == "Shotgun here. I couldn't work out that request."


async def test_ready_jobs_are_batched_with_the_question_last(db, watcher, calls):
    question = await job_in(db, JobState.NEEDS_APPROVAL, summary="Send the email to Alex?")
    await job_in(db, JobState.DONE, summary="The research is ready.")
    await job_in(db, JobState.FAILED, summary="The food order didn't go through.")
    batch = await watcher.tick(db)
    assert [j.state for j in batch][-1] is JobState.NEEDS_APPROVAL
    assert len(calls) == 1
    assert calls[0]["greeting"] == (
        "Shotgun here. The research is ready. The food order didn't go through. "
        "Send the email to Alex?"
    )
    assert calls[0]["pending_job_id"] == str(question.id)


async def test_one_question_at_a_time(db, watcher, clock, calls):
    first = await job_in(db, JobState.NEEDS_APPROVAL, summary="First?")
    await job_in(db, JobState.NEEDS_APPROVAL, summary="Second?")
    await watcher.tick(db)
    clock.now += 600
    assert await watcher.tick(db) == []  # first still unanswered
    await jobs.transition(db, first.id, "approved")
    await watcher.tick(db)
    assert [c["greeting"] for c in calls] == ["Shotgun here. First?", "Shotgun here. Second?"]


async def test_missing_summary_falls_back_to_label(db, watcher, calls):
    job = await jobs.create_job(db, JobType.RESEARCH, {"label": "Ramen near home"}, request="x")
    await jobs.transition(db, job.id, "running")
    await jobs.transition(db, job.id, "done")
    await watcher.tick(db)
    assert calls[0]["greeting"] == "Shotgun here. Ramen near home is done."


async def test_done_after_approval_is_announced_again(db, watcher, clock, calls):
    job = await job_in(db, JobState.NEEDS_APPROVAL, summary="Merge it?")
    await watcher.tick(db)
    await jobs.transition(db, job.id, "approved")
    await jobs.transition(db, job.id, "done", summary="Merged.")
    clock.now += 600
    await watcher.tick(db)
    assert [c["greeting"] for c in calls] == ["Shotgun here. Merge it?", "Shotgun here. Merged."]


async def test_spoken_no_does_not_ring_back(db, watcher, clock, calls, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "allowed_caller_number", "+15555550100")
    job = await job_in(db, JobState.NEEDS_APPROVAL, summary="Order the ramen?")
    await watcher.tick(db)
    body = voice_tools.ApproveBody(job_id=job.id, approved=False, caller="+15555550100")
    reply = await voice_tools.approve_action(body, db)
    assert reply.message == "OK, cancelled."
    clock.now += 600
    assert await watcher.tick(db) == []
    assert len(calls) == 1


# --- timing and failures -----------------------------------------------------------------


async def test_collision_guard_spaces_out_calls(db, watcher, clock, calls):
    await job_in(db, JobState.DONE, summary="One.")
    await watcher.tick(db)
    await job_in(db, JobState.DONE, summary="Two.")
    clock.now += callbacks.MIN_GAP_SECONDS - 1
    assert await watcher.tick(db) == []
    clock.now += 1
    await watcher.tick(db)
    assert [c["greeting"] for c in calls] == ["Shotgun here. One.", "Shotgun here. Two."]


async def test_failed_call_is_retried_after_the_gap(db, watcher, clock, calls, monkeypatch):
    real_fake = telephony.place_call

    async def failing(variables, **kwargs):
        raise telephony.CallError("ElevenLabs down")

    await job_in(db, JobState.DONE, summary="Done.")
    monkeypatch.setattr(telephony, "place_call", failing)
    assert await watcher.tick(db) == []
    monkeypatch.setattr(telephony, "place_call", real_fake)
    clock.now += callbacks.MIN_GAP_SECONDS
    assert len(await watcher.tick(db)) == 1
    assert calls[0]["greeting"] == "Shotgun here. Done."


async def test_job_that_moved_on_during_the_call_is_announced_again(db, watcher, clock, calls):
    job = await job_in(db, JobState.NEEDS_APPROVAL, summary="Merge it?")
    batch = await callbacks.pick_batch(db)
    await jobs.transition(db, job.id, "approved")  # answered while we were dialling
    await callbacks.mark_announced(db, batch)
    assert (await jobs.get_job(db, job.id)).state is JobState.APPROVED
    cur = await db.execute("select announced_state from jobs where id = %s", (job.id,))
    assert (await cur.fetchone())[0] is None


# --- unclaimed guard: no request ends in silence ----------------------------------------------


def later(minutes):
    return datetime.now(UTC) + timedelta(minutes=minutes)


async def test_job_nobody_claims_fails_and_rings(db, watcher, calls):
    job = await jobs.create_job(
        db, JobType.EMAIL, {"label": "Email Alex I'm running late"}, request="email Alex"
    )
    assert await callbacks.expire_unclaimed(db, later(1)) == []
    [expired] = await callbacks.expire_unclaimed(db, later(callbacks.UNCLAIMED_MINUTES + 1))
    assert (expired.id, expired.state) == (job.id, JobState.FAILED)
    assert expired.summary == "Sorry, I can't handle this one yet: Email Alex I'm running late."
    await watcher.tick(db)
    assert calls[0]["greeting"] == (
        "Shotgun here. Sorry, I can't handle this one yet: Email Alex I'm running late."
    )


async def test_unclaimed_guard_falls_back_to_the_request(db):
    await jobs.create_job(db, JobType.RESEARCH, request="find ramen nearby")
    [expired] = await callbacks.expire_unclaimed(db, later(callbacks.UNCLAIMED_MINUTES + 1))
    assert expired.summary == "Sorry, I can't handle this one yet: find ramen nearby."


@pytest.mark.parametrize("state", [JobState.RUNNING, JobState.NEEDS_APPROVAL, JobState.DONE])
async def test_claimed_jobs_are_left_alone(db, state):
    await job_in(db, state)
    assert await callbacks.expire_unclaimed(db, later(60)) == []


# --- busy-line guard --------------------------------------------------------------------------


async def test_no_callback_while_the_driver_is_on_a_call(db, watcher, clock, calls):
    await job_in(db, JobState.DONE, summary="Three ramen places are open.")
    calls.busy[0] = True
    assert await watcher.tick(db) == []
    assert calls == []
    calls.busy[0] = False  # hung up; no full gap needed, nothing was placed
    assert len(await watcher.tick(db)) == 1


def conversations(*rows):
    def handler(request):
        assert request.url.params["agent_id"] == settings.elevenlabs_agent_id
        return httpx.Response(200, json={"conversations": list(rows)})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    ("rows", "busy"),
    [
        ([], False),
        ([{"status": "done", "start_time_unix_secs": time.time() - 30}], False),
        ([{"status": "processing", "start_time_unix_secs": time.time() - 30}], False),
        ([{"status": "in-progress", "start_time_unix_secs": time.time() - 30}], True),
        ([{"status": "initiated", "start_time_unix_secs": time.time() - 2}], True),
        ([{"status": "in-progress", "start_time_unix_secs": time.time() - 3600}], False),
    ],
)
async def test_call_in_progress_reads_conversation_status(rows, busy):
    assert await telephony.call_in_progress(client=conversations(*rows)) is busy


async def test_call_in_progress_fails_open():
    down = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert await telephony.call_in_progress(client=down) is False
