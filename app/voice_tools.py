"""POST /tools/*: webhook tools the ElevenLabs voice agent calls mid-conversation.

Build steps: 2.2 (inline and background tools, D17), 2.3 (wired into ElevenLabs, caller
allowlist), 4.2 (approval: pre-approval at dispatch, spoken yes/no).

Tools (schemas live in config/elevenlabs_agent.json; every body also carries `caller` and
`called`, bound to system__caller_id and system__called_number).

Inline (do the work during the call, under 8 s; logic in app/inline.py; no database):
- search_web(query): a one- or two-sentence spoken answer (Haiku + Claude web search).
- draft_message(to, intent): draft text for the agent to read back. Sends nothing.
- set_destination(destination, drive_id?): Google Routes ETA from the plug-in location; stores
  destination, eta and arrival_call_at on the drive (step 5.1, app/eta.py). Uses the database.
  A slow or failed answer is 200 {"ok": false} with a spoken fallback, never a hang.

Background (answer in under 500 ms, never block on a worker):
- dispatch_task(type, details, label?, preapproval?): one job in the current drive (a drive is
  opened if none is), then return. The voice agent sends one typed job per part (D17; D13's
  fallback is now the main path). Without a worker `type` the text is stored as a `plan` job
  for the orchestrator (step 2.4), which splits it. `request` is the old name of `details`,
  still accepted. `preapproval` = {condition, require_tests_pass?, max_usd?}: the driver's yes
  given up front, so the job can finish without a call (step 4.2).
- get_status(drive_id?): one spoken sentence, plus the job list. With drive_id: that drive's
  jobs, finished ones included; without: every open job.
- approve_action(job_id, approved): needs_approval or exception -> approved, or -> failed
  ("Cancelled").

Business problems (unknown job, nothing to approve) come back as 200 {"ok": false, "message"}
so the agent can say something sensible. Auth problems are HTTP errors: 401 bad secret,
403 caller not allowed, 503 not configured.

Caller allowlist (step 2.3) - init: the ElevenLabs "conversation initiation client data" webhook.
It runs for inbound Twilio calls, and for outbound calls only when the request carries no
initiation data (ours always does), per the docs read 2026-10-03. It receives {caller_id,
agent_id, called_number, call_sid, conversation_id} and must return every dynamic variable the
agent defines. Daniel's number gets the normal greeting; anyone else gets caller_allowed "no"
and a refusal greeting, and the prompt makes the agent hang up at once. ElevenLabs has no
built-in caller allowlist, and the docs don't describe rejecting a call from this webhook, so
the tools' 403 is the second layer.

Hard rules: background tools answer in < 500 ms and never block on a worker; inline tools
answer in < 8 s; every request checks X-Shotgun-Secret (TOOLS_SHARED_SECRET) and the caller
(ALLOWED_CALLER_NUMBER).
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from psycopg import AsyncConnection
from psycopg_pool import PoolTimeout
from pydantic import BaseModel

from app import db, drives, eta, inline, jobs, telephony
from app.config import settings
from app.jobs import IllegalTransition, Job, JobNotFound, JobState, JobType
from app.security import check_caller, check_secret, normalize_number

log = logging.getLogger(__name__)
POOL_TIMEOUT = 2.0  # seconds; the agent's own timeout is 5 s and our budget is 0.5 s


def require_tools_secret(x_shotgun_secret: str | None = Header(default=None)) -> None:
    check_secret(x_shotgun_secret, settings.tools_shared_secret)


# Router-level dependency: runs before the body is parsed or a DB connection is taken, so an
# unauthenticated request gets 401 and costs nothing.
router = APIRouter(prefix="/tools", dependencies=[Depends(require_tools_secret)])


async def get_conn() -> AsyncIterator[AsyncConnection]:
    pool = db.get_pool()
    if pool is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "database not configured")
    try:
        async with pool.connection(timeout=POOL_TIMEOUT) as conn:
            yield conn
    except PoolTimeout as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "database busy") from exc


Conn = Annotated[AsyncConnection, Depends(get_conn)]


class CallContext(BaseModel):
    caller: str | None = None
    called: str | None = None
    conversation_id: str | None = None

    def authorize(self) -> None:
        check_caller(self.caller, self.called, settings.allowed_caller_number)


class Preapproval(BaseModel):
    """A yes given at dispatch time. Code enforces only the structured fields (D17)."""

    condition: str
    require_tests_pass: bool | None = None
    max_usd: float | None = None


class DispatchBody(CallContext):
    type: str | None = None
    details: str | None = None
    request: str | None = None  # the old name of `details`, sent by agents pushed before D17
    label: str | None = None
    to: str | None = None  # email: who it's for, as spoken ("Alex")
    repo: str | None = None  # code fix: which repo, as spoken ("my demo app")
    preapproval: Preapproval | None = None


class StatusBody(CallContext):
    drive_id: int | str | None = None


class ApproveBody(CallContext):
    job_id: int | str
    approved: bool


class SearchBody(CallContext):
    query: str
    drive_id: int | str | None = None


class DraftBody(CallContext):
    to: str
    intent: str


class DestinationBody(CallContext):
    destination: str
    drive_id: int | str | None = None


class Reply(BaseModel):
    ok: bool
    message: str
    job_id: int | None = None
    drive_id: int | None = None
    jobs: list[dict[str, Any]] | None = None


class InitBody(BaseModel):
    caller_id: str | None = None
    agent_id: str | None = None
    called_number: str | None = None
    call_sid: str | None = None
    conversation_id: str | None = None


INBOUND_GREETING = "Shotgun here."  # D17: never open with "How can I help?"
REFUSAL_GREETING = "Sorry, this line is private. Goodbye."


@router.post("/init")
async def init(body: InitBody) -> dict[str, Any]:
    """Conversation initiation webhook: no database, answers instantly."""
    allowed = normalize_number(settings.allowed_caller_number)
    if not allowed:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "caller allowlist not configured")
    is_daniel = normalize_number(body.caller_id) == allowed
    variables = telephony.call_variables(
        INBOUND_GREETING if is_daniel else REFUSAL_GREETING, call_kind="inbound"
    )
    variables["caller_allowed"] = "yes" if is_daniel else "no"
    if not is_daniel:
        log.warning("init: refused inbound call from an unknown number (%s)", body.call_sid)
    return {"type": "conversation_initiation_client_data", "dynamic_variables": variables}


@router.post("/search_web")
async def search_web(body: SearchBody) -> Reply:
    body.authorize()
    query = body.query.strip()
    if not query:
        return Reply(ok=False, message="I didn't catch the question.")
    try:
        answer = await inline.search_web(query, near=await drive_context(body.drive_id))
    except inline.InlineError as exc:
        log.warning("search_web failed: %s", exc)
        return Reply(ok=False, message="I couldn't find that quickly.")
    return Reply(ok=True, message=answer)


async def drive_context(drive_id: int | str | None) -> dict[str, str]:
    """Where the driver is, for "nearby": the drive's plug-in location and destination.

    Best effort: no database or no drive just means no location (a search still answers).
    """
    pool = db.get_pool()
    if pool is None:
        return {}
    try:
        async with pool.connection(timeout=POOL_TIMEOUT) as conn:
            drive = None
            if str(drive_id or "").strip().isdigit():
                drive = await drives.get_drive(conn, int(str(drive_id).strip()))
            drive = drive or await drives.current_drive(conn)
    except Exception:
        log.exception("search_web: no drive context")
        return {}
    return drives.near(drive)


@router.post("/draft_message")
async def draft_message(body: DraftBody) -> Reply:
    body.authorize()
    if not body.intent.strip():
        return Reply(ok=False, message="What should the message say?")
    try:
        draft = await inline.draft_message(body.to.strip(), body.intent.strip())
    except inline.InlineError as exc:
        log.warning("draft_message failed: %s", exc)
        return Reply(ok=False, message="I couldn't write that one. Can you say it again?")
    return Reply(ok=True, message=draft)


@router.post("/set_destination")
async def set_destination(body: DestinationBody, conn: Conn) -> Reply:
    body.authorize()
    text = body.destination.strip()
    if not text:
        return Reply(ok=False, message="Where are you headed?")
    drive = None
    if str(body.drive_id or "").strip().isdigit():
        drive = await drives.get_drive(conn, int(str(body.drive_id).strip()))
    drive = drive or await drives.current_or_open(conn)
    drive, message = await eta.set_destination(conn, drive, text)
    return Reply(ok=True, message=message, drive_id=drive.id)


def repo_matches(said: str) -> bool:
    """Did the driver name the one connected repo? "my demo app", "the demo workflow repo",
    "shotgun demo" and "shotgun-demo-app" do; "the shotgun repo" doesn't (that's 25danielc/shotgun,
    this project). Rule: the word "demo", or GITHUB_DEMO_REPO's own name."""
    words = set(re.findall(r"[a-z0-9]+", said.lower()))
    repo_name = settings.github_demo_repo.rsplit("/", 1)[-1].lower()
    return "demo" in words or repo_name in "-".join(re.findall(r"[a-z0-9]+", said.lower()))


def repo_refusal(said: str | None) -> str | None:
    """None if a code fix may go ahead, else what to tell the driver (only one repo is
    connected, and the driver has to say it's that one)."""
    spoken = settings.github_repo_spoken
    if not said or not said.strip():
        return f"Which repo is that in? I'm only connected to your {spoken}."
    if not repo_matches(said):
        return f"I'm not connected to {said.strip()}. I can only work on your {spoken}."
    return None


def job_type_of(name: str | None) -> JobType:
    """The worker type named, else `plan` (unknown or missing types are never dropped)."""
    if not name:
        return JobType.PLAN
    try:
        job_type = JobType(name.strip().lower())
    except ValueError:
        log.warning("dispatch_task: unknown type %r, storing as plan", name)
        return JobType.PLAN
    return job_type if job_type in jobs.WORKER_TYPES else JobType.PLAN


@router.post("/dispatch_task")
async def dispatch_task(body: DispatchBody, conn: Conn) -> Reply:
    body.authorize()
    text = (body.details or body.request or "").strip()
    if not text:
        return Reply(ok=False, message="I didn't catch a request.")
    details: dict[str, Any] = {}
    if body.conversation_id:
        details["conversation_id"] = body.conversation_id
    if body.label and body.label.strip():
        details["label"] = body.label.strip()
    if body.to and body.to.strip():
        details["to"] = body.to.strip()
        details["body"] = text  # email: the exact text the driver approved
    preapproval = body.preapproval.model_dump(exclude_none=True) if body.preapproval else None
    job_type = job_type_of(body.type)
    if job_type is JobType.CODER:
        refusal = repo_refusal(body.repo)
        if refusal:
            log.info("dispatch_task: coder refused (repo %r)", body.repo)
            return Reply(ok=False, message=refusal)
        details["repo"] = settings.github_demo_repo
    async with conn.transaction():
        drive = await drives.current_or_open(conn)
        job = await jobs.create_job(
            conn,
            job_type,
            details,
            request=text,
            source="voice",
            drive_id=drive.id,
            preapproval=preapproval,
        )
    log.info("dispatch_task: job %s (%s) queued in drive %s", job.id, job.type, drive.id)
    message = "On it."
    if preapproval:
        message = f"On it. Pre-approved: {preapproval['condition']}"
    return Reply(ok=True, message=message, job_id=job.id, drive_id=drive.id)


TYPE_NAMES = {
    JobType.CODER: "the code fix",
    JobType.EMAIL: "the email",
    JobType.FOOD: "the food order",
    JobType.RESEARCH: "the research",
}
STATE_PHRASES = {
    JobState.QUEUED: "is queued",
    JobState.RUNNING: "is in progress",
    JobState.NEEDS_APPROVAL: "needs your OK",
    JobState.EXCEPTION: "needs your OK",
    JobState.APPROVED: "is going through",
    JobState.DONE: "is done",
    JobState.FAILED: "didn't work out",
}


def status_sentence(listed: list[Job]) -> str:
    """One short spoken sentence about the jobs (open ones, or one drive's).

    e.g. "The code fix is in progress and the email needs your OK."
    """
    listed = [j for j in listed if not (j.type is JobType.PLAN and j.state is JobState.DONE)]
    if not listed:
        return "Nothing is in progress right now."
    parts = []
    for job in listed:
        if job.type is JobType.PLAN:
            if job.state is JobState.FAILED:
                parts.append("I couldn't work out one request")
            else:
                parts.append("I'm still planning your request")
        else:
            parts.append(f"{TYPE_NAMES[job.type]} {STATE_PHRASES[job.state]}")
    if len(parts) > 1:
        parts[-1] = "and " + parts[-1]
    sentence = (", " if len(parts) > 2 else " ").join(parts)
    return sentence[0].upper() + sentence[1:] + "."


@router.post("/get_status")
async def get_status(body: StatusBody, conn: Conn) -> Reply:
    body.authorize()
    drive_id = None
    if body.drive_id not in (None, ""):
        try:
            drive_id = int(str(body.drive_id).strip())
        except ValueError:
            return Reply(ok=False, message="I couldn't find that drive.")
    found = await jobs.list_jobs(conn, JobState if drive_id else None, drive_id=drive_id)
    listed = [{"id": j.id, "type": j.type, "state": j.state, "summary": j.summary} for j in found]
    return Reply(ok=True, message=status_sentence(found), drive_id=drive_id, jobs=listed)


@router.post("/approve_action")
async def approve_action(body: ApproveBody, conn: Conn) -> Reply:
    body.authorize()
    try:
        job_id = int(str(body.job_id).strip())
    except ValueError:
        return Reply(ok=False, message="I couldn't find that job.")
    if body.approved:
        new_state, summary, note = JobState.APPROVED, None, "driver said yes"
    else:
        new_state, summary, note = JobState.FAILED, "Cancelled", "driver said no"
    try:
        job = await jobs.transition(
            conn, job_id, new_state, summary=summary, note=note, expect=jobs.WAITING
        )
    except JobNotFound:
        return Reply(ok=False, message="I couldn't find that job.", job_id=job_id)
    except IllegalTransition:
        return Reply(ok=False, message="That one isn't waiting for your OK.", job_id=job_id)
    if job.state is JobState.FAILED:
        # The driver heard "OK, cancelled" on this call: no callback about it.
        await jobs.set_announced(conn, job.id, job.state)
    log.info("approve_action: job %s -> %s", job.id, job.state)
    message = "Done, going ahead." if body.approved else "OK, cancelled."
    return Reply(ok=True, message=message, job_id=job.id)
