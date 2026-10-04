"""Mission control: GET /dashboard/state matches docs/dashboard_state.schema.md and is built from
real rows (drives, calls, jobs, job_events) plus the in-process records in app/dashboard.py.

Offline: the `http` fixture runs the real app against the test database; ElevenLabs is mocked
by `rang`, and the health/live-call monitor is not started.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app import dashboard, drives, jobs
from app.config import settings
from app.jobs import JobState
from tests.helpers import DANIEL, TESTS_YES, TOOLS_SECRET

TOKEN = "d" * 32
FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "dashboard_state.json").read_text())


@pytest.fixture(autouse=True)
def clean_records(monkeypatch):
    monkeypatch.setattr(settings, "dashboard_token", TOKEN)
    monkeypatch.setattr(settings, "demo_mode", False)
    for record in (
        dashboard.TOOL_CALLS,
        dashboard.TOOL_EVENTS,
        dashboard.HEALTH,
        dashboard.LIVE_CALL,
    ):
        record.clear()
    yield
    for record in (
        dashboard.TOOL_CALLS,
        dashboard.TOOL_EVENTS,
        dashboard.HEALTH,
        dashboard.LIVE_CALL,
    ):
        record.clear()


async def get_state(http):
    response = await http.get("/dashboard/state", params={"token": TOKEN})
    assert response.status_code == 200, response.text
    return response.json()


async def coder_mid_pipeline(db):
    """A drive with a pre-approved coder job whose PR is open and waiting for its tests."""
    drive = await drives.open_drive(db)
    job = await jobs.create_job(
        db,
        "coder",
        {"label": "Fix the login redirect loop"},
        request="fix the login redirect loop",
        drive_id=drive.id,
        preapproval=TESTS_YES,
    )
    await jobs.transition(db, job.id, JobState.RUNNING)
    await jobs.update_result(
        db,
        job.id,
        {
            "issue_number": 12,
            "issue_url": "https://github.com/o/r/issues/12",
            "pr_number": 13,
            "pr_url": "https://github.com/o/r/pull/13",
            "pr_opened_at": datetime.now(UTC).isoformat(),
            "tests": "pending",
        },
    )
    return drive, job


# ── auth ────────────────────────────────────────────────────────────────────
async def test_token_required(http, monkeypatch):
    assert (await http.get("/dashboard/state")).status_code == 401
    assert (await http.get("/dashboard/state", params={"token": "nope"})).status_code == 401
    monkeypatch.setattr(settings, "dashboard_token", "")
    assert (await http.get("/dashboard/state", params={"token": TOKEN})).status_code == 503


async def test_token_in_a_header_keeps_it_out_of_access_logs(http):
    """The page sends X-Dashboard-Token (opened as /dashboard#token=...), so no URL carries it."""
    ok = await http.get("/dashboard/state", headers={"X-Dashboard-Token": TOKEN})
    assert ok.status_code == 200
    bad = await http.get("/dashboard/state", headers={"X-Dashboard-Token": "nope"})
    assert bad.status_code == 401
    page = (await http.get("/dashboard")).text
    assert "'X-Dashboard-Token': TOKEN" in page
    assert "/dashboard/state?token=" not in page


async def test_unbuilt_worker_is_not_reported_as_offline(http, monkeypatch):
    # Every worker exists since email (3.2); a stand-in keeps the dim "not built" path covered.
    monkeypatch.setitem(dashboard.AGENT_TASKS, "email", None)
    state = (await http.get("/dashboard/state", headers={"X-Dashboard-Token": TOKEN})).json()
    email = next(a for a in state["agents"] if a["name"] == "email")
    assert (email["status"], email["last_heartbeat"]) == ("not_built", None)


async def test_page_is_served(http):
    response = await http.get("/dashboard")
    assert response.status_code == 200
    assert "mission control" in response.text


# ── contract shape ──────────────────────────────────────────────────────────
def same_keys(actual: dict, expected: dict, where: str, optional=()):
    missing = set(expected) - set(actual) - set(optional)
    extra = set(actual) - set(expected)
    assert not missing and not extra, f"{where}: missing {missing}, extra {extra}"


async def test_state_matches_the_fixture_shape(http, db):
    await coder_mid_pipeline(db)
    dashboard.record_tool_call("get_status", b"{}", 138, 200)
    dashboard.LIVE_CALL.update(
        conversation_id="conv_1",
        status="active",
        started_at=datetime.now(UTC) - timedelta(seconds=30),
        ended_at=None,
        duration_s=None,
    )
    state = await get_state(http)
    same_keys(state, FIXTURE, "top level")
    same_keys(state["server"], FIXTURE["server"], "server")
    same_keys(state["drive"], FIXTURE["drive"], "drive")
    same_keys(state["call"], FIXTURE["call"], "call", optional=("transcript",))
    same_keys(state["jobs"][0], FIXTURE["jobs"][0], "job")
    same_keys(state["jobs"][0]["steps"][0], FIXTURE["jobs"][0]["steps"][0], "step")
    same_keys(state["upcoming"][0], FIXTURE["upcoming"][0], "upcoming")
    same_keys(state["tool_calls"][0], FIXTURE["tool_calls"][0], "tool call")
    same_keys(state["events"][0], FIXTURE["events"][0], "event")
    same_keys(state["services"][0], FIXTURE["services"][0], "service")
    same_keys(state["agents"][0], FIXTURE["agents"][0], "agent")
    assert [s["name"] for s in state["services"]] == [s["name"] for s in FIXTURE["services"]]
    assert [a["name"] for a in state["agents"]] == [a["name"] for a in FIXTURE["agents"]]


async def test_no_drive_still_renders(http):
    state = await get_state(http)
    assert state["drive"] is None and state["call"] is None
    assert state["jobs"] == [] and state["upcoming"] == []
    assert state["services"][0]["name"] == "api_server" and state["services"][0]["ok"] is True
    assert all(s["ok"] is None for s in state["services"][1:])  # never checked yet


# ── derived from real rows ──────────────────────────────────────────────────
async def test_coder_job_steps_approval_and_upcoming(http, db):
    drive, job = await coder_mid_pipeline(db)
    state = await get_state(http)

    assert state["drive"]["id"] == drive.id
    assert state["drive"]["status"] == "plugged_in"  # no call yet, just plugged in
    view = state["jobs"][0]
    assert view["id"] == job.id and view["state"] == "running"
    assert [(s["name"], s["state"]) for s in view["steps"]] == [
        ("issue_filed", "done"),
        ("action_running", "done"),
        ("pr_opened", "done"),
        ("tests", "running"),
        ("approval_check", "pending"),
        ("merged", "pending"),
    ]
    assert view["progress"] == 0.5
    assert view["approval"] == {"status": "preapproved", "note": None}
    assert view["preapproval"]["require_tests_pass"] is True
    assert view["result_url"] == "https://github.com/o/r/pull/13"

    kinds = [u["kind"] for u in state["upcoming"]]
    assert "arrival_call" in kinds  # no ETA yet: "once every job is done or held"
    assert "preapproved_action" in kinds
    assert "job_expiry" in kinds  # tests timeout
    timed = [u["at"] for u in state["upcoming"] if u["at"]]
    assert timed == sorted(timed)
    assert state["upcoming"][-1]["at"] is None  # untimed items last

    types = [e["type"] for e in state["events"]]
    assert {"plug_in", "job_dispatched", "job_state"} <= set(types)
    ids = [e["id"] for e in state["events"]]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)


async def test_merged_job_is_done_and_exception_carries_its_reason(http, db):
    drive, job = await coder_mid_pipeline(db)
    await jobs.update_result(db, job.id, {"tests": "success"})
    await jobs.transition(db, job.id, JobState.APPROVED, note="pre-approved")
    await jobs.transition(db, job.id, JobState.DONE, summary="Merged.", note="merged")
    other = await jobs.create_job(
        db, "coder", request="fix the footer", drive_id=drive.id, preapproval=TESTS_YES
    )
    await jobs.transition(db, other.id, JobState.RUNNING)
    await jobs.update_result(
        db, other.id, {"issue_number": 14, "pr_number": 15, "tests": "failure"}
    )
    await jobs.transition(
        db, other.id, JobState.EXCEPTION, note="pre-approval broken: the tests failed"
    )

    by_id = {j["id"]: j for j in (await get_state(http))["jobs"]}
    done = by_id[job.id]
    assert done["progress"] == 1.0
    assert all(s["state"] == "done" for s in done["steps"])
    assert done["result_summary"] == "Merged."
    broken = by_id[other.id]
    assert broken["approval"] == {"status": "exception", "note": "the tests failed"}
    assert dict((s["name"], s["state"]) for s in broken["steps"])["tests"] == "failed"


async def test_held_job_waits_for_the_arrival_call(http, db):
    drive = await drives.open_drive(db)
    eta = datetime.now(UTC) + timedelta(minutes=15)
    await db.execute(
        "update drives set eta = %s, arrival_call_at = %s where id = %s",
        (eta, eta - timedelta(minutes=3), drive.id),
    )
    job = await jobs.create_job(db, "email", request="tell Prof. Kim I'm late", drive_id=drive.id)
    await jobs.transition(db, job.id, JobState.RUNNING)
    await jobs.transition(db, job.id, JobState.NEEDS_APPROVAL, note="held for a yes")

    state = await get_state(http)
    assert state["jobs"][0]["approval"]["status"] == "held"
    held = [u for u in state["upcoming"] if u["kind"] == "held_action"]
    assert held and held[0]["at"] == state["drive"]["arrival_call_at"]
    assert state["upcoming"][0]["kind"] == "arrival_call"


async def test_parked_drive_and_old_drives(http, db):
    drive = await drives.open_drive(db)
    await drives.close_drive(db)
    state = await get_state(http)
    assert state["drive"]["status"] == "parked"
    assert state["events"][-1]["type"] == "unplug"
    await db.execute(
        "update drives set ended_at = now() - interval '2 hours' where id = %s", (drive.id,)
    )
    assert (await get_state(http))["drive"] is None


# ── tool recorder ───────────────────────────────────────────────────────────
async def test_tool_calls_are_recorded_but_refused_callers_are_not(http, db):
    await drives.open_drive(db)
    body = {"caller": DANIEL, "called": "+15555550199", "conversation_id": "c1"}
    ok = await http.post("/tools/get_status", json=body, headers={"X-Shotgun-Secret": TOOLS_SECRET})
    assert ok.status_code == 200
    refused = await http.post("/tools/get_status", json=body, headers={"X-Shotgun-Secret": "x"})
    assert refused.status_code == 401

    state = await get_state(http)
    assert len(state["tool_calls"]) == 1
    call = state["tool_calls"][0]
    assert call["tool"] == "get_status" and call["ok"] is True and call["latency_ms"] >= 0
    assert any(e["type"] == "tool_called" for e in state["events"])


def test_queries_are_scrubbed():
    dashboard.record_tool_call(
        "draft_message",
        json.dumps({"to": "kim@umich.edu", "intent": "call me at +1 (734) 555-0100"}).encode(),
        700,
        200,
    )
    query = dashboard.TOOL_CALLS[0]["query"]
    assert "umich" not in query and "555" not in query
    assert "[email]" in query and "[phone]" in query


def test_init_marks_a_call_live(monkeypatch):
    monkeypatch.setattr(settings, "allowed_caller_number", DANIEL)
    body = json.dumps({"conversation_id": "conv_9", "caller_id": DANIEL}).encode()
    dashboard.record_tool_call("init", body, 40, 200)
    assert dashboard.call_live()
    assert not dashboard.TOOL_CALLS  # /tools/init is the call starting, not a tool


def test_init_from_a_stranger_is_not_a_call(monkeypatch):
    """/tools/init answers a stranger 200 (caller_allowed "no"): not shown as the drive's call."""
    monkeypatch.setattr(settings, "allowed_caller_number", DANIEL)
    stranger = json.dumps({"conversation_id": "conv_x", "caller_id": "+15555550111"}).encode()
    dashboard.record_tool_call("init", stranger, 40, 200)
    dashboard.record_tool_call("init", json.dumps({"caller_id": DANIEL}).encode(), 40, 503)
    assert not dashboard.call_live() and not dashboard.TOOL_EVENTS


# ── demo endpoints ──────────────────────────────────────────────────────────
async def test_demo_routes_404_unless_demo_mode(http):
    response = await http.post("/dashboard/demo/arrive", params={"token": TOKEN})
    assert response.status_code == 404


async def test_demo_arrive_and_plug_in(http, db, rang, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "call_policy", "always")
    assert (await http.post("/dashboard/demo/arrive", params={"token": "x"})).status_code == 401
    assert (await http.post("/dashboard/demo/arrive", params={"token": TOKEN})).status_code == 409

    response = await http.post("/dashboard/demo/plug-in", params={"token": TOKEN})
    assert response.status_code == 202
    assert len(rang) == 1  # the real departure path rang (mocked ElevenLabs)
    drive = await drives.current_drive(db)
    assert drive is not None and drive.source == "dashboard_demo"

    # calls.arrival_due: a drive with no jobs gets no arrival call, so there is nothing to do
    response = await http.post("/dashboard/demo/arrive", params={"token": TOKEN})
    assert response.status_code == 409
    await jobs.create_job(db, "research", request="ramen nearby", drive_id=drive.id)
    response = await http.post("/dashboard/demo/arrive", params={"token": TOKEN})
    assert response.status_code == 202
    drive = await drives.current_drive(db)
    assert drive.arrival_call_at is not None
    state = await get_state(http)
    assert any("(demo)" in e["message"] for e in state["events"])


async def test_state_carries_the_page_build_so_old_tabs_reload(http):
    """Daniel 21:50: an open tab kept showing the pre-deploy page."""
    state = (await http.get("/dashboard/state", headers={"X-Dashboard-Token": TOKEN})).json()
    assert state["server"]["build"] == dashboard.BUILD and len(dashboard.BUILD) == 12
    page = (await http.get("/dashboard")).text
    assert "else if (build !== pageBuild) { location.reload(); return; }" in page
