"""Step 4.5 pass check: make demo-call, demo-arrive and watch each work against a local server
with mocked telephony (the `http` and `rang` fixtures in conftest.py)."""

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app import calls, drives, jobs
from app.config import settings
from app.jobs import JobType
from tests.helpers import TESTS_YES, run

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("demo", ROOT / "scripts" / "demo.py")
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)


# --- make demo-call ---------------------------------------------------------------------------


async def test_demo_call_simulates_the_plug_in(http, db, rang, monkeypatch):
    monkeypatch.setattr(settings, "call_policy", "always")
    reply = await demo.demo_call("http://test", client=http)
    assert reply == {"accepted": True, "calling": True}
    drive = await drives.current_drive(db)
    assert drive.source == "demo"
    assert [r["call_kind"] for r in rang] == ["departure"]
    assert await drives.calls_for(db, drive.id) == ["departure"]


# --- make demo-arrive -------------------------------------------------------------------------


async def test_demo_arrive_makes_the_arrival_call_ring_now(http, db, rang):
    await demo.demo_call("http://test", client=http)
    drive = await drives.current_drive(db)
    job = await jobs.create_job(
        db, JobType.CODER, {"label": "Fix the login bug"}, drive_id=drive.id, preapproval=TESTS_YES
    )
    await run(db, job.id, "running")  # still waiting for its tests
    assert await calls.tick(db) is None  # not settled, no ETA: no arrival call yet

    _, message = await demo.demo_arrive(db)
    assert message == f"drive {drive.id}: arrival call due now with 1 job(s)"
    assert await calls.tick(db) == "arrival"
    assert rang[-1]["summary"] == "Fix the login bug is still in progress."
    _, again = await demo.demo_arrive(db)
    assert again == f"drive {drive.id} already had its arrival call"


async def test_demo_arrive_explains_when_nothing_will_ring(http, db, rang):
    assert (await demo.demo_arrive(db))[1] == "no open drive: plug in first (make demo-call)"
    await demo.demo_call("http://test", client=http)
    drive = await drives.current_drive(db)
    assert (await demo.demo_arrive(db))[1] == (
        f"drive {drive.id} has no jobs, so no arrival call (D17)"
    )
    assert await calls.tick(db) is None


# --- make watch -------------------------------------------------------------------------------


async def test_watch_shows_the_drive_jobs_and_calls(http, db, rang):
    await demo.demo_call("http://test", client=http)
    drive = await drives.current_drive(db)
    now = datetime.now(UTC)
    await db.execute(
        "update drives set destination = 'Home', eta = %s, arrival_call_at = %s where id = %s",
        (now + timedelta(minutes=22), now + timedelta(minutes=19), drive.id),
    )
    coder = await jobs.create_job(
        db, JobType.CODER, {"label": "Fix the login bug"}, drive_id=drive.id, preapproval=TESTS_YES
    )
    await run(db, coder.id, "running")
    research = await jobs.create_job(
        db, JobType.RESEARCH, {"label": "Lions score"}, drive_id=drive.id
    )
    await run(db, research.id, "running", "done", summary="The Lions won 31 to 24.")

    screen = demo.render(*await demo.snapshot(db), now=now)
    lines = screen.splitlines()
    assert lines[0].startswith(f"Drive {drive.id}  open  started ")
    assert lines[1].startswith("  Home · ETA ") and "(1140 s)" in lines[1]
    assert f"▶ #{coder.id:<4} coder    running        Fix the login bug [pre-approved]" in screen
    assert f"● #{research.id:<4} research done           Lions score" in screen
    assert "      The Lions won 31 to 24." in screen
    assert lines[-1].startswith("Calls: departure ")


async def test_watch_falls_back_to_the_last_drive_and_handles_none(db):
    assert demo.render(*await demo.snapshot(db), now=datetime.now(UTC)) == (
        "No drives yet. Plug in (make demo-call)."
    )
    drive = await drives.open_drive(db)
    await drives.close_drive(db)
    screen = demo.render(*await demo.snapshot(db), now=datetime.now(UTC))
    assert screen.startswith(f"Drive {drive.id}  closed ")
    assert "  (none)" in screen and "Calls: none" in screen
