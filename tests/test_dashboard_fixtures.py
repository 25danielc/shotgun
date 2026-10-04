"""The page's mock fixtures are real backend output, not hand-written guesses.

tests/fixtures/dashboard_state.json (mid-drive) and dashboard_state_empty.json (just plugged
in) are what GET /dashboard/state returns for a scripted drive: rows inserted with fixed ids and
times, tool calls through record_tool_call, the call through the live-call monitor (ElevenLabs
mocked) and health from check_health (mocked HTTP). If the backend's output changes, this test
fails; regenerate with

    REGEN_DASHBOARD_FIXTURES=1 uv run pytest tests/test_dashboard_fixtures.py

and look at the diff (and the screenshots, tests/dashboard/screenshot.py).
"""

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from psycopg.types.json import Jsonb

from app import dashboard
from app import db as app_db
from app.config import settings
from tests.helpers import TESTS_YES

FIXTURES = Path(__file__).parent / "fixtures"
# Fixed ids (drive 17, jobs 41 and 42) would collide with real rows in Neon.
pytestmark = pytest.mark.skipif(
    os.environ.get("USE_NEON") == "1", reason="inserts fixed ids: embedded Postgres only"
)
T0 = datetime(2026, 10, 3, 22, 29, 10, tzinfo=UTC)  # plug-in


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


@pytest.fixture(autouse=True)
def clean_records(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "allowed_caller_number", "+15555550100")
    records = (dashboard.TOOL_CALLS, dashboard.TOOL_EVENTS, dashboard.HEALTH, dashboard.LIVE_CALL)
    for record in records:
        record.clear()
    yield
    for record in records:
        record.clear()


@pytest.fixture
async def workers():
    """The worker loops app/main.py starts, by task name (planner, coder, research)."""
    tasks = [
        asyncio.create_task(asyncio.sleep(3600), name=n) for n in ("planner", "coder", "research")
    ]
    yield
    for task in tasks:
        task.cancel()


class Pool:
    def __init__(self, conn):
        self.conn = conn

    def connection(self, timeout=None):
        conn = self.conn

        class Ctx:
            async def __aenter__(self):
                return conn

            async def __aexit__(self, *exc):
                return False

        return Ctx()


async def insert(db, table: str, row: dict) -> None:
    cols = ", ".join(row)
    marks = ", ".join(["%s"] * len(row))
    values = [Jsonb(v) if isinstance(v, dict) else v for v in row.values()]
    await db.execute(
        f"insert into {table} ({cols}) overriding system value values ({marks})",  # noqa: S608
        values,
    )


async def event(db, job_id: int, from_state, to_state, note, seconds) -> None:
    await db.execute(
        "insert into job_events (job_id, from_state, to_state, note, at)"
        " values (%s, %s, %s, %s, %s)",
        (job_id, from_state, to_state, note, at(seconds)),
    )


async def health(monkeypatch, latencies: dict[str, int], checked: dict[str, float]) -> None:
    """Run the real check_health against mocked services, then pin latency and time."""
    for name in ("elevenlabs_api_key", "anthropic_api_key", "github_token", "google_maps_api_key"):
        monkeypatch.setattr(settings, name, "k")
    monkeypatch.setattr(settings, "twilio_account_sid", "AC1")
    monkeypatch.setattr(settings, "twilio_auth_token", "t")
    monkeypatch.setattr(settings, "github_demo_repo", "o/r")
    monkeypatch.setattr(settings, "ntfy_topic", "secret-topic")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))
    ) as client:
        await dashboard.check_health(client)
    for name, entry in dashboard.HEALTH.items():
        if name in latencies:
            entry["latency_ms"] = latencies[name]
        entry["checked_at"] = at(checked.get(name, 0))


def tool(name: str, body: dict, latency: int, seconds: float, monkeypatch) -> None:
    """record_tool_call as the middleware does it, at a fixed time."""
    monkeypatch.setattr(dashboard, "_now", lambda: at(seconds) + timedelta(milliseconds=latency))
    dashboard.record_tool_call(name, json.dumps(body).encode(), latency, 200)


def compare(state: dict, name: str) -> None:
    state["server"]["uptime_s"] = 11520 if name == "dashboard_state.json" else 11182
    state["server"]["build"] = "test"  # the page's fingerprint changes with every page edit
    state["services"][0]["latency_ms"] = 3
    path = FIXTURES / name
    text = json.dumps(state, indent=2, ensure_ascii=False) + "\n"
    if os.environ.get("REGEN_DASHBOARD_FIXTURES") == "1":
        path.write_text(text)
    assert json.loads(path.read_text()) == state, f"{name} is stale: regenerate it (see docstring)"


async def test_mid_drive_fixture_is_backend_output(db, monkeypatch, workers):
    monkeypatch.setattr(app_db, "get_pool", lambda: Pool(db))
    # the scripted drive is in the past: don't let the monitor's stale-call guard drop it
    monkeypatch.setattr(dashboard.telephony, "STALE_CALL_SECONDS", 10**10)
    await insert(
        db,
        "drives",
        {
            "id": 17,
            "source": "ios_shortcut",
            "started_at": at(0),
            "start_lat": 42.2967,
            "start_lng": -83.7211,
            "destination": "North Campus",
            "eta": at(950),
            "arrival_call_at": at(770),
        },
    )
    await insert(
        db,
        "calls",
        {"drive_id": 17, "kind": "departure", "conversation_id": "conv_17", "at": at(1)},
    )
    # research #41: dispatched, claimed, done
    research = {"conversation_id": "conv_17", "label": "Quiet study spot on North Campus"}
    await insert(
        db,
        "jobs",
        {
            "id": 41,
            "type": "research",
            "state": "done",
            "request": "find a quiet study spot on North Campus open past 10",
            "details": research,
            "summary": "The Duderstadt Center is open 24 hours; floors 3 and 4 are the quiet ones.",
            "result": {"answer": "...", "sources": []},
            "source": "voice",
            "created_at": at(48.2),
            "updated_at": at(71),
            "drive_id": 17,
        },
    )
    await event(db, 41, None, "queued", "created", 48.2)
    await event(db, 41, "queued", "running", "claimed", 49)
    await event(db, 41, "running", "done", None, 71)
    # coder #42: pre-approved "merge it if the tests pass", PR open, tests running
    coder = {"conversation_id": "conv_17", "label": "Fix the login redirect loop on /settings"}
    await insert(
        db,
        "jobs",
        {
            "id": 42,
            "type": "coder",
            "state": "running",
            "request": "fix the login redirect loop on settings",
            "details": coder,
            "result": {
                "issue_number": 12,
                "issue_url": "https://github.com/25danielc/shotgun-demo-app/issues/12",
                "pr_number": 13,
                "pr_url": "https://github.com/25danielc/shotgun-demo-app/pull/13",
                "head_sha": "abc123",
                "pr_opened_at": at(270).isoformat(),
                "tests": "pending",
            },
            "source": "voice",
            "created_at": at(100.2),
            "updated_at": at(101),
            "drive_id": 17,
            "preapproval": TESTS_YES,
        },
    )
    await event(db, 42, None, "queued", "created", 100.2)
    await event(db, 42, "queued", "running", "claimed", 101)

    tool("set_destination", {"destination": "North Campus", "drive_id": "17"}, 412, 21, monkeypatch)
    tool("search_web", {"query": "is the Bonisteel lot open on Saturday"}, 3712, 31, monkeypatch)
    tool(
        "dispatch_task",
        {"type": "research", "details": "find a quiet study spot on North Campus open past 10"},
        171,
        48,
        monkeypatch,
    )
    tool(
        "dispatch_task",
        {
            "type": "coder",
            "details": "fix the login redirect loop on settings",
            "preapproval": TESTS_YES,
        },
        184,
        100,
        monkeypatch,
    )
    tool(
        "draft_message",
        {"to": "Maya", "intent": "running ten minutes late"},
        2208,
        112,
        monkeypatch,
    )
    tool("get_status", {"drive_id": "17"}, 138, 310, monkeypatch)

    # the live-call monitor sees the departure call in progress
    live = {
        "conversation_id": "conv_17",
        "status": "in-progress",
        "start_time_unix_secs": at(2).timestamp(),
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"conversations": [live]}))
    ) as client:
        await dashboard.check_live_call(client)

    await health(
        monkeypatch,
        {"neon": 42, "elevenlabs": 188, "twilio": 121, "anthropic": 233, "github": 97, "ntfy": 64},
        {
            "neon": 330,
            "elevenlabs": 328,
            "twilio": 329,
            "anthropic": 331,
            "github": 332,
            "google_routes": 334,
            "ntfy": 333,
        },
    )
    dashboard.HEALTH["neon"].update(ok=True, detail=None)  # check_health ran without the pool
    compare(await dashboard.build_state(now=at(342)), "dashboard_state.json")


async def test_empty_fixture_is_backend_output(db, monkeypatch, workers):
    monkeypatch.setattr(app_db, "get_pool", lambda: Pool(db))
    await insert(db, "drives", {"id": 17, "source": "ios_shortcut", "started_at": at(0)})
    await health(
        monkeypatch,
        {"neon": 39, "elevenlabs": 176, "twilio": 118, "anthropic": 241, "github": 102, "ntfy": 61},
        {
            "neon": -8,
            "elevenlabs": -10,
            "twilio": -9,
            "anthropic": -7,
            "github": -6,
            "google_routes": -4,
            "ntfy": -5,
        },
    )
    dashboard.HEALTH["neon"].update(ok=True, detail=None)
    compare(await dashboard.build_state(now=at(4)), "dashboard_state_empty.json")
