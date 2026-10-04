"""Step 4.1 pass check (D17): a drive with 2 pre-approved jobs produces exactly 2 outbound calls
(departure + arrival), and a job that breaks its pre-approval triggers at most one exception call.

Offline, with a mocked ElevenLabs client: telephony.place_call records the dynamic variables
instead of ringing, and call_in_progress reports a free line unless a test says otherwise. The
plug-in goes through the real /events endpoint and dispatches through the real /tools.
"""

import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app import calls, drives, jobs, telephony
from app.config import settings
from app.jobs import JobState, JobType
from tests.helpers import DANIEL, EVENTS_SECRET, TESTS_YES, TOOLS_SECRET, run, ticks


async def plug_in(http):
    body = {"source": "test", "event": "carplay_connected", "location": None}
    response = await http.post("/events", json=body, headers={"X-Shotgun-Secret": EVENTS_SECRET})
    assert response.status_code == 202


async def dispatch(http, label, preapproval=None, job_type="coder"):
    body = {
        "type": job_type,
        "details": f"{label}, please",
        "label": label,
        "caller": DANIEL,
        "called": "+15555550199",
    }
    if preapproval:
        body["preapproval"] = preapproval
    response = await http.post(
        "/tools/dispatch_task", json=body, headers={"X-Shotgun-Secret": TOOLS_SECRET}
    )
    return response.json()


# --- the pass check ---------------------------------------------------------------------------


async def test_drive_with_two_pre_approved_jobs_makes_exactly_two_calls(http, db, rang):
    await plug_in(http)
    assert [r["call_kind"] for r in rang] == ["departure"]
    drive = await drives.current_drive(db)

    first = await dispatch(http, "Fix the login bug", TESTS_YES)
    second = await dispatch(http, "Fix the signup typo", {"condition": "merge it"})
    assert {first["drive_id"], second["drive_id"]} == {drive.id}

    await run(db, first["job_id"], "running")
    await run(db, second["job_id"], "running", "approved")  # pre-approved: no question
    assert await ticks(db) == [None] * 5  # work in progress: nobody rings

    await run(
        db, first["job_id"], "approved", "done", summary="I merged the fix for the login bug."
    )
    assert await ticks(db) == [None] * 5  # one still going through
    await run(db, second["job_id"], "done", summary="I merged the fix for the signup typo.")
    assert await ticks(db) == ["arrival", None, None, None, None]

    assert len(rang) == 2
    assert await drives.calls_for(db, drive.id) == ["departure", "arrival"]
    arrival = rang[1]
    assert arrival["call_kind"] == "arrival"
    assert arrival["drive_id"] == str(drive.id)
    assert arrival["pending_job_id"] == ""
    assert arrival["summary"] == (
        "I merged the fix for the login bug. I merged the fix for the signup typo."
    )
    assert arrival["greeting"].startswith("Hey, quick update.")  # no ETA: no "almost there"


async def test_late_results_after_the_arrival_call_never_ring(http, db, rang):
    await plug_in(http)
    job = await dispatch(http, "Fix the login bug", TESTS_YES)
    await run(db, job["job_id"], "running", "approved", "done", summary="Merged.")
    await ticks(db)
    late = await jobs.create_job(db, JobType.RESEARCH, drive_id=job["drive_id"])
    await run(db, late.id, "running", "done", summary="Three ramen places are open.")
    await ticks(db)
    assert [r["call_kind"] for r in rang] == ["departure", "arrival"]


async def test_drive_with_no_jobs_gets_no_arrival_call(http, db, rang):
    await plug_in(http)
    assert await ticks(db, 10) == [None] * 10
    assert len(rang) == 1


# --- arrival timing ---------------------------------------------------------------------------


async def test_arrival_rings_at_eta_minus_3_even_if_work_is_running(db, rang):
    now = datetime.now(UTC)
    drive = await drives.open_drive(db, now=now)
    await db.execute(
        "update drives set arrival_call_at = %s where id = %s",
        (now + timedelta(minutes=19), drive.id),
    )
    done = await jobs.create_job(db, JobType.RESEARCH, {"label": "Lions score"}, drive_id=drive.id)
    await run(db, done.id, "running", "done", summary="The Lions won 31 to 24.")
    busy = await jobs.create_job(
        db, JobType.CODER, {"label": "Fix the login bug"}, drive_id=drive.id
    )
    await run(db, busy.id, "running")

    assert await calls.tick(db, now + timedelta(minutes=18)) is None  # too early
    assert await calls.tick(db, now + timedelta(minutes=19)) == "arrival"
    assert rang[0]["summary"] == "The Lions won 31 to 24. Fix the login bug is still in progress."


async def test_unknown_eta_waits_until_everything_is_settled(db, rang):
    drive = await drives.open_drive(db)
    plan = await jobs.create_job(db, JobType.PLAN, request="two things", drive_id=drive.id)
    await run(db, plan.id, "running")
    assert await calls.tick(db) is None  # still planning
    await run(db, plan.id, "done", summary="On it: two things.")
    research = await jobs.create_job(db, JobType.RESEARCH, drive_id=drive.id)
    held = await jobs.create_job(db, JobType.CODER, {"label": "Fix the bug"}, drive_id=drive.id)
    await run(db, research.id, "running", "done", summary="It's 54 degrees.")
    assert await calls.tick(db) is None  # the coder job hasn't been claimed yet
    await run(
        db,
        held.id,
        "running",
        "needs_approval",
        summary="I opened a pull request: Fix the bug. Merge it?",
    )
    assert await calls.tick(db) == "arrival"
    [arrival] = rang
    assert arrival["summary"] == (
        "It's 54 degrees. I opened a pull request: Fix the bug. Merge it?"
    )  # the finished plan is not read out; the question comes last
    assert arrival["pending_job_id"] == str(held.id)


async def test_arrival_batches_done_failed_then_one_question(db, rang):
    drive = await drives.open_drive(db)

    async def job_with(label, *steps, summary=None, preapproval=None):
        job = await jobs.create_job(
            db, JobType.CODER, {"label": label}, drive_id=drive.id, preapproval=preapproval
        )
        return await run(db, job.id, *steps, summary=summary)

    held = await job_with("Fix A", "running", "needs_approval", summary="PR for A. Merge it?")
    exc = await job_with(
        "Fix B", "running", "exception", summary="Tests failed on B. Merge?", preapproval=TESTS_YES
    )
    await job_with("Fix C", "running", "failed", summary="I couldn't file the issue for C.")
    await job_with("Fix D", "running", "done", summary="Fix D is done.")
    assert await calls.tick(db) == "arrival"
    [arrival] = rang
    assert arrival["summary"] == (
        "Fix D is done. I couldn't file the issue for C. PR for A. Merge it? "
        "Tests failed on B. Merge?"
    )
    assert arrival["pending_job_id"] == str(exc.id)  # an exception is asked before a hold
    assert held.id != exc.id


# --- exception calls --------------------------------------------------------------------------


async def exception_in(db, drive_id, label):
    what = label.lower().removeprefix("fix ")
    job = await jobs.create_job(
        db, JobType.CODER, {"label": label}, drive_id=drive_id, preapproval=TESTS_YES
    )
    return await run(
        db,
        job.id,
        "running",
        "exception",
        summary=f"The tests failed on the fix for {what}. Merge it anyway?",
    )


async def test_broken_preapproval_rings_one_exception_call(db, rang):
    drive = await drives.open_drive(db)
    other = await jobs.create_job(db, JobType.CODER, drive_id=drive.id)
    await run(db, other.id, "running")  # keeps the arrival call from being due
    job = await exception_in(db, drive.id, "Fix the login bug")
    later = datetime.now(UTC) + timedelta(hours=1)
    assert await ticks(db, 5) == ["exception", None, None, None, None]
    assert await ticks(db, 3, now=later) == [None] * 3  # never twice for the same job
    [call] = rang
    assert call["call_kind"] == "exception"
    assert call["pending_job_id"] == str(job.id)
    assert call["greeting"] == (
        "Hey, quick one. The tests failed on the fix for the login bug. Merge it anyway?"
    )


async def test_at_most_one_exception_call_per_10_minutes_others_wait(db, rang):
    now = datetime.now(UTC)
    drive = await drives.open_drive(db, now=now)
    other = await jobs.create_job(db, JobType.CODER, drive_id=drive.id)
    await run(db, other.id, "running")
    await exception_in(db, drive.id, "Fix A")
    second = await exception_in(db, drive.id, "Fix B")
    assert await calls.tick(db, now) == "exception"
    assert await calls.tick(db, now + timedelta(minutes=9)) is None
    # The running job finishes: the arrival call carries the second exception as its question.
    await run(db, other.id, "done", summary="Fix C is merged.")
    assert await calls.tick(db, now + timedelta(minutes=9)) == "arrival"
    assert rang[1]["pending_job_id"] == str(second.id)
    assert len(rang) == 2


async def test_no_exception_call_after_unplug(db, rang):
    drive = await drives.open_drive(db)
    await exception_in(db, drive.id, "Fix A")
    await db.execute("update drives set ended_at = now() where id = %s", (drive.id,))
    assert await ticks(db) == [None] * 5


# --- guards -----------------------------------------------------------------------------------


async def test_nothing_rings_while_the_driver_is_on_a_call(db, rang):
    drive = await drives.open_drive(db)
    job = await jobs.create_job(db, JobType.RESEARCH, drive_id=drive.id)
    await run(db, job.id, "running", "done", summary="Three ramen places are open.")
    rang.busy = True
    assert await ticks(db) == [None] * 5
    rang.busy = False
    assert await calls.tick(db) == "arrival"


async def test_refused_call_is_retried_and_nothing_is_marked(db, rang):
    drive = await drives.open_drive(db)
    job = await jobs.create_job(db, JobType.RESEARCH, drive_id=drive.id)
    await run(db, job.id, "running", "done", summary="Done.")
    rang.refuse = True
    assert await calls.tick(db) == calls.FAILED
    assert (await drives.get_drive(db, drive.id)).arrival_called is False
    rang.refuse = False
    assert await calls.tick(db) == "arrival"
    assert (await drives.get_drive(db, drive.id)).arrival_called is True


def later(minutes):
    return datetime.now(UTC) + timedelta(minutes=minutes)


async def test_job_nobody_claims_fails_and_is_reported_on_arrival(db, rang):
    drive = await drives.open_drive(db)
    job = await jobs.create_job(
        db, JobType.EMAIL, {"label": "Email Alex I'm running late"}, drive_id=drive.id
    )
    assert await calls.expire_unclaimed(db, later(1)) == []
    assert await calls.tick(db, later(calls.UNCLAIMED_MINUTES + 1)) == "arrival"
    assert (await jobs.get_job(db, job.id)).state is JobState.FAILED
    assert rang[0]["summary"] == "I can't do that one yet: email Alex I'm running late."


async def test_unclaimed_guard_falls_back_to_the_request(db):
    await jobs.create_job(db, JobType.RESEARCH, request="find ramen nearby")
    [expired] = await calls.expire_unclaimed(db, later(calls.UNCLAIMED_MINUTES + 1))
    assert expired.summary == "I can't do that one yet: find ramen nearby."


@pytest.mark.parametrize("steps", [["running"], ["running", "needs_approval"]])
async def test_claimed_jobs_are_left_alone(db, steps):
    job = await jobs.create_job(db, JobType.CODER)
    await run(db, job.id, *steps)
    assert await calls.expire_unclaimed(db, later(60)) == []


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
