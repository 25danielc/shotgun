"""Mission control derivations against the real schema, privacy, robustness and the monitor.

Every derived field (drive status, steps, approval, upcoming) follows the rules of the code
that acts on the rows: app/calls.py (arrival_due, spoken, exception_job, expire_unclaimed),
app/workers/coder.py (result.tests, timeouts) and app/voice_tools.py approve_action.
Offline: the `http` fixture runs the real app on the test database; health and live-call
checks run against httpx.MockTransport.
"""

import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app import calls, dashboard, drives, jobs
from app import db as app_db
from app.config import settings
from app.jobs import JobState
from tests.helpers import DANIEL, TESTS_YES

TOKEN = "d" * 32
HOME = "500 Maple Ridge Dr, Ann Arbor, MI 48104"


@pytest.fixture(autouse=True)
def clean_records(monkeypatch):
    monkeypatch.setattr(settings, "dashboard_token", TOKEN)
    monkeypatch.setattr(settings, "home_address", HOME)
    records = (dashboard.TOOL_CALLS, dashboard.TOOL_EVENTS, dashboard.HEALTH, dashboard.LIVE_CALL)
    for record in records:
        record.clear()
    yield
    for record in records:
        record.clear()


async def get_state(http):
    response = await http.get("/dashboard/state", params={"token": TOKEN})
    assert response.status_code == 200, response.text
    return response.json()


async def coder_job(db, drive, **result):
    job = await jobs.create_job(
        db, "coder", {"label": "Fix login"}, drive_id=drive.id, preapproval=TESTS_YES
    )
    await jobs.transition(db, job.id, JobState.RUNNING, note="claimed")
    if result:
        await jobs.update_result(db, job.id, result)
    return job


def steps_of(view):
    return {s["name"]: s["state"] for s in view["steps"]}


# ── every job state in one drive ────────────────────────────────────────────
async def test_a_drive_with_every_job_state(http, db):
    drive = await drives.open_drive(db)
    queued = await jobs.create_job(db, "email", request="tell Kim I'm late", drive_id=drive.id)
    running = await coder_job(db, drive, issue_number=3)
    held = await jobs.create_job(db, "coder", request="fix the footer", drive_id=drive.id)
    await jobs.transition(db, held.id, JobState.RUNNING)
    await jobs.update_result(db, held.id, {"issue_number": 4, "pr_number": 5})
    await jobs.transition(db, held.id, JobState.NEEDS_APPROVAL, summary="Merge it?")
    broken = await coder_job(db, drive, issue_number=6, pr_number=7, tests="timed_out")
    await jobs.transition(db, broken.id, JobState.EXCEPTION, note="pre-approval broken: no tests")
    approved = await coder_job(db, drive, issue_number=8, pr_number=9, tests="success")
    await jobs.transition(db, approved.id, JobState.APPROVED, note="pre-approved")
    said_no = await jobs.create_job(db, "coder", request="rename it", drive_id=drive.id)
    await jobs.transition(db, said_no.id, JobState.RUNNING)
    await jobs.update_result(db, said_no.id, {"issue_number": 10, "pr_number": 11})
    await jobs.transition(db, said_no.id, JobState.NEEDS_APPROVAL)
    await jobs.transition(
        db, said_no.id, JobState.FAILED, summary="Cancelled", note="driver said no"
    )
    no_pr = await coder_job(db, drive, issue_number=12)
    await jobs.transition(
        db, no_pr.id, JobState.FAILED, error="no pull request after 15 minutes", summary="Late."
    )
    research = await jobs.create_job(db, "research", request="ramen", drive_id=drive.id)
    await jobs.transition(db, research.id, JobState.RUNNING)
    await jobs.transition(db, research.id, JobState.DONE, summary="Two ramen shops downtown.")
    plan_done = await jobs.create_job(db, "plan", request="do two things", drive_id=drive.id)
    await jobs.transition(db, plan_done.id, JobState.RUNNING)
    await jobs.transition(db, plan_done.id, JobState.DONE)
    plan_failed = await jobs.create_job(db, "plan", request="mumble", drive_id=drive.id)
    await jobs.transition(db, plan_failed.id, JobState.FAILED, error="no jobs")

    state = await get_state(http)
    by_id = {j["id"]: j for j in state["jobs"]}
    # a finished plan is only the request the planner split (calls.spoken); a failed one stays
    assert plan_done.id not in by_id and plan_failed.id in by_id

    assert by_id[queued.id]["approval"]["status"] == "pending"
    assert steps_of(by_id[running.id])["action_running"] == "running"
    assert by_id[running.id]["approval"]["status"] == "preapproved"
    assert by_id[held.id]["approval"]["status"] == "held"
    assert steps_of(by_id[held.id])["tests"] == "skipped"  # no require_tests_pass
    assert steps_of(by_id[held.id])["approval_check"] == "running"

    ex = by_id[broken.id]
    assert ex["approval"] == {"status": "exception", "note": "no tests"}
    tests_step = next(s for s in ex["steps"] if s["name"] == "tests")
    assert tests_step["state"] == "failed" and tests_step["detail"] == "never reported"

    ap = by_id[approved.id]
    assert steps_of(ap)["approval_check"] == "done" and steps_of(ap)["merged"] == "running"
    assert ap["approval"]["status"] == "preapproved"

    no = by_id[said_no.id]
    assert no["approval"]["status"] == "declined"
    assert steps_of(no)["approval_check"] == "failed"
    assert steps_of(no)["merged"] == "pending"  # a "no" is not a failed merge

    late = by_id[no_pr.id]
    failed_step = next(s for s in late["steps"] if s["state"] == "failed")
    assert failed_step["name"] == "action_running"
    assert "no pull request" in failed_step["detail"]

    assert by_id[research.id]["approval"]["status"] == "not_needed"
    assert by_id[research.id]["progress"] == 1.0

    kinds = {(u["kind"], u["job_id"]) for u in state["upcoming"]}
    assert ("exception_call", broken.id) in kinds  # unannounced, no exception call yet: now
    assert ("held_action", held.id) in kinds
    assert ("preapproved_action", approved.id) in kinds  # merging now
    assert ("job_expiry", queued.id) in kinds  # no email worker running
    assert ("job_expiry", running.id) in kinds  # no PR by PR_TIMEOUT_MINUTES
    exception = next(u for u in state["upcoming"] if u["kind"] == "exception_call")
    assert exception["at"] == state["server"]["now"]
    arrival = next(u for u in state["upcoming"] if u["kind"] == "arrival_call")
    spoken = sum(1 for j in await calls.drive_jobs(db, drive.id) if calls.spoken(j))
    assert f"{spoken} jobs" in arrival["description"]


async def test_exception_calls_follow_the_rate_limit_and_stop_after_arrival(http, db):
    drive = await drives.open_drive(db)
    job = await coder_job(db, drive, issue_number=1, pr_number=2, tests="failure")
    await jobs.transition(db, job.id, JobState.EXCEPTION, note="pre-approval broken: failed")
    last = datetime.now(UTC) - timedelta(minutes=4)
    await drives.record_call(db, "exception", drive_id=None, conversation_id="c0", now=last)
    up = [u for u in (await get_state(http))["upcoming"] if u["kind"] == "exception_call"]
    expected = last + timedelta(seconds=calls.EXCEPTION_GAP_SECONDS)
    assert up and abs(datetime.fromisoformat(up[0]["at"]) - expected) < timedelta(seconds=2)

    await db.execute("update drives set arrival_called = true where id = %s", (drive.id,))
    state = await get_state(http)
    assert not [u for u in state["upcoming"] if u["kind"] in ("exception_call", "arrival_call")]


async def test_held_after_the_arrival_call_waits_for_the_next_plug_in(http, db):
    drive = await drives.open_drive(db)
    job = await jobs.create_job(db, "coder", request="fix it", drive_id=drive.id)
    await jobs.transition(db, job.id, JobState.RUNNING)
    await jobs.transition(db, job.id, JobState.NEEDS_APPROVAL)
    await db.execute("update drives set arrival_called = true where id = %s", (drive.id,))
    held = [u for u in (await get_state(http))["upcoming"] if u["kind"] == "held_action"]
    assert held[0]["at"] is None and "next plug-in" in held[0]["description"]


async def test_arrival_with_no_eta_is_due_once_everything_settled(http, db):
    drive = await drives.open_drive(db)
    job = await jobs.create_job(db, "research", request="ramen", drive_id=drive.id)

    def arrival(state):
        return next(u for u in state["upcoming"] if u["kind"] == "arrival_call")

    assert arrival(await get_state(http))["at"] is None  # waits for the job
    await jobs.transition(db, job.id, JobState.RUNNING)
    await jobs.transition(db, job.id, JobState.DONE, summary="Found two.")
    done = [await jobs.get_job(db, job.id)]
    assert calls.arrival_due(await drives.current_drive(db), done, datetime.now(UTC))
    state = await get_state(http)
    assert arrival(state)["at"] == state["server"]["now"]  # rings on the next tick


async def test_a_drive_with_no_jobs_gets_no_arrival_call(http, db):
    await drives.open_drive(db)
    assert (await get_state(http))["upcoming"] == []


# ── drive and call status ───────────────────────────────────────────────────
async def test_a_call_just_placed_shows_as_ringing(http, db):
    drive = await drives.open_drive(db, source="ios_shortcut")
    state = await get_state(http)
    assert state["drive"]["status"] == "plugged_in"
    assert state["events"][0]["message"].startswith("CarPlay connected")
    await drives.record_call(db, "departure", drive_id=drive.id, conversation_id="conv_d")
    state = await get_state(http)
    assert state["drive"]["status"] == "on_call"
    assert state["call"]["status"] == "ringing" and state["call"]["kind"] == "departure"
    # the monitor picks it up: kind comes from the calls row
    dashboard.LIVE_CALL.update(
        conversation_id="conv_d",
        status="active",
        started_at=datetime.now(UTC) - timedelta(seconds=20),
        ended_at=None,
        duration_s=None,
    )
    state = await get_state(http)
    assert state["call"]["status"] == "active" and state["call"]["kind"] == "departure"
    assert state["call"]["duration_s"] >= 19


async def test_an_old_call_row_is_not_ringing(http, db):
    drive = await drives.open_drive(db)
    old = datetime.now(UTC) - timedelta(minutes=2)
    await drives.record_call(db, "departure", drive_id=drive.id, conversation_id="c", now=old)
    state = await get_state(http)
    assert state["drive"]["status"] == "silent" and state["call"] is None


async def test_drive_opened_by_a_call(http, db):
    await drives.current_or_open(db)
    assert "call with no plug-in" in (await get_state(http))["events"][0]["message"]


# ── privacy ─────────────────────────────────────────────────────────────────
SECRETS = {
    "anthropic_api_key": "sk-ant-SECRETKEY0000",
    "elevenlabs_api_key": "el-SECRETKEY0001",
    "twilio_auth_token": "tw-SECRETKEY0002",
    "github_token": "github_pat_SECRETKEY0003",
    "google_maps_api_key": "AIzaSECRETKEY0004",
    "ntfy_topic": "shotgun-0123456789abcdef01234567",
    "tools_shared_secret": "t" * 32,
    "events_shared_secret": "e" * 32,
    "my_phone_number": "+17345550100",
    "allowed_caller_number": DANIEL,
}


async def test_no_secret_phone_email_home_or_coordinates_in_the_payload(http, db, monkeypatch):
    for name, value in SECRETS.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(settings, "dashboard_token", TOKEN)
    drive = await drives.open_drive(db, lat=42.29671, lng=-83.72113)
    await db.execute(
        "update drives set destination = %s where id = %s", ("500 Maple Ridge Drive", drive.id)
    )
    job = await jobs.create_job(
        db,
        "email",
        {"to": "kim@umich.edu", "label": "Email Kim at kim@umich.edu"},
        request="email kim@umich.edu, call me at (734) 555-0100, I'm at 500 Maple Ridge Dr",
        drive_id=drive.id,
    )
    await jobs.transition(db, job.id, JobState.RUNNING)
    await jobs.transition(
        db, job.id, JobState.FAILED, summary="Text +1 734 555 0100", error="to kim@umich.edu"
    )
    dashboard.record_tool_call(
        "set_destination", json.dumps({"destination": HOME}).encode(), 400, 200
    )
    dashboard.record_tool_call(
        "draft_message", json.dumps({"to": "+17345550100", "intent": "hi"}).encode(), 900, 200
    )
    raw = json.dumps(await get_state(http))
    for value in SECRETS.values():
        assert value not in raw
    for leak in ("umich.edu", "555-0100", "555 0100", "5550100", "Maple Ridge", "42.29", "83.72"):
        assert leak not in raw, leak
    assert "start_lat" not in raw and "start_lng" not in raw
    assert "[home]" in raw and "[email]" in raw and "[phone]" in raw


def test_scrub_keeps_dates_numbers_and_issue_ids():
    text = "PR #13 merged 2026-10-03 at 18:42, 3 of 4 tests, $31.40, 42.2967 deg, issue 12345"
    assert dashboard.scrub(text) == text
    assert dashboard.scrub("call 734-555-0100") == "call [phone]"
    assert dashboard.scrub("call +44 20 7946 0958 now") == "call [phone] now"
    assert dashboard.scrub("I'm at 500 maple ridge drive.") == "I'm at [home]."


async def test_state_never_calls_a_third_party(http, db, monkeypatch):
    await coder_job(db, await drives.open_drive(db), issue_number=1)

    async def no_network(self, request, *args, **kwargs):
        raise AssertionError(f"state handler called {request.url.host}")

    # every real HTTP request goes through this transport (the test client uses ASGITransport)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_network)
    await get_state(http)


# ── robustness ──────────────────────────────────────────────────────────────
async def test_no_pool_or_a_broken_pool_still_answers(monkeypatch):
    monkeypatch.setattr(app_db, "get_pool", lambda: None)
    state = await dashboard.build_state()
    assert state["drive"] is None and state["jobs"] == [] and len(state["services"]) == 8

    class Broken:
        @asynccontextmanager
        async def connection(self, timeout=None):
            raise OSError("neon down")
            yield

    monkeypatch.setattr(app_db, "get_pool", lambda: Broken())
    state = await dashboard.build_state()
    assert state["drive"] is None and state["events"] == []


async def test_long_text_and_bad_results_do_not_break_the_payload(http, db):
    drive = await drives.open_drive(db)
    job = await jobs.create_job(db, "coder", request="fix " + "very " * 2000, drive_id=drive.id)
    await jobs.transition(db, job.id, JobState.RUNNING)
    await jobs.update_result(
        db, job.id, {"issue_number": 1, "pr_number": 2, "pr_opened_at": "yesterday-ish"}
    )
    await jobs.update_result(db, job.id, {"tests": "pending", "pr_url": "javascript:alert(1)"})
    state = await get_state(http)
    view = state["jobs"][0]
    assert len(view["title"]) <= 60 and view["title"].endswith("…")
    assert view["result_url"] is None  # only https links reach the page
    assert all(len(e["message"]) <= dashboard.MAX_TEXT for e in state["events"])


async def test_parked_drive_shows_no_upcoming(http, db):
    drive = await drives.open_drive(db)
    await jobs.create_job(db, "research", request="ramen", drive_id=drive.id)
    await drives.close_drive(db)
    state = await get_state(http)
    assert state["drive"]["status"] == "parked" and state["upcoming"] == []
    assert state["events"][-1]["message"] == f"unplugged: drive #{drive.id} closed"


async def test_polling_is_a_few_queries(http, db, monkeypatch):
    drive = await drives.open_drive(db)
    for i in range(20):
        await jobs.create_job(db, "research", request=f"lookup {i}", drive_id=drive.id)
    executed = []
    real = type(db).cursor

    def counting_cursor(self, *args, **kwargs):
        cur = real(self, *args, **kwargs)
        execute = cur.execute

        async def counted(query, *a, **k):
            executed.append(query)
            return await execute(query, *a, **k)

        cur.execute = counted
        return cur

    monkeypatch.setattr(type(db), "cursor", counting_cursor)
    await get_state(http)
    assert len(executed) <= 5, executed


# ── monitor: health probes and the live call ────────────────────────────────
async def test_health_probes_are_read_only_and_never_touch_the_ntfy_topic(monkeypatch):
    for name, value in SECRETS.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(settings, "twilio_account_sid", "AC123")
    monkeypatch.setattr(settings, "github_demo_repo", "o/r")
    monkeypatch.setattr(app_db, "get_pool", lambda: None)
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"healthy": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await dashboard.check_health(client)
    assert seen and all(r.method == "GET" for r in seen)
    assert not any(settings.ntfy_topic in str(r.url) for r in seen)
    assert not any("routes.googleapis.com" in r.url.host for r in seen)
    ntfy = [r for r in seen if r.url.host == "ntfy.sh"]
    assert [r.url.path for r in ntfy] == ["/v1/health"]
    health = dashboard.HEALTH
    assert health["google_routes"]["ok"] is None
    assert health["neon"]["ok"] is False and health["anthropic"]["ok"] is True
    for entry in health.values():
        assert not any(v in (entry["detail"] or "") for v in SECRETS.values())


def conversations(*items):
    def handler(request):
        return httpx.Response(200, json={"conversations": list(items)})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_live_call_monitor(monkeypatch):
    now = datetime.now(UTC).timestamp()
    # /tools/init marked it live a moment ago; the list doesn't show it yet: still live
    dashboard.LIVE_CALL.update(
        conversation_id="c1", status="active", started_at=datetime.now(UTC), ended_at=None
    )
    async with conversations() as client:
        await dashboard.check_live_call(client)
    assert dashboard.LIVE_CALL["status"] == "active"
    # the list says initiated: an already-connected call doesn't step back to ringing
    item = {"conversation_id": "c1", "status": "initiated", "start_time_unix_secs": now - 5}
    async with conversations(item) as client:
        await dashboard.check_live_call(client)
    assert dashboard.LIVE_CALL["status"] == "active"
    # done, with a float duration: ended, and the event is written
    done = {**item, "status": "done", "call_duration_secs": 75.6}
    async with conversations(done) as client:
        await dashboard.check_live_call(client)
    assert dashboard.LIVE_CALL["status"] == "ended" and dashboard.LIVE_CALL["duration_s"] == 75
    assert dashboard.TOOL_EVENTS[-1]["message"] == "call ended after 01:15"
