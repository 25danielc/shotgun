"""ToolCallRecorder (app/dashboard.py) never breaks or slows /tools/*.

It must pass the request body through intact (FastAPI reads it after), pass a streamed response
through unchanged, never raise its own errors into a request, add negligible latency (a
background tool still answers in < 500 ms through the full app), and not record refused
callers, including on /tools/init. Offline: raw ASGI apps, or the real app via `http`.
"""

import asyncio
import json
import time

import httpx
import pytest

from app import dashboard, drives
from app.config import settings
from tests.helpers import DANIEL, TOOLS_SECRET

HEADERS = {"X-Shotgun-Secret": TOOLS_SECRET}
CALL = {"caller": DANIEL, "called": "+15555550199", "conversation_id": "conv_t"}


@pytest.fixture(autouse=True)
def clean_records():
    for record in (dashboard.TOOL_CALLS, dashboard.TOOL_EVENTS, dashboard.LIVE_CALL):
        record.clear()
    yield
    for record in (dashboard.TOOL_CALLS, dashboard.TOOL_EVENTS, dashboard.LIVE_CALL):
        record.clear()


async def echo_app(scope, receive, send):
    """Reads the whole body (in however many chunks), then streams it back in 3 parts."""
    body = b""
    while True:
        message = await receive()
        body += message.get("body", b"")
        if not message.get("more_body"):
            break
    await send({"type": "http.response.start", "status": 200, "headers": []})
    third = max(1, len(body) // 3)
    parts = [body[:third], body[third : 2 * third], body[2 * third :]]
    for i, part in enumerate(parts):
        await send({"type": "http.response.body", "body": part, "more_body": i < 2})


async def drive_asgi(app, path: str, chunks: list[bytes]):
    """Run one request through an ASGI app; returns (status, body chunks sent)."""
    scope = {"type": "http", "method": "POST", "path": path, "headers": [], "query_string": b""}
    incoming = [
        {"type": "http.request", "body": c, "more_body": i < len(chunks) - 1}
        for i, c in enumerate(chunks)
    ]
    sent: list[dict] = []

    async def receive():
        return incoming.pop(0) if incoming else {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    return status, [m["body"] for m in sent if m["type"] == "http.response.body"]


async def test_request_body_and_streamed_response_pass_through_intact():
    payload = json.dumps({"query": "ramen near " + "x" * 40_000}).encode()  # > MAX_BODY
    chunks = [payload[:10], payload[10:20_000], payload[20_000:]]
    status, out = await drive_asgi(
        dashboard.ToolCallRecorder(echo_app), "/tools/search_web", chunks
    )
    assert status == 200
    assert b"".join(out) == payload  # the app saw every byte, in order
    assert len(out) == 3  # streamed parts are forwarded one by one, not buffered
    assert dashboard.TOOL_CALLS[0]["tool"] == "search_web"
    assert dashboard.TOOL_CALLS[0]["ok"] is True


async def test_other_paths_are_untouched():
    status, out = await drive_asgi(dashboard.ToolCallRecorder(echo_app), "/events", [b"{}"])
    assert status == 200 and b"".join(out) == b"{}"
    assert not dashboard.TOOL_CALLS and not dashboard.TOOL_EVENTS


async def test_a_recording_failure_never_reaches_the_request(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("recorder bug")

    monkeypatch.setattr(dashboard, "record_tool_call", broken)
    status, out = await drive_asgi(
        dashboard.ToolCallRecorder(echo_app), "/tools/get_status", [b'{"a": 1}']
    )
    assert status == 200 and b"".join(out) == b'{"a": 1}'


async def test_app_errors_propagate_unchanged_and_are_recorded_as_failed():
    async def crashing(scope, receive, send):
        await receive()
        raise ValueError("app bug")

    with pytest.raises(ValueError, match="app bug"):
        await drive_asgi(dashboard.ToolCallRecorder(crashing), "/tools/get_status", [b"{}"])
    assert dashboard.TOOL_CALLS[0]["ok"] is False


async def test_latency_stops_at_the_last_response_byte():
    """Work after the response (e.g. BackgroundTasks) doesn't count against the tool."""

    async def slow_after(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})
        await asyncio.sleep(0.3)

    await drive_asgi(dashboard.ToolCallRecorder(slow_after), "/tools/dispatch_task", [b"{}"])
    assert dashboard.TOOL_CALLS[0]["latency_ms"] < 100


async def test_overhead_is_negligible():
    async def tiny(scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'{"ok": true}'})

    body = json.dumps(
        {**CALL, "type": "coder", "repo": "demo app", "details": "fix the login bug"}
    ).encode()
    wrapped = dashboard.ToolCallRecorder(tiny)
    n = 300

    async def timed(app) -> float:
        started = time.perf_counter()
        for _ in range(n):
            await drive_asgi(app, "/tools/dispatch_task", [body])
        return (time.perf_counter() - started) / n

    await timed(wrapped)  # warm up
    bare, recorded = await timed(tiny), await timed(wrapped)
    assert recorded - bare < 0.001, f"overhead {1000 * (recorded - bare):.3f} ms per request"


# ── through the real app ────────────────────────────────────────────────────
async def test_background_tool_answers_under_500ms_through_the_full_app(http, db):
    await drives.open_drive(db)
    body = {
        **CALL,
        "type": "coder",
        "repo": "demo app",
        "details": "fix the login bug",
        "label": "Fix login",
    }
    worst = 0.0
    for _ in range(5):
        started = time.perf_counter()
        response = await http.post("/tools/dispatch_task", json=body, headers=HEADERS)
        worst = max(worst, time.perf_counter() - started)
        assert response.status_code == 200 and response.json()["ok"] is True
    assert worst < 0.5, f"dispatch_task took {worst * 1000:.0f} ms"
    started = time.perf_counter()
    status = await http.post("/tools/get_status", json=CALL, headers=HEADERS)
    assert status.status_code == 200 and time.perf_counter() - started < 0.5

    recorded = dashboard.TOOL_CALLS[-1]
    assert recorded["tool"] == "dispatch_task" and recorded["ok"] is True
    assert recorded["query"] == "coder: fix the login bug"
    assert all(t["latency_ms"] < 500 for t in dashboard.TOOL_CALLS)


async def test_refused_callers_are_not_recorded(http, db):
    stranger = {**CALL, "caller": "+15555550111", "called": "+15555550199"}
    assert (await http.post("/tools/get_status", json=CALL)).status_code == 401
    forbidden = await http.post("/tools/get_status", json=stranger, headers=HEADERS)
    assert forbidden.status_code == 403
    assert not dashboard.TOOL_CALLS and not dashboard.TOOL_EVENTS


async def test_tools_init_through_the_full_app(http):
    init = {"caller_id": DANIEL, "agent_id": "a", "called_number": "+15555550199"}
    init |= {"call_sid": "CA1", "conversation_id": "conv_in"}

    # wrong secret: 401, nothing recorded
    assert (await http.post("/tools/init", json=init)).status_code == 401
    # a stranger: 200 with the refusal greeting, but not shown as a call
    stranger = await http.post(
        "/tools/init", json={**init, "caller_id": "+15555550111"}, headers=HEADERS
    )
    assert stranger.status_code == 200
    assert stranger.json()["dynamic_variables"]["caller_allowed"] == "no"
    assert not dashboard.call_live() and not dashboard.TOOL_EVENTS
    # Daniel: the body reached FastAPI intact (it answered with his greeting) and the call is live
    ok = await http.post("/tools/init", json=init, headers=HEADERS)
    assert ok.status_code == 200
    assert ok.json()["dynamic_variables"]["caller_allowed"] == "yes"
    assert dashboard.call_live() and dashboard.LIVE_CALL["conversation_id"] == "conv_in"
    assert not dashboard.TOOL_CALLS  # init is the call starting, not a tool


async def test_unconfigured_allowlist_init_is_not_a_call(http, monkeypatch):
    monkeypatch.setattr(settings, "allowed_caller_number", "")
    response = await http.post("/tools/init", json={"caller_id": DANIEL}, headers=HEADERS)
    assert response.status_code == 503
    assert not dashboard.call_live()


def test_tool_queries_name_the_main_argument():
    q = dashboard.tool_query
    assert q("approve_action", {"job_id": 7, "approved": True}) == "job #7: yes"
    assert q("approve_action", {"job_id": "7", "approved": False}) == "job #7: no"
    assert q("dispatch_task", {"type": "research", "details": "ramen"}) == "research: ramen"
    assert q("dispatch_task", {"request": "old agent text"}) == "old agent text"
    assert q("set_destination", {"destination": "North Campus"}) == "North Campus"
    assert q("get_status", {"drive_id": 3}) == "drive #3"
    assert q("get_status", {}) is None


def test_garbage_bodies_are_recorded_without_a_query():
    dashboard.record_tool_call("search_web", b"\xff not json", 12, 422)
    dashboard.record_tool_call("search_web", b"[1, 2]", 12, 200)
    assert [t["query"] for t in dashboard.TOOL_CALLS] == [None, None]
    assert dashboard.TOOL_CALLS[1]["ok"] is False


async def test_the_recorder_makes_no_http_calls(monkeypatch):
    """The recorder does no I/O: no HTTP client is created while it runs."""

    def no_client(*args, **kwargs):
        raise AssertionError("the recorder must not make HTTP calls")

    monkeypatch.setattr(httpx, "AsyncClient", no_client)
    await drive_asgi(dashboard.ToolCallRecorder(echo_app), "/tools/search_web", [b"{}"])
    assert dashboard.TOOL_CALLS
