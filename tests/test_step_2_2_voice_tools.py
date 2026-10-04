"""Step 2.2 pass check, background tools: sample payloads answer in under 500 ms and write a
row with its drive and pre-approval (D17). The inline tools are tests/test_step_2_2_inline_tools.py.

The sample bodies in tests/fixtures/elevenlabs/ go through the real app (ASGI, same event loop)
against Postgres: embedded by default, Neon with `make test-neon`. Each request is timed.
The literal curl check against a running server is scripts/curl_tools.sh.
"""

import json
import os
import time
from pathlib import Path

import httpx
import pytest

from app import drives, jobs, voice_tools
from app.config import settings
from app.jobs import JobState
from app.main import app

FIXTURES = Path(__file__).parent / "fixtures" / "elevenlabs"
SECRET = "t" * 32
DANIEL = "+15555550100"
BUDGET = 0.5


def sample(name: str, **changes) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text()) | changes


@pytest.fixture
async def client(db, monkeypatch):
    monkeypatch.setattr(settings, "tools_shared_secret", SECRET)
    monkeypatch.setattr(settings, "allowed_caller_number", DANIEL)

    async def test_conn():
        yield db

    app.dependency_overrides[voice_tools.get_conn] = test_conn
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


async def call(client, tool, body, secret=SECRET):
    headers = {"X-Shotgun-Secret": secret} if secret is not None else {}
    start = time.perf_counter()
    response = await client.post(f"/tools/{tool}", json=body, headers=headers)
    return response, time.perf_counter() - start


async def needs_approval_job(db, job_type="email"):
    job = await jobs.create_job(db, job_type, request="email Alex")
    await jobs.transition(db, job.id, "running")
    await jobs.transition(db, job.id, "needs_approval", summary="Email to Alex. Send it?")
    return job


# --- the pass check -----------------------------------------------------------------------


async def test_dispatch_task_sample_is_fast_and_writes_a_plan_row(client, db):
    response, elapsed = await call(client, "dispatch_task", sample("dispatch_task"))
    assert response.status_code == 200
    assert elapsed < BUDGET
    reply = response.json()
    assert reply["ok"] is True
    job = await jobs.get_job(db, reply["job_id"])
    assert job.type == "plan"
    assert job.state is JobState.QUEUED
    assert job.request.startswith("Order my usual ramen")
    assert job.details == {"conversation_id": "conv_sample_inbound"}


async def test_typed_dispatch_writes_a_worker_row_with_drive_and_preapproval(client, db):
    response, elapsed = await call(client, "dispatch_task", sample("dispatch_task_typed"))
    assert response.status_code == 200
    assert elapsed < BUDGET
    reply = response.json()
    assert reply["message"] == "On it. Pre-approved: merge it if the tests pass"
    job = await jobs.get_job(db, reply["job_id"])
    assert job.type == "coder"
    assert job.request == "Fix the login bug Sarah filed."
    assert job.details["label"] == "Fix Sarah's login bug"
    assert job.preapproval == {
        "condition": "merge it if the tests pass",
        "require_tests_pass": True,
    }
    assert job.drive_id == reply["drive_id"]
    assert (await drives.get_drive(db, job.drive_id)).ended_at is None


async def test_dispatch_without_preapproval_stores_none(client, db):
    body = sample("dispatch_task_typed")
    del body["preapproval"]
    job = await jobs.get_job(db, (await call(client, "dispatch_task", body))[0].json()["job_id"])
    assert job.preapproval is None


async def test_dispatch_joins_the_open_drive(client, db):
    drive = await drives.open_drive(db, lat=42.28, lng=-83.74)
    reply = (await call(client, "dispatch_task", sample("dispatch_task_typed")))[0].json()
    assert reply["drive_id"] == drive.id
    again = (await call(client, "dispatch_task", sample("dispatch_task")))[0].json()
    assert again["drive_id"] == drive.id


async def test_dispatch_with_no_drive_opens_one(client, db):
    reply = (await call(client, "dispatch_task", sample("dispatch_task_typed")))[0].json()
    drive = await drives.get_drive(db, reply["drive_id"])
    assert drive.source == "call"
    assert (await drives.current_drive(db)).id == drive.id


async def test_old_request_field_still_dispatches(client, db):
    """Agents pushed before D17 send `request`, not `details`."""
    body = {
        "request": "Fix the login bug.",
        "type": "coder",
        "repo": "demo app",
        **sample("get_status"),
    }
    job = await jobs.get_job(db, (await call(client, "dispatch_task", body))[0].json()["job_id"])
    assert (job.type, job.request) == ("coder", "Fix the login bug.")


async def test_get_status_for_a_drive_includes_finished_jobs(client, db):
    drive = await drives.open_drive(db)
    other = await jobs.create_job(db, "research", request="elsewhere")
    done = await jobs.create_job(db, "research", request="score", drive_id=drive.id)
    await jobs.transition(db, done.id, "running")
    await jobs.transition(db, done.id, "done", summary="Michigan won.")
    coder = await jobs.create_job(db, "coder", request="fix", drive_id=drive.id)
    response, elapsed = await call(client, "get_status", sample("get_status", drive_id=drive.id))
    assert elapsed < BUDGET
    reply = response.json()
    assert [j["id"] for j in reply["jobs"]] == [done.id, coder.id]
    assert other.id not in [j["id"] for j in reply["jobs"]]
    assert reply["message"] == "The research is done and the code fix is queued."


async def test_get_status_sample_is_fast_and_speakable(client, db):
    await jobs.create_job(db, "plan", request="something")
    await needs_approval_job(db)
    response, elapsed = await call(client, "get_status", sample("get_status"))
    assert response.status_code == 200
    assert elapsed < BUDGET
    reply = response.json()
    assert reply["message"] == "I'm still planning your request and the email needs your OK."
    assert [j["state"] for j in reply["jobs"]] == ["queued", "needs_approval"]


async def test_approve_action_sample_approves_and_logs(client, db):
    job = await needs_approval_job(db)
    response, elapsed = await call(
        client, "approve_action", sample("approve_action", job_id=str(job.id))
    )
    assert response.status_code == 200
    assert elapsed < BUDGET
    assert response.json() == {
        "ok": True,
        "message": "Done, going ahead.",
        "job_id": job.id,
        "drive_id": None,
        "jobs": None,
    }
    assert (await jobs.get_job(db, job.id)).state is JobState.APPROVED
    assert (await jobs.events(db, job.id))[-1] == ("needs_approval", "approved", "driver said yes")


# --- behaviour ----------------------------------------------------------------------------


async def test_spoken_no_cancels(client, db):
    job = await needs_approval_job(db)
    body = sample("approve_action", job_id=job.id, approved=False)
    reply = (await call(client, "approve_action", body))[0].json()
    assert reply["message"] == "OK, cancelled."
    job = await jobs.get_job(db, job.id)
    assert (job.state, job.summary) == (JobState.FAILED, "Cancelled")


@pytest.mark.parametrize("approved", [True, False])
async def test_approve_only_acts_on_jobs_waiting_for_approval(client, db, approved):
    job = await jobs.create_job(db, "food")  # queued, never asked
    body = sample("approve_action", job_id=job.id, approved=approved)
    reply = (await call(client, "approve_action", body))[0].json()
    assert reply["ok"] is False
    assert (await jobs.get_job(db, job.id)).state is JobState.QUEUED


@pytest.mark.parametrize("job_id", ["999999999", "the ramen one"])
async def test_approve_unknown_job(client, job_id):
    reply = (await call(client, "approve_action", sample("approve_action", job_id=job_id)))[
        0
    ].json()
    assert reply == {
        "ok": False,
        "message": "I couldn't find that job.",
        "job_id": reply["job_id"],
        "drive_id": None,
        "jobs": None,
    }


async def test_unknown_type_falls_back_to_plan(client, db):
    body = sample("dispatch_task_typed", type="groceries")
    job = await jobs.get_job(db, (await call(client, "dispatch_task", body))[0].json()["job_id"])
    assert job.type == "plan"


async def test_plan_is_not_a_valid_explicit_worker_type_but_still_stored(client, db):
    body = sample("dispatch_task_typed", type="PLAN")
    job = await jobs.get_job(db, (await call(client, "dispatch_task", body))[0].json()["job_id"])
    assert job.type == "plan"


async def test_blank_request_writes_nothing(client, db):
    reply = (await call(client, "dispatch_task", sample("dispatch_task", request="  ")))[0].json()
    assert reply["ok"] is False
    assert await jobs.list_jobs(db) == []


async def test_status_sentence_for_none_and_three_jobs(db):
    assert voice_tools.status_sentence([]) == "Nothing is in progress right now."
    coder = await jobs.create_job(db, "coder")
    await jobs.transition(db, coder.id, "running")
    await needs_approval_job(db)
    await jobs.create_job(db, "research")
    assert voice_tools.status_sentence(await jobs.list_jobs(db)) == (
        "The code fix is in progress, the email needs your OK, and the research is queued."
    )


# --- auth -----------------------------------------------------------------------------------


@pytest.mark.parametrize("secret", ["wrong", None])
@pytest.mark.parametrize(
    "tool", ["dispatch_task", "get_status", "approve_action", "search_web", "draft_message"]
)
async def test_bad_secret_is_401_and_writes_nothing(client, db, tool, secret):
    response, _ = await call(client, tool, sample(tool), secret=secret)
    assert response.status_code == 401
    assert await jobs.list_jobs(db) == []


async def test_bad_secret_with_garbage_body_is_still_401(client):
    response = await client.post(
        "/tools/dispatch_task", content=b"{}", headers={"X-Shotgun-Secret": "x"}
    )
    assert response.status_code == 401


async def test_stranger_is_403_and_writes_nothing(client, db):
    body = sample("dispatch_task", caller="+12025550123", called="+15555550199")
    response, _ = await call(client, "dispatch_task", body)
    assert response.status_code == 403
    assert await jobs.list_jobs(db) == []


async def test_missing_caller_is_403(client):
    body = {"request": "fix the bug"}
    assert (await call(client, "dispatch_task", body))[0].status_code == 403


@pytest.mark.parametrize("tool", ["search_web", "draft_message"])
async def test_inline_tools_refuse_strangers(client, tool):
    body = sample(tool, caller="+12025550123", called="+15555550199")
    assert (await call(client, tool, body))[0].status_code == 403


async def test_caller_number_formatting_is_normalised(client):
    body = sample("dispatch_task", caller="+1 (555) 555-0100")
    assert (await call(client, "dispatch_task", body))[0].status_code == 200


async def test_unconfigured_allowlist_fails_closed(client, monkeypatch):
    monkeypatch.setattr(settings, "allowed_caller_number", "")
    assert (await call(client, "dispatch_task", sample("dispatch_task")))[0].status_code == 503


async def test_no_database_is_503(monkeypatch):
    monkeypatch.setattr(settings, "tools_shared_secret", SECRET)
    monkeypatch.setattr(voice_tools.db, "get_pool", lambda: None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response, _ = await call(client, "get_status", sample("get_status"))
    assert response.status_code == 503


@pytest.mark.live
def test_live_curl_samples_against_deployed_server():
    """Runs scripts/curl_tools.sh against BASE_URL / PUBLIC_BASE_URL; it exits non-zero on any
    slow or failed request."""
    import subprocess

    base = os.environ.get("BASE_URL") or settings.public_base_url
    result = subprocess.run(
        ["bash", "scripts/curl_tools.sh", base], capture_output=True, text=True, check=False
    )
    print(result.stdout, result.stderr)
    assert result.returncode == 0


# --- drives -------------------------------------------------------------------------------------


async def test_opening_a_drive_closes_the_open_one(db):
    first = await drives.open_drive(db, lat=42.28, lng=-83.74)
    second = await drives.open_drive(db)
    assert (await drives.get_drive(db, first.id)).ended_at == second.started_at
    assert (await drives.current_drive(db)).id == second.id
    assert (first.start_lat, first.start_lng, first.arrival_called) == (42.28, -83.74, False)


async def test_a_drive_older_than_max_hours_is_not_current(db):
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    old = await drives.open_drive(db, now=now - timedelta(hours=drives.MAX_HOURS, minutes=1))
    assert await drives.current_drive(db, now) is None
    fresh = await drives.current_or_open(db, now=now)
    assert fresh.id != old.id


# --- code fixes must name the connected repo (Daniel 21:30) -------------------------------------


@pytest.mark.parametrize(
    "said",
    ["my demo app", "the demo workflow repo", "shotgun demo", "shotgun-demo-app", "Demo App"],
)
async def test_naming_the_connected_repo_dispatches(client, db, said):
    body = sample("dispatch_task_typed", repo=said)
    reply = (await call(client, "dispatch_task", body))[0].json()
    assert reply["ok"] is True
    job = await jobs.get_job(db, reply["job_id"])
    assert job.details["repo"] == settings.github_demo_repo


@pytest.mark.parametrize(
    ("said", "message"),
    [
        (None, "Which repo is that in? I'm only connected to your shotgun demo app."),
        ("", "Which repo is that in? I'm only connected to your shotgun demo app."),
        (
            "my lab's Cheetah repo",
            "I'm not connected to my lab's Cheetah repo. I can only work on your shotgun demo app.",
        ),
        (
            "the shotgun repo",
            "I'm not connected to the shotgun repo. I can only work on your shotgun demo app.",
        ),
    ],
)
async def test_a_code_fix_without_the_connected_repo_is_refused(client, db, said, message):
    body = sample("dispatch_task_typed")
    body.pop("repo")
    if said is not None:
        body["repo"] = said
    response, elapsed = await call(client, "dispatch_task", body)
    assert elapsed < BUDGET
    assert response.json()["ok"] is False
    assert response.json()["message"] == message
    assert await jobs.list_jobs(db) == []


async def test_other_job_types_need_no_repo(client, db):
    body = sample("dispatch_task_curl")  # research
    assert (await call(client, "dispatch_task", body))[0].json()["ok"] is True
