"""POST /tools/*: webhook tools the ElevenLabs voice agent calls mid-conversation.

Build steps: 2.2 (webhooks answer < 500 ms and write rows), 2.3 (wired into ElevenLabs,
caller allowlist), 4.2 (spoken approval loop).

Tools (schemas live in config/elevenlabs_agent.json; every body also carries `caller` and
`called`, bound to system__caller_id and system__called_number):
- dispatch_task(request, type?): one insert, then return. Without `type` (the normal path) the
  request is stored as a queued `plan` job for the orchestrator (app/orchestrator.py, step 2.4).
  With a worker `type` (the D13 fallback) that worker job is created directly.
- get_status(): one spoken sentence about open jobs, plus the job list.
- approve_action(job_id, approved): needs_approval -> approved, or -> failed ("Cancelled").
  The only path to an irreversible action; the worker acts on `approved` (step 4.2).

Business problems (unknown job, nothing to approve) come back as 200 {"ok": false, "message"}
so the agent can say something sensible. Auth problems are HTTP errors: 401 bad secret,
403 caller not allowed, 503 not configured.

Still to come in step 2.3:
- init: the ElevenLabs "conversation initiation client data" webhook. It receives
  {caller_id, agent_id, called_number, call_sid, conversation_id} and returns
  {"type": "conversation_initiation_client_data", "dynamic_variables": {...}}. An unknown
  caller_id gets a refusal greeting and the agent calls end_call. ElevenLabs has no built-in
  caller allowlist (docs read 2026-10-03). TODO(verify) whether this webhook can reject a
  call outright.

Hard rules: answer in < 500 ms; never block on a worker; check X-Shotgun-Secret
(TOOLS_SHARED_SECRET) and the caller (ALLOWED_CALLER_NUMBER) on every request.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from psycopg import AsyncConnection
from psycopg_pool import PoolTimeout
from pydantic import BaseModel

from app import db, jobs
from app.config import settings
from app.jobs import IllegalTransition, Job, JobNotFound, JobState, JobType
from app.security import check_caller, check_secret

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


class DispatchBody(CallContext):
    request: str
    type: str | None = None


class ApproveBody(CallContext):
    job_id: int | str
    approved: bool


class Reply(BaseModel):
    ok: bool
    message: str
    job_id: int | None = None
    jobs: list[dict[str, Any]] | None = None


@router.post("/dispatch_task")
async def dispatch_task(body: DispatchBody, conn: Conn) -> Reply:
    body.authorize()
    request = body.request.strip()
    if not request:
        return Reply(ok=False, message="I didn't catch a request.")
    job_type = JobType.PLAN
    if body.type:
        try:
            job_type = JobType(body.type.strip().lower())
        except ValueError:
            log.warning("dispatch_task: unknown type %r, storing as plan", body.type)
        if job_type not in jobs.WORKER_TYPES:
            job_type = JobType.PLAN
    job = await jobs.create_job(
        conn,
        job_type,
        {"conversation_id": body.conversation_id} if body.conversation_id else {},
        request=request,
        source="voice",
    )
    log.info("dispatch_task: job %s (%s) queued", job.id, job.type)
    return Reply(ok=True, message="On it.", job_id=job.id)


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
    JobState.APPROVED: "is going through",
}


def status_sentence(open_jobs: list[Job]) -> str:
    """One short spoken sentence about open jobs.

    e.g. "The code fix is in progress and the email needs your OK."
    """
    if not open_jobs:
        return "Nothing is in progress right now."
    parts = []
    for job in open_jobs:
        if job.type is JobType.PLAN:
            parts.append("I'm still planning your request")
        else:
            parts.append(f"{TYPE_NAMES[job.type]} {STATE_PHRASES[job.state]}")
    if len(parts) > 1:
        parts[-1] = "and " + parts[-1]
    sentence = (", " if len(parts) > 2 else " ").join(parts)
    return sentence[0].upper() + sentence[1:] + "."


@router.post("/get_status")
async def get_status(body: CallContext, conn: Conn) -> Reply:
    body.authorize()
    open_jobs = await jobs.list_jobs(conn)
    listed = [
        {"id": j.id, "type": j.type, "state": j.state, "summary": j.summary} for j in open_jobs
    ]
    return Reply(ok=True, message=status_sentence(open_jobs), jobs=listed)


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
            conn, job_id, new_state, summary=summary, note=note, expect=JobState.NEEDS_APPROVAL
        )
    except JobNotFound:
        return Reply(ok=False, message="I couldn't find that job.", job_id=job_id)
    except IllegalTransition:
        return Reply(ok=False, message="That one isn't waiting for your OK.", job_id=job_id)
    log.info("approve_action: job %s -> %s", job.id, job.state)
    message = "Done, going ahead." if body.approved else "OK, cancelled."
    return Reply(ok=True, message=message, job_id=job.id)
