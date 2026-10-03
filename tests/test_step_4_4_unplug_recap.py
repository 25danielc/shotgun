"""Step 4.4 pass check (D17): unplug cancels the arrival call and sends the ntfy push.

Offline: the real /events endpoint with a mocked ElevenLabs (`rang`) and ntfy (httpx
MockTransport, or a recorder in place of recap.push). Live: one real push to NTFY_TOPIC; Daniel
confirms it arrived on the phone.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app import calls, drives, jobs, recap
from app.config import settings
from app.jobs import JobType
from tests.helpers import EVENTS_SECRET, TESTS_YES, run, ticks


@pytest.fixture
def pushed(monkeypatch):
    sent = []

    async def fake_push(text, **kwargs):
        sent.append(text)
        return True

    monkeypatch.setattr(recap, "push", fake_push)
    return sent


async def event(http, name, location=None):
    body = {"source": "test", "event": name, "location": location}
    response = await http.post("/events", json=body, headers={"X-Shotgun-Secret": EVENTS_SECRET})
    assert response.status_code == 202
    return response.json()


# --- the pass check ---------------------------------------------------------------------------


async def test_unplug_cancels_the_arrival_call_and_pushes_the_recap(http, db, rang, pushed):
    await event(http, "carplay_connected")
    drive = await drives.current_drive(db)
    arrive = datetime.now(UTC) + timedelta(minutes=20)
    await db.execute("update drives set arrival_call_at = %s where id = %s", (arrive, drive.id))
    done = await jobs.create_job(db, JobType.RESEARCH, {"label": "Lions score"}, drive_id=drive.id)
    await run(db, done.id, "running", "done", summary="The Lions won 31 to 24.")
    held = await jobs.create_job(
        db, JobType.CODER, {"label": "Fix the login bug"}, drive_id=drive.id, preapproval=TESTS_YES
    )
    await run(
        db,
        held.id,
        "running",
        "exception",
        summary="The tests failed on the pull request for Fix the login bug. Merge it anyway?",
    )

    reply = await event(http, "carplay_disconnected")
    assert reply == {"accepted": True, "calling": False}
    assert (await drives.get_drive(db, drive.id)).ended_at is not None

    # The arrival time comes and goes: no call. The exception isn't rung about either.
    assert await ticks(db, 5, now=arrive + timedelta(minutes=1)) == [None] * 5
    assert [r["call_kind"] for r in rang] == ["departure"]

    assert pushed == [
        "Done: The Lions won 31 to 24.\n"
        "Waiting on you: The tests failed on the pull request for Fix the login bug. "
        "Merge it anyway?"
    ]


async def test_spec_name_car_disconnected_works_too(http, db, rang, pushed):
    await event(http, "carplay_connected")
    drive = await drives.current_drive(db)
    job = await jobs.create_job(
        db, JobType.CODER, {"label": "Fix the login bug"}, drive_id=drive.id
    )
    await run(db, job.id, "running")
    await event(http, "car_disconnected")
    assert await drives.current_drive(db) is None
    assert pushed == ["Still running: Fix the login bug is still in progress."]


async def test_drive_without_jobs_pushes_nothing(http, db, rang, pushed):
    await event(http, "carplay_connected")
    await event(http, "carplay_disconnected")
    assert pushed == []
    assert await drives.current_drive(db) is None


async def test_unplug_with_no_open_drive_is_harmless(http, rang, pushed):
    assert await event(http, "carplay_disconnected") == {"accepted": True, "calling": False}
    assert pushed == [] and rang == []


async def test_waiting_items_come_back_on_the_next_plug_in(http, db, rang, pushed, monkeypatch):
    await event(http, "carplay_connected")
    drive = await drives.current_drive(db)
    held = await jobs.create_job(db, JobType.CODER, {"label": "Fix A"}, drive_id=drive.id)
    await run(db, held.id, "running", "needs_approval", summary="PR for A. Merge it?")
    await event(http, "carplay_disconnected")
    assert (await event(http, "carplay_connected"))["calling"] is True  # pending item
    assert rang[-1]["pending_job_id"] == str(held.id)


# --- the recap text and the push ---------------------------------------------------------------


async def test_recap_groups_jobs_and_skips_finished_plans(db):
    drive = await drives.open_drive(db)

    async def job(job_type, *steps, summary=None, label="x"):
        made = await jobs.create_job(db, job_type, {"label": label}, drive_id=drive.id)
        return await run(db, made.id, *steps, summary=summary) if steps else made

    await job(JobType.PLAN, "running", "done", summary="On it: two things.")
    await job(JobType.CODER, "running", "failed", summary="I couldn't file the issue.")
    await job(JobType.RESEARCH, "running", "done", summary="It's 54 degrees.")
    await job(JobType.CODER, "running", "needs_approval", summary="PR for B. Merge it?")
    await job(JobType.CODER, label="Fix C")
    text = recap.recap_text(await calls.drive_jobs(db, drive.id))
    assert text == (
        "Done: It's 54 degrees.\n"
        "Waiting on you: PR for B. Merge it?\n"
        "Still running: Fix C is still in progress.\n"
        "Didn't work: I couldn't file the issue."
    )
    assert recap.recap_text([]) is None


async def test_push_posts_to_the_topic(monkeypatch):
    monkeypatch.setattr(settings, "ntfy_topic", "shotgun-test-topic-0123456789")
    seen = {}

    def handler(request):
        seen.update(url=str(request.url), body=request.content.decode(), headers=request.headers)
        return httpx.Response(200, json={"id": "abc"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await recap.push("Done: It's 54 degrees.", client=client) is True
    assert seen["url"] == "https://ntfy.sh/shotgun-test-topic-0123456789"
    assert seen["body"] == "Done: It's 54 degrees."
    assert seen["headers"]["x-title"] == "Shotgun: drive recap"


async def test_push_without_a_topic_sends_nothing(monkeypatch):
    monkeypatch.setattr(settings, "ntfy_topic", "")

    def handler(request):
        raise AssertionError("no request expected")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await recap.push("x", client=client) is False


async def test_push_failure_is_logged_not_raised(monkeypatch, caplog):
    monkeypatch.setattr(settings, "ntfy_topic", "shotgun-test-topic-0123456789")
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(429)))
    assert await recap.push("x", client=client) is False
    assert "ntfy push failed" in caplog.text


@pytest.mark.live
async def test_live_push_reaches_the_phone():
    """Sends one real push to NTFY_TOPIC. Daniel confirms it arrived."""
    assert settings.ntfy_topic, "set NTFY_TOPIC in .env"
    assert await recap.push("Done: Shotgun step 4.4 test push.") is True
