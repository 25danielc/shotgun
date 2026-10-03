"""Mission control: the judging-table view of a drive. Read-only; approval stays voice-only.

Routes (contract: docs/dashboard_state.schema.md, page: static/dashboard.html):
    GET  /dashboard                  the page; it reads ?token= and polls the state every 1.5 s
    GET  /dashboard/state?token=     the state. 401 bad token, 503 DASHBOARD_TOKEN unset
    POST /dashboard/demo/{plug-in,arrive,unplug}?token=    DEMO_MODE=true only, else 404

The state handler never calls a third-party service. It reads Neon (drives, calls, jobs,
job_events) plus four in-process records kept by this module:
- TOOL_CALLS / tool events: ToolCallRecorder, an ASGI middleware around /tools/*, times every
  tool request (refused callers are not recorded). /tools/init marks a call as started.
- HEALTH: run_monitor() probes each service every HEALTH_SECONDS, read-only.
- LIVE_CALL: run_monitor() asks ElevenLabs for the agent's live conversation every
  CALL_SECONDS while a drive is open (the same list telephony.call_in_progress reads).
- Worker liveness: the app's background tasks (planner, research, coder) are found by name; a
  running task is the heartbeat. The email worker is a stub with no task, so it shows offline.

Everything derived (drive status, job steps, approval, upcoming) is computed per request from
those rows; nothing is invented. Free text is scrubbed of email addresses and phone numbers
because the page is shown in public.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response, status
from fastapi.responses import FileResponse
from psycopg.rows import class_row, dict_row

from app import db, drives, events, jobs, telephony
from app.calls import EXCEPTION_GAP_SECONDS, UNCLAIMED_MINUTES
from app.config import settings
from app.jobs import Job, JobState, JobType
from app.security import check_secret
from app.workers import coder

log = logging.getLogger(__name__)
router = APIRouter()

PAGE = Path(__file__).resolve().parents[1] / "static" / "dashboard.html"
STARTED = time.time()
SCHEMA_VERSION = 1
HEALTH_SECONDS = 30.0
CALL_SECONDS = 5.0
PROBE_TIMEOUT = 5.0
RECENT_DRIVE = timedelta(minutes=30)  # a parked drive stays on screen this long
RECENT_CALL = timedelta(minutes=2)
FRESH_DRIVE = timedelta(seconds=60)  # "plugged in" until the departure call or this long
SERVICES = (
    "api_server",
    "neon",
    "elevenlabs",
    "twilio",
    "anthropic",
    "github",
    "google_routes",
    "ntfy",
)
AGENT_TASKS = {"orchestrator": "planner", "coder": "coder", "research": "research", "email": None}
AGENT_TYPES = {
    "orchestrator": JobType.PLAN,
    "research": JobType.RESEARCH,
    "coder": JobType.CODER,
    "email": JobType.EMAIL,
}
INLINE_TOOLS = {"search_web", "draft_message", "set_destination"}
STEPS = {
    JobType.CODER: (
        "issue_filed",
        "action_running",
        "pr_opened",
        "tests",
        "approval_check",
        "merged",
    ),
    JobType.RESEARCH: ("searching", "summarizing"),
    JobType.EMAIL: ("drafting", "approval_check", "sent"),
    JobType.FOOD: ("cart_built", "approval_check", "ordered"),
    JobType.PLAN: ("planning",),
}
ACTION = {JobType.CODER: "merge the PR", JobType.EMAIL: "send it", JobType.FOOD: "place the order"}
READ_ONLY = {JobType.RESEARCH, JobType.PLAN}

# ── in-process records ──────────────────────────────────────────────────────
TOOL_CALLS: deque[dict[str, Any]] = deque(maxlen=20)
TOOL_EVENTS: deque[dict[str, Any]] = deque(maxlen=100)  # tool_called, call_*, demo events
HEALTH: dict[str, dict[str, Any]] = {}
LIVE_CALL: dict[str, Any] = {}  # {conversation_id, status, started_at, ended_at, duration_s}

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")


def scrub(value: Any) -> Any:
    """Mask email addresses and phone numbers in any string inside value."""
    if isinstance(value, str):
        return PHONE_RE.sub("[phone]", EMAIL_RE.sub("[email]", value))
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _now() -> datetime:
    return datetime.now(UTC)


def add_event(type_: str, message: str, *, at: datetime | None = None, job_id=None) -> None:
    TOOL_EVENTS.append(
        {"at": at or _now(), "type": type_, "message": scrub(message), "job_id": job_id}
    )


# ── /tools/* recorder ───────────────────────────────────────────────────────
def tool_query(tool: str, body: dict[str, Any]) -> str | None:
    if tool == "draft_message" and body.get("intent"):
        return f"{body.get('to') or ''}: {body['intent']}".strip(": ")
    for key in ("query", "destination", "details", "request", "label", "message"):
        if body.get(key):
            return str(body[key])
    if body.get("job_id") is not None:
        return f"job #{body['job_id']}"
    return None


def record_tool_call(tool: str, raw: bytes, latency_ms: int, status_code: int) -> None:
    if status_code in (401, 403):
        return  # a refused caller: not part of the drive
    try:
        body = json.loads(raw or b"{}")
        body = body if isinstance(body, dict) else {}
    except ValueError:
        body = {}
    at = _now() - timedelta(milliseconds=latency_ms)
    if tool == "init":
        LIVE_CALL.update(
            conversation_id=body.get("conversation_id"),
            status="active",
            started_at=at,
            ended_at=None,
            duration_s=None,
        )
        add_event("call_started", "call connected: agent on the line", at=at)
        return
    query = tool_query(tool, body)
    if query and len(query) > 120:
        query = query[:119] + "…"
    ok = status_code < 400
    TOOL_CALLS.appendleft(
        {"at": at, "tool": tool, "query": scrub(query), "latency_ms": latency_ms, "ok": ok}
    )
    shown = f'{tool} "{query}"' if query else tool
    add_event("tool_called", f"{shown} {latency_ms}ms{'' if ok else ' FAILED'}", at=at)


class ToolCallRecorder:
    """Pure ASGI middleware: times each /tools/* request and keeps its body for the query."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/tools/"):
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        body = bytearray()
        code = 500

        async def receive_and_keep():
            message = await receive()
            if message["type"] == "http.request" and len(body) < 16_384:
                body.extend(message.get("body", b""))
            return message

        async def send_and_watch(message):
            nonlocal code
            if message["type"] == "http.response.start":
                code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive_and_keep, send_and_watch)
        finally:
            latency = round((time.perf_counter() - started) * 1000)
            tool = scope["path"].removeprefix("/tools/").strip("/")
            try:
                record_tool_call(tool, bytes(body), latency, code)
            except Exception:
                log.exception("dashboard: could not record tool call %s", tool)


# ── health + live call monitor ──────────────────────────────────────────────
async def _http_probe(client: httpx.AsyncClient, method: str, url: str, **kwargs) -> str | None:
    """None if the service answered 2xx, else a short reason (never the URL: it may hold keys)."""
    try:
        response = await client.request(method, url, **kwargs)
    except httpx.TimeoutException:
        return f"timeout after {int(PROBE_TIMEOUT * 1000)}ms"
    except httpx.HTTPError as exc:
        return type(exc).__name__
    if response.status_code >= 400:
        return f"HTTP {response.status_code}"
    return None


async def probe(name: str, client: httpx.AsyncClient) -> tuple[bool | None, str | None]:
    """(ok, detail) for one service. Read-only calls; ok=None means deliberately not probed."""
    s = settings
    if name == "api_server":
        return True, None
    if name == "neon":
        pool = db.get_pool()
        if pool is None:
            return False, "no pool (DATABASE_URL)"
        try:
            async with pool.connection(timeout=PROBE_TIMEOUT) as conn:
                await conn.execute("select 1")
        except Exception as exc:
            return False, type(exc).__name__
        return True, None
    if name == "elevenlabs":
        if not s.elevenlabs_api_key:
            return False, "not configured"
        url = "https://api.elevenlabs.io/v1/convai/agents"  # key check, DECISIONS §11
        detail = await _http_probe(
            client,
            "GET",
            url,
            params={"page_size": 1},
            headers={"xi-api-key": s.elevenlabs_api_key},
        )
        return detail is None, detail
    if name == "twilio":
        if not (s.twilio_account_sid and s.twilio_auth_token):
            return False, "not configured"
        url = f"https://api.twilio.com/2010-04-01/Accounts/{s.twilio_account_sid}.json"
        detail = await _http_probe(
            client, "GET", url, auth=(s.twilio_account_sid, s.twilio_auth_token)
        )
        return detail is None, detail
    if name == "anthropic":
        if not s.anthropic_api_key:
            return False, "not configured"
        headers = {"x-api-key": s.anthropic_api_key, "anthropic-version": "2023-06-01"}
        detail = await _http_probe(
            client,
            "GET",
            "https://api.anthropic.com/v1/models",
            params={"limit": 1},
            headers=headers,
        )
        return detail is None, detail
    if name == "github":
        if not (s.github_token and s.github_demo_repo):
            return False, "not configured"
        headers = {
            "Authorization": f"Bearer {s.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        url = f"https://api.github.com/repos/{s.github_demo_repo}"
        detail = await _http_probe(client, "GET", url, headers=headers)
        return detail is None, detail
    if name == "google_routes":
        if not s.google_maps_api_key:
            return False, "not configured"
        return None, "not probed: billed per request"
    if name == "ntfy":
        if not s.ntfy_topic:
            return False, "not configured"
        # Health endpoint, not the topic: a request to the topic would publish its name.
        # TODO(verify): GET /v1/health on ntfy.sh returns {"healthy": true}.
        detail = await _http_probe(client, "GET", f"{s.ntfy_server.rstrip('/')}/v1/health")
        return detail is None, detail
    raise ValueError(name)


async def check_health(client: httpx.AsyncClient) -> None:
    async def one(name: str) -> None:
        started = time.perf_counter()
        try:
            ok, detail = await probe(name, client)
        except Exception as exc:
            ok, detail = False, type(exc).__name__
        latency = round((time.perf_counter() - started) * 1000)
        HEALTH[name] = {
            "name": name,
            "ok": ok,
            "latency_ms": latency if ok is not None else None,
            "checked_at": _now(),
            "detail": detail,
        }

    await asyncio.gather(*(one(name) for name in SERVICES))


async def check_live_call(client: httpx.AsyncClient) -> None:
    """Mirror the agent's live conversation into LIVE_CALL (status, start, end)."""
    response = await client.get(
        telephony.CONVERSATIONS_URL,
        params={"agent_id": settings.elevenlabs_agent_id, "page_size": 5},
        headers={"xi-api-key": settings.elevenlabs_api_key},
    )
    response.raise_for_status()
    conversations = response.json().get("conversations", [])
    now = time.time()
    live = next(
        (
            c
            for c in conversations
            if c.get("status") in telephony.LIVE_STATUSES
            and now - (c.get("start_time_unix_secs") or now) < telephony.STALE_CALL_SECONDS
        ),
        None,
    )
    if live:
        started = datetime.fromtimestamp(live.get("start_time_unix_secs") or now, UTC)
        state = "ringing" if live.get("status") == "initiated" else "active"
        if LIVE_CALL.get("status") not in ("active", "ringing") and state == "active":
            add_event("call_started", "call connected: agent on the line", at=started)
        LIVE_CALL.update(
            conversation_id=live.get("conversation_id"),
            status=state,
            started_at=started,
            ended_at=None,
            duration_s=None,
        )
    elif LIVE_CALL.get("status") in ("active", "ringing"):
        ended = _now()
        started = LIVE_CALL.get("started_at") or ended
        last = next(
            (c for c in conversations if c.get("conversation_id") == LIVE_CALL["conversation_id"]),
            {},
        )
        # TODO(verify): call_duration_secs on the conversations list item.
        duration = last.get("call_duration_secs") or int((ended - started).total_seconds())
        LIVE_CALL.update(status="ended", ended_at=ended, duration_s=int(duration))
        add_event("call_ended", f"call ended after {duration // 60:02d}:{duration % 60:02d}")


async def _drive_open(pool) -> bool:
    if pool is None:
        return False
    try:
        async with pool.connection() as conn:
            return await drives.current_drive(conn) is not None
    except Exception:
        return False


async def run_monitor(pool) -> None:
    """Background loop: service health every 30 s, live call every 5 s while a drive is open."""
    calls_ok = settings.elevenlabs_api_key and settings.elevenlabs_agent_id
    last_health = 0.0
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT) as client:
        while True:
            if time.monotonic() - last_health >= HEALTH_SECONDS:
                last_health = time.monotonic()
                try:
                    await check_health(client)
                except Exception:
                    log.exception("dashboard: health check failed")
            if calls_ok and (
                LIVE_CALL.get("status") in ("active", "ringing") or await _drive_open(pool)
            ):
                with contextlib.suppress(Exception):
                    await check_live_call(client)
            await asyncio.sleep(CALL_SECONDS)


# ── derived views ───────────────────────────────────────────────────────────
def call_live() -> bool:
    return LIVE_CALL.get("status") in ("active", "ringing")


def label_of(job: Job) -> str:
    return job.details.get("label") or job.request or f"{job.type} job"


def reached(history: list[dict[str, Any]], state: str) -> datetime | None:
    return next((e["at"] for e in history if e["to_state"] == state), None)


def step_dict(name, state="pending", started=None, finished=None, detail=None) -> dict[str, Any]:
    return {
        "name": name,
        "state": state,
        "started_at": iso(started),
        "finished_at": iso(finished),
        "detail": detail,
    }


def coder_steps(job: Job, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    r = job.result or {}
    running_at = reached(history, "running")
    pr_at = datetime.fromisoformat(r["pr_opened_at"]) if r.get("pr_opened_at") else None
    approved_at = reached(history, "approved")
    waiting_at = reached(history, "needs_approval") or reached(history, "exception")
    out = []
    if r.get("issue_number"):
        out.append(
            step_dict("issue_filed", "done", running_at, None, f"issue #{r['issue_number']}")
        )
        if r.get("pr_number"):
            out.append(step_dict("action_running", "done", running_at, pr_at))
        else:
            out.append(step_dict("action_running", "running", running_at))
    else:
        out.append(step_dict("issue_filed", "running" if running_at else "pending", running_at))
        out.append(step_dict("action_running"))
    if r.get("pr_number"):
        out.append(step_dict("pr_opened", "done", None, pr_at, f"PR #{r['pr_number']}"))
    else:
        out.append(step_dict("pr_opened"))
    tests = r.get("tests")
    needs = (job.preapproval or {}).get("require_tests_pass")
    if tests == "pending":
        out.append(step_dict("tests", "running", pr_at, detail="waiting for checks"))
    elif tests == "success":
        out.append(step_dict("tests", "done", pr_at, approved_at or waiting_at, "passed"))
    elif tests:
        out.append(step_dict("tests", "failed", pr_at, waiting_at, str(tests).replace("_", " ")))
    else:
        out.append(step_dict("tests", "pending" if needs else "skipped"))
    out.append(approval_step(job, history, waiting_at, approved_at))
    if job.state == JobState.DONE:
        out.append(step_dict("merged", "done", approved_at, reached(history, "done")))
    elif job.state == JobState.APPROVED:
        out.append(step_dict("merged", "running", approved_at))
    else:
        out.append(step_dict("merged"))
    return out


def approval_step(job, history, waiting_at, approved_at) -> dict[str, Any]:
    if approved_at:
        detail = "pre-approved" if job.preapproval and not waiting_at else "spoken yes"
        return step_dict("approval_check", "done", waiting_at or approved_at, approved_at, detail)
    if job.state in jobs.WAITING:
        detail = "exception" if job.state == JobState.EXCEPTION else "held for a yes"
        return step_dict("approval_check", "running", waiting_at, detail=detail)
    if job.state == JobState.FAILED and waiting_at:
        return step_dict("approval_check", "failed", waiting_at, reached(history, "failed"), "no")
    return step_dict("approval_check")


def simple_steps(job: Job, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Steps for workers without finer progress: first step = running, last = done."""
    names = STEPS.get(JobType(job.type), ())
    running_at = reached(history, "running")
    done_at = reached(history, "done")
    waiting_at = reached(history, "needs_approval") or reached(history, "exception")
    approved_at = reached(history, "approved")
    out = []
    for i, name in enumerate(names):
        if name == "approval_check":
            out.append(approval_step(job, history, waiting_at, approved_at))
        elif job.state == JobState.DONE:
            out.append(step_dict(name, "done", running_at if i == 0 else None, done_at))
        elif i == 0 and job.state == JobState.RUNNING:
            out.append(step_dict(name, "running", running_at))
        elif i == 0 and job.state not in (JobState.QUEUED, JobState.FAILED):
            out.append(step_dict(name, "done", running_at, waiting_at or approved_at))
        elif i == len(names) - 1 and job.state == JobState.APPROVED:
            out.append(step_dict(name, "running", approved_at))
        else:
            out.append(step_dict(name))
    return out


def steps_for(job: Job, history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = coder_steps(job, history) if job.type == JobType.CODER else simple_steps(job, history)
    if job.state == JobState.FAILED:
        # the step that was in progress (or next) is the one that failed
        failed_at = reached(history, "failed")
        for step in out:
            if step["state"] in ("running", "pending"):
                step.update(state="failed", finished_at=iso(failed_at), detail=job.error or None)
                break
    return out


def progress_of(steps: list[dict[str, Any]]) -> float | None:
    if not steps:
        return None
    finished = sum(1 for s in steps if s["state"] in ("done", "skipped"))
    return round(finished / len(steps), 2)


def approval_of(job: Job, history: list[dict[str, Any]]) -> dict[str, Any]:
    if job.state == JobState.EXCEPTION:
        note = next(
            (e["note"] for e in reversed(history) if e["to_state"] == "exception" and e["note"]),
            None,
        )
        return {"status": "exception", "note": (note or "").removeprefix("pre-approval broken: ")}
    if job.state == JobState.NEEDS_APPROVAL:
        return {"status": "pending" if call_live() else "held", "note": None}
    if job.preapproval:
        return {"status": "preapproved", "note": None}
    if job.type in READ_ONLY:
        return {"status": "not_needed", "note": None}
    if reached(history, "approved"):
        return {"status": "approved", "note": None}
    if job.state == JobState.FAILED and reached(history, "needs_approval"):
        return {"status": "declined", "note": None}
    return {"status": "pending", "note": None}


def job_view(job: Job, history: list[dict[str, Any]]) -> dict[str, Any]:
    steps = steps_for(job, history)
    r = job.result or {}
    return {
        "id": job.id,
        "type": str(job.type),
        "title": scrub(label_of(job))[:60],
        "details": scrub(job.details),
        "state": str(job.state),
        "progress": progress_of(steps),
        "steps": steps,
        "preapproval": scrub(
            {
                "condition": job.preapproval.get("condition", ""),
                "require_tests_pass": job.preapproval.get("require_tests_pass"),
                "max_usd": job.preapproval.get("max_usd"),
            }
        )
        if job.preapproval
        else None,
        "approval": approval_of(job, history),
        "result_summary": scrub(job.summary),
        "result_url": r.get("pr_url") or r.get("issue_url"),
        "created_at": iso(job.created_at),
        "updated_at": iso(job.updated_at),
    }


def drive_status(drive: drives.Drive, drive_calls: list[dict[str, Any]], now: datetime) -> str:
    if drive.ended_at:
        return "parked"
    if call_live():
        return "on_call"
    if not drive_calls and now - drive.started_at < FRESH_DRIVE:
        return "plugged_in"
    return "silent"


def call_view(drive_calls: list[dict[str, Any]], now: datetime) -> dict[str, Any] | None:
    if not LIVE_CALL.get("status"):
        return None
    ended = LIVE_CALL.get("ended_at")
    if ended and now - ended > RECENT_CALL:
        return None
    started = LIVE_CALL.get("started_at") or now
    kind = next(
        (
            c["kind"]
            for c in drive_calls
            if c["conversation_id"] == LIVE_CALL.get("conversation_id")
        ),
        "inbound",
    )
    duration = LIVE_CALL.get("duration_s")
    if LIVE_CALL["status"] == "active":
        duration = int((now - started).total_seconds())
    last_tool = next((t["tool"] for t in TOOL_CALLS if t["at"] >= started), None)
    return {
        "status": LIVE_CALL["status"],
        "kind": kind,
        "started_at": iso(started),
        "duration_s": max(0, int(duration or 0)),
        "last_tool": last_tool,
    }


def upcoming_for(
    drive: drives.Drive | None,
    drive_jobs: list[Job],
    history: dict[int, list[dict[str, Any]]],
    last_exception_call: datetime | None,
    now: datetime,
) -> list[dict[str, Any]]:
    if drive is None or drive.ended_at:
        return []
    out: list[dict[str, Any]] = []
    workers = [j for j in drive_jobs if j.type != JobType.PLAN]
    if not drive.arrival_called and workers:
        n = len(workers)
        what = f"arrival call: batched summary of {n} job{'s' if n != 1 else ''}"
        if drive.arrival_call_at:
            out.append(_up(drive.arrival_call_at, "arrival_call", what))
        else:
            out.append(_up(None, "arrival_call", f"{what}, once every job is done or held"))
    for job in drive_jobs:
        r = job.result or {}
        action = ACTION.get(JobType(job.type), "finish")
        if job.state in (JobState.RUNNING, JobState.APPROVED) and job.preapproval:
            cond = job.preapproval.get("condition") or "pre-approved"
            when = "if tests pass" if job.preapproval.get("require_tests_pass") else "when ready"
            out.append(_up(None, "preapproved_action", f"{action} {when} ({cond})", job.id))
        if job.state == JobState.NEEDS_APPROVAL:
            at = None if drive.arrival_called else drive.arrival_call_at
            out.append(_up(at, "held_action", f"held for arrival call: {label_of(job)}", job.id))
        if job.state == JobState.EXCEPTION and job.announced_state != str(job.state):
            at = None
            if last_exception_call:
                at = max(now, last_exception_call + timedelta(seconds=EXCEPTION_GAP_SECONDS))
            out.append(_up(at, "exception_call", f"exception call: {label_of(job)}", job.id))
        if job.state == JobState.QUEUED:
            at = job.created_at + timedelta(minutes=UNCLAIMED_MINUTES)
            out.append(_up(at, "job_expiry", f"#{job.id} fails if no worker claims it", job.id))
        if job.type == JobType.CODER and job.state == JobState.RUNNING:
            if not r.get("pr_number"):
                at = job.updated_at + timedelta(minutes=coder.PR_TIMEOUT_MINUTES)
                out.append(_up(at, "job_expiry", f"coder #{job.id} fails if no PR by then", job.id))
            elif r.get("tests") == "pending" and r.get("pr_opened_at"):
                at = datetime.fromisoformat(r["pr_opened_at"]) + timedelta(
                    minutes=coder.TESTS_TIMEOUT_MINUTES
                )
                out.append(
                    _up(at, "job_expiry", f"#{job.id} goes to you if tests never report", job.id)
                )
    out.sort(key=lambda u: (u["at"] is None, u["at"] or ""))
    return out


def _up(at: datetime | None, kind: str, description: str, job_id: int | None = None):
    return {"at": iso(at), "kind": kind, "description": scrub(description), "job_id": job_id}


def events_for(
    drive: drives.Drive | None,
    drive_calls: list[dict[str, Any]],
    drive_jobs: list[Job],
    history: dict[int, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows: list[tuple[datetime, int, str, str, int | None]] = []
    since = drive.started_at - timedelta(seconds=60) if drive else None
    if drive:
        how = "CarPlay connected," if drive.source == "carplay" else f"{drive.source}:"
        rows.append((drive.started_at, 0, "plug_in", f"{how} drive #{drive.id} opened", None))
        for c in drive_calls:
            kind = {"departure": "call_started", "arrival": "arrival_call"}.get(
                c["kind"], "exception_call"
            )
            n = len(c["job_ids"] or [])
            extra = f", {n} job{'s' if n != 1 else ''}" if c["kind"] != "departure" else ""
            rows.append((c["at"], 1, kind, f"{c['kind']} call placed{extra}", None))
        if drive.ended_at:
            rows.append(
                (
                    drive.ended_at,
                    9,
                    "unplug",
                    f"CarPlay disconnected, drive #{drive.id} closed",
                    None,
                )
            )
    by_id = {j.id: j for j in drive_jobs}
    for job_id, hist in history.items():
        job = by_id[job_id]
        for e in hist:
            if e["from_state"] is None:
                pre = f", pre-approved: {job.preapproval['condition']}" if job.preapproval else ""
                msg = f"{job.type} #{job_id}: {label_of(job)}{pre}"
                rows.append((e["at"], 2, "job_dispatched", msg, job_id))
            else:
                note = f": {e['note']}" if e["note"] else ""
                msg = f"#{job_id} {e['from_state']} -> {e['to_state']}{note}"
                rows.append((e["at"], 3, "job_state", msg, job_id))
    for e in TOOL_EVENTS:
        if since is None or e["at"] >= since:
            rows.append((e["at"], 4, e["type"], e["message"], e["job_id"]))
    rows.sort(key=lambda r: (r[0], r[1]))
    out, last_id = [], 0
    for at, rank, type_, message, job_id in rows[-100:]:
        # Monotonic ids from time, so the page can append "id > last seen".
        event_id = max(int(at.timestamp() * 1000) * 16 + rank, last_id + 1)
        last_id = event_id
        out.append(
            {
                "id": event_id,
                "at": iso(at),
                "type": type_,
                "message": scrub(message),
                "job_id": job_id,
            }
        )
    return out


def agents_view(
    drive_jobs: list[Job], open_jobs: list[Job], now: datetime
) -> tuple[list, str | None]:
    alive = {t.get_name() for t in asyncio.all_tasks() if not t.done()}
    out = []
    voice_health = HEALTH.get("elevenlabs", {})
    last_tool = TOOL_CALLS[0]["at"] if TOOL_CALLS else None
    if call_live():
        voice = "busy"
    elif voice_health.get("ok") is False:
        voice = "offline"
    else:
        voice = "idle"
    beat = max(
        filter(None, [last_tool, voice_health.get("checked_at"), LIVE_CALL.get("started_at")]),
        default=None,
    )
    out.append(
        {"name": "voice", "status": voice, "last_heartbeat": iso(beat), "current_job_id": None}
    )
    for name, task in AGENT_TASKS.items():
        busy = next(
            (j for j in open_jobs if j.type == AGENT_TYPES[name] and j.state == JobState.RUNNING),
            None,
        )
        if task and task in alive:
            status_ = "busy" if busy else "idle"
            beat = now
        else:
            status_, beat = "offline", None
        out.append(
            {
                "name": name,
                "status": status_,
                "last_heartbeat": iso(beat),
                "current_job_id": busy.id if busy and status_ == "busy" else None,
            }
        )
    # active: whoever did the most recent thing
    latest: tuple[datetime, str] | None = None
    if call_live():
        latest = (now, "voice")
    for job in drive_jobs:
        if job.state == JobState.RUNNING:
            name = next((n for n, t in AGENT_TYPES.items() if t == job.type), None)
            if name is None:
                continue
            if latest is None or (latest[1] != "voice" and job.updated_at > latest[0]):
                latest = (job.updated_at, name)
    return out, latest[1] if latest else None


def services_view(now: datetime) -> list[dict[str, Any]]:
    out = []
    for name in SERVICES:
        h = HEALTH.get(name)
        if name == "api_server":
            h = {"name": name, "ok": True, "latency_ms": 0, "checked_at": now, "detail": None}
        if h is None:
            out.append(
                {"name": name, "ok": None, "latency_ms": None, "checked_at": None, "detail": None}
            )
        else:
            out.append({**h, "checked_at": iso(h["checked_at"])})
    return out


async def build_state(now: datetime | None = None) -> dict[str, Any]:
    now = now or _now()
    drive = None
    drive_calls: list[dict[str, Any]] = []
    drive_jobs: list[Job] = []
    history: dict[int, list[dict[str, Any]]] = {}
    last_exception_call = None
    pool = db.get_pool()
    if pool is not None:
        try:
            async with pool.connection() as conn:
                drive, drive_calls, drive_jobs, history, last_exception_call = await _read(
                    conn, now
                )
        except Exception:
            log.exception("dashboard: state read failed")
    open_jobs = [j for j in drive_jobs if j.state in jobs.OPEN]
    shown = [j for j in drive_jobs if j.type != JobType.PLAN or j.state in jobs.OPEN]
    agents, active = agents_view(drive_jobs, open_jobs, now)
    tool_calls = [{**t, "at": iso(t["at"])} for t in TOOL_CALLS]
    return {
        "schema_version": SCHEMA_VERSION,
        "server": {
            "now": iso(now),
            "uptime_s": int(time.time() - STARTED),
            "demo_mode": settings.demo_mode,
        },
        "services": services_view(now),
        "drive": None
        if drive is None
        else {
            "id": drive.id,
            "status": drive_status(drive, drive_calls, now),
            "started_at": iso(drive.started_at),
            "ended_at": iso(drive.ended_at),
            "destination": scrub(drive.destination),
            "eta": iso(drive.eta),
            "arrival_call_at": iso(drive.arrival_call_at),
            "arrival_called": drive.arrival_called,
        },
        "call": call_view(drive_calls, now),
        "jobs": [job_view(j, history.get(j.id, [])) for j in shown],
        "upcoming": upcoming_for(drive, drive_jobs, history, last_exception_call, now),
        "tool_calls": tool_calls,
        "events": events_for(drive, drive_calls, drive_jobs, history),
        "agents": agents,
        "active_agent": active,
    }


async def _read(conn, now: datetime):
    cur = conn.cursor(row_factory=class_row(drives.Drive))
    await cur.execute("select * from drives order by started_at desc, id desc limit 1")
    drive = await cur.fetchone()
    if drive is not None:
        stale_open = drive.ended_at is None and now - drive.started_at > timedelta(
            hours=drives.MAX_HOURS
        )
        old_parked = drive.ended_at is not None and now - drive.ended_at > RECENT_DRIVE
        if stale_open or old_parked:
            drive = None
    last_exception_call = await drives.last_call_at(conn, "exception")
    if drive is None:
        return None, [], [], {}, last_exception_call
    cur = conn.cursor(row_factory=dict_row)
    await cur.execute(
        "select kind, conversation_id, job_ids, at from calls where drive_id = %s order by at",
        (drive.id,),
    )
    drive_calls = await cur.fetchall()
    jcur = conn.cursor(row_factory=class_row(DashJob))
    await jcur.execute("select * from jobs where drive_id = %s order by id", (drive.id,))
    drive_jobs = await jcur.fetchall()
    history: dict[int, list[dict[str, Any]]] = {j.id: [] for j in drive_jobs}
    if drive_jobs:
        await cur.execute(
            "select job_id, from_state, to_state, note, at from job_events"
            " where job_id = any(%s) order by id",
            ([j.id for j in drive_jobs],),
        )
        for row in await cur.fetchall():
            history[row["job_id"]].append(row)
    return drive, drive_calls, drive_jobs, history, last_exception_call


class DashJob(Job):
    announced_state: str | None = None


# ── routes ──────────────────────────────────────────────────────────────────
def check_token(token: str | None) -> None:
    check_secret(token, settings.dashboard_token)


@router.get("/dashboard", include_in_schema=False)
async def page() -> FileResponse:
    return FileResponse(PAGE, media_type="text/html", headers={"Cache-Control": "no-store"})


@router.get("/dashboard/state")
async def state(response: Response, token: str | None = Query(default=None)) -> dict[str, Any]:
    check_token(token)
    response.headers["Cache-Control"] = "no-store"
    return await build_state()


DEMO_ACTIONS = ("plug-in", "arrive", "unplug")


@router.post("/dashboard/demo/{action}", status_code=status.HTTP_202_ACCEPTED)
async def demo(
    action: str, background: BackgroundTasks, token: str | None = Query(default=None)
) -> dict[str, bool]:
    if not settings.demo_mode or action not in DEMO_ACTIONS:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    check_token(token)
    if action == "plug-in":
        add_event("plug_in", "simulated plug-in (demo)")
        calling, variables, drive = await events.plan_departure(
            events.Event(source="dashboard_demo", event=events.CONNECTED)
        )
        if calling:
            background.add_task(events.ring, variables, drive)
    elif action == "unplug":
        add_event("unplug", "simulated unplug (demo)")
        text = await events.end_drive()
        if text:
            background.add_task(events.send_recap, text)
    else:
        pool = db.get_pool()
        if pool is None:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "no database")
        async with pool.connection() as conn:
            cur = await conn.execute(
                "update drives set arrival_call_at = now()"
                " where ended_at is null and not arrival_called returning id"
            )
            row = await cur.fetchone()
        if row is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "no open drive waiting for its arrival call"
            )
        add_event(
            "arrival_scheduled", f"simulated arrival (demo): drive #{row[0]} calls on the next tick"
        )
    return {"accepted": True}
