"""Step 4.3 pass check: a unit test per departure-call rule passes, plus the CALL_POLICY=always
override (D17). Then the same rules end to end through POST /events with a mocked ElevenLabs.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app import drives, jobs, policy
from app.config import settings
from app.jobs import JobType
from tests.helpers import EVENTS_SECRET, run

NOW = datetime(2026, 10, 3, 18, 30, tzinfo=UTC)
RECENT = NOW - timedelta(minutes=5)


def decide(pending=0, eta_minutes=None, last_call_at=RECENT):
    return policy.departure(
        now=NOW, pending=pending, eta_minutes=eta_minutes, last_call_at=last_call_at
    )


# --- one test per rule ------------------------------------------------------------------------


def test_pending_items_ring():
    decision = decide(pending=2)
    assert decision == policy.Decision(True, "2 item(s) waiting on the driver")


@pytest.mark.parametrize(("eta", "call"), [(10, True), (31, True), (9, False), (None, False)])
def test_a_known_drive_of_10_min_or_more_rings(eta, call):
    assert decide(eta_minutes=eta).call is call


@pytest.mark.parametrize(
    ("last", "call"),
    [
        (None, True),  # never called
        (NOW - timedelta(minutes=30), True),
        (NOW - timedelta(minutes=29), False),
        (RECENT, False),
    ],
)
def test_no_call_in_the_last_30_min_rings(last, call):
    assert decide(last_call_at=last).call is call


def test_otherwise_silent():
    decision = decide()
    assert decision.call is False
    assert decision.reason == "nothing pending, short or unknown drive, called recently"


def test_call_policy_always_overrides(monkeypatch):
    monkeypatch.setattr(settings, "call_policy", "always")
    assert decide() == policy.Decision(True, "CALL_POLICY=always")


def test_thresholds_are_config_values(monkeypatch):
    monkeypatch.setattr(settings, "departure_min_drive_minutes", 20)
    monkeypatch.setattr(settings, "departure_quiet_minutes", 4)
    assert decide(eta_minutes=15).call is True  # recent call was 5 min ago, quiet is 4
    monkeypatch.setattr(settings, "departure_quiet_minutes", 60)
    assert decide(eta_minutes=15).call is False
    assert decide(eta_minutes=20).call is True


# --- end to end through /events ---------------------------------------------------------------


async def plug_in(http):
    body = {
        "source": "test",
        "event": "carplay_connected",
        "location": {"lat": 42.28, "lng": -83.74},
    }
    response = await http.post("/events", json=body, headers={"X-Shotgun-Secret": EVENTS_SECRET})
    assert response.status_code == 202
    return response.json()["calling"]


async def test_first_plug_in_rings_a_quick_replug_stays_silent(http, db, rang):
    assert await plug_in(http) is True
    first = await drives.current_drive(db)
    assert (first.start_lat, first.start_lng) == (42.28, -83.74)
    assert rang[0]["greeting"] == "Shotgun here. Where are we headed?"
    assert rang[0]["call_kind"] == "departure"
    assert rang[0]["drive_id"] == str(first.id)

    assert await plug_in(http) is False  # called a moment ago, nothing pending
    assert len(rang) == 1
    second = await drives.current_drive(db)
    assert second.id != first.id  # the drive is opened anyway
    assert (await drives.get_drive(db, first.id)).ended_at is not None


async def test_waiting_items_ring_and_lead_the_greeting(http, db, rang):
    await plug_in(http)
    held = await jobs.create_job(db, JobType.CODER, {"label": "Fix the login bug"})
    await run(
        db,
        held.id,
        "running",
        "needs_approval",
        summary="The fix for the login bug is ready as a pull request. Want me to merge it?",
    )
    assert await plug_in(http) is True  # a quick replug, but something is waiting
    call = rang[1]
    assert call["greeting"] == (
        "Shotgun here. The fix for the login bug is ready as a pull request. Want me to merge it?"
    )
    assert call["pending_job_id"] == str(held.id)


async def test_always_rings_on_every_plug_in_for_rehearsals(http, rang, monkeypatch):
    monkeypatch.setattr(settings, "call_policy", "always")
    assert [await plug_in(http) for _ in range(3)] == [True, True, True]
    assert len(rang) == 3
