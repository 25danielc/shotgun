"""Arrival and exception calls: the only outbound calls after the departure call (D17).

Build step 4.1. Replaces the per-job callback watcher (app/callbacks.py, deleted): the driver
gets at most two calls per drive (departure on plug-in, arrival before parking) plus rare
exception calls. Pass check: a drive with 2 pre-approved jobs makes exactly 2 outbound calls.

Each tick (every POLL_SECONDS, one loop in the app process):
1. Unclaimed guard: a job still queued after UNCLAIMED_MINUTES has no worker running for its
   type (a stub worker, or the Mac food worker offline). It fails with a spoken reason, so it
   shows up in the arrival summary instead of hanging silently.
2. Busy-line guard: nothing rings while the agent is on a call (telephony.call_in_progress).
3. Arrival call, once per drive with at least one job:
   - at drive.arrival_call_at (ETA - 3 min, set by step 5.1), or
   - when the ETA is unknown, once every job in the drive is terminal or held for a yes.
   The call reads a batched summary: done, failed, still running, and at most one question
   (pending_job_id; an exception before a needs_approval). A drive with no jobs gets no call.
4. Otherwise an exception call, for one `exception` job in an open drive that hasn't been
   announced, at most one per EXCEPTION_GAP_SECONDS across all drives. Anything else waits for
   the arrival call. (Every `exception` job has an irreversible action pending: that's how it
   got there, see app/approvals.py.)
Nothing rings after the arrival call; later results go in the unplug recap (step 4.4).

Every call is logged in `calls` (app/drives.py). Calls go through app/telephony.py with the
text in dynamic variables: greeting (said first), summary, pending_job_id, drive_id, call_kind.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from psycopg import AsyncConnection
from psycopg.rows import class_row

from app import drives, jobs, phrasing, telephony
from app.config import settings
from app.drives import Drive
from app.jobs import Job, JobState, JobType

log = logging.getLogger(__name__)

POLL_SECONDS = 3.0
RETRY_SECONDS = 60.0  # after ElevenLabs refuses a call
FAILED = "failed"
UNCLAIMED_MINUTES = 2
EXCEPTION_GAP_SECONDS = 10 * 60
QUESTION_ORDER = (JobState.EXCEPTION, JobState.NEEDS_APPROVAL)


def label_of(job: Job) -> str:
    return job.details.get("label") or job.request or f"job {job.id}"


def lower_first(text: str) -> str:
    text = text.strip().rstrip(".")
    return text[:1].lower() + text[1:]


def job_sentence(job: Job) -> str:
    """What the driver hears about one job."""
    if job.state in (JobState.DONE, JobState.FAILED, *jobs.WAITING) and job.summary:
        return job.summary.strip()
    label = label_of(job)
    return {
        JobState.DONE: f"{label} is done.",
        JobState.FAILED: f"{label} didn't work out.",
        JobState.NEEDS_APPROVAL: f"{label} is ready. Should I go ahead?",
        JobState.EXCEPTION: f"{label} needs your OK.",
        JobState.APPROVED: f"{label} is going through now.",
    }.get(job.state, f"{label} is still in progress.")


def spoken(job: Job) -> bool:
    """A finished plan job was already acknowledged on the call; its parts speak for it."""
    return not (job.type is JobType.PLAN and job.state is JobState.DONE)


def arrival_batch(drive_jobs: list[Job]) -> tuple[list[Job], Job | None]:
    """(jobs to mention, in order, question last; the one job asked about).

    The question is a job the driver hasn't been asked about yet if there is one (an exception
    held back by the 10-minute gap), exceptions before plain holds. A question already asked on
    an exception call and not answered is still read out, just not asked again first.
    """
    heard = [j for j in drive_jobs if spoken(j)]
    waiting = [j for j in heard if j.state in jobs.WAITING]
    question = min(
        waiting,
        key=lambda j: (j.announced_state == j.state, QUESTION_ORDER.index(j.state), j.id),
        default=None,
    )
    order = {JobState.DONE: 0, JobState.FAILED: 1}
    rest = sorted(
        (j for j in heard if j is not question), key=lambda j: (order.get(j.state, 2), j.id)
    )
    return rest + ([question] if question else []), question


FACT_STATUS = {JobState.DONE: "done", JobState.FAILED: "didn't work"}


def facts_for(batch: list[Job], question: Job | None) -> list[phrasing.Fact]:
    """What the arrival call reports, minus the question (asked last, separately)."""
    facts = []
    for job in batch:
        if job is question:
            continue
        if job.state in jobs.WAITING:
            status = "waiting on you"
        else:
            status = FACT_STATUS.get(job.state, "still going")
        facts.append(phrasing.Fact(status, job_sentence(job)))
    return facts


def minutes_left(drive: Drive, now: datetime) -> int | None:
    """Minutes to the ETA, for "you're about 3 minutes out". None without an ETA (the call is
    then the "everything's settled" fallback and must not claim the driver is arriving)."""
    if drive.eta is None:
        return None
    return max(0, round((drive.eta - now).total_seconds() / 60))


async def arrival_variables(
    drive: Drive, batch: list[Job], question: Job | None, now: datetime | None = None
) -> dict[str, str]:
    """The arrival call: a natural greeting (app/phrasing.py) plus the plain facts as summary."""
    sentences = " ".join(job_sentence(job) for job in batch)
    asked = job_sentence(question) if question else None
    left = minutes_left(drive, now or datetime.now(UTC))
    greeting = await phrasing.arrival_greeting(facts_for(batch, question), asked, left)
    return telephony.call_variables(
        greeting,
        summary=sentences,
        eta_minutes=left,
        pending_job_id=question.id if question else None,
        drive_id=drive.id,
        call_kind="arrival",
    )


async def drive_jobs(conn: AsyncConnection, drive_id: int) -> list[Job]:
    return await jobs.list_jobs(conn, jobs.JobState, drive_id=drive_id)


def arrival_due(drive: Drive, drive_jobs: list[Job], now: datetime) -> bool:
    if drive.arrival_called or not drive_jobs:
        return False
    if drive.arrival_call_at is not None:
        return now >= drive.arrival_call_at
    settled = jobs.TERMINAL | jobs.WAITING
    return all(job.state in settled for job in drive_jobs)


async def open_drives(conn: AsyncConnection, now: datetime) -> list[Drive]:
    cutoff = now - timedelta(hours=drives.MAX_HOURS)
    cur = conn.cursor(row_factory=class_row(Drive))
    await cur.execute(
        "select * from drives where ended_at is null and started_at > %s order by id", (cutoff,)
    )
    return await cur.fetchall()


async def mark_announced(conn: AsyncConnection, batch: list[Job]) -> None:
    async with conn.transaction():
        for job in batch:
            await jobs.set_announced(conn, job.id, job.state)


async def place(
    conn: AsyncConnection,
    kind: str,
    variables: dict[str, str],
    drive_id: int,
    batch: list[Job],
    now: datetime,
) -> bool:
    """Ring, then log the call and mark what was said. False if ElevenLabs refused."""
    try:
        # Arrival and exception calls use the callback agent: same prompt and tools, but a 10 s
        # silence timer, so "If you don't have anything else, I'm going to hang up" means it.
        placed = await telephony.place_call(
            variables, agent_id=settings.elevenlabs_callback_agent_id or None
        )
    except telephony.CallError as exc:
        log.error("%s call for drive %s failed: %s", kind, drive_id, exc)
        return False
    async with conn.transaction():
        await drives.record_call(
            conn,
            kind,
            drive_id=drive_id,
            conversation_id=placed.conversation_id,
            job_ids=[job.id for job in batch],
            now=now,
        )
        if kind == "arrival":
            await conn.execute("update drives set arrival_called = true where id = %s", (drive_id,))
        await mark_announced(conn, batch)
    log.info(
        "%s call %s, drive %s, jobs %s",
        kind,
        placed.conversation_id,
        drive_id,
        [job.id for job in batch],
    )
    return True


async def exception_job(conn: AsyncConnection) -> Job | None:
    """The oldest unannounced exception job in an open drive."""
    cur = conn.cursor(row_factory=class_row(Job))
    await cur.execute(
        """select j.* from jobs j join drives d on d.id = j.drive_id
           where j.state = 'exception' and j.announced_state is distinct from 'exception'
             and d.ended_at is null and not d.arrival_called
           order by j.updated_at, j.id limit 1"""
    )
    return await cur.fetchone()


async def tick(conn: AsyncConnection, now: datetime | None = None) -> str | None:
    """Place at most one call. Returns its kind, FAILED if ElevenLabs refused it, or None."""
    now = now or datetime.now(UTC)
    await expire_unclaimed(conn, now)
    due = []
    for drive in await open_drives(conn, now):
        found = await drive_jobs(conn, drive.id)
        if arrival_due(drive, found, now):
            due.append((drive, found))
    pending_exception = None
    if not due:
        last = await drives.last_call_at(conn, "exception")
        if last is None or (now - last).total_seconds() >= EXCEPTION_GAP_SECONDS:
            pending_exception = await exception_job(conn)
    if not due and pending_exception is None:
        return None
    if await telephony.call_in_progress():
        return None  # the driver is on a call; try again next tick
    if due:
        drive, found = due[0]
        batch, question = arrival_batch(found)
        placed = await place(
            conn,
            "arrival",
            await arrival_variables(drive, batch, question, now),
            drive.id,
            batch,
            now,
        )
        return "arrival" if placed else FAILED
    job = pending_exception
    variables = telephony.call_variables(
        f"Hey, quick one. {job_sentence(job)}",
        summary=job_sentence(job),
        pending_job_id=job.id,
        drive_id=job.drive_id,
        call_kind="exception",
    )
    placed = await place(conn, "exception", variables, job.drive_id, [job], now)
    return "exception" if placed else FAILED


async def expire_unclaimed(conn: AsyncConnection, now: datetime | None = None) -> list[Job]:
    """Fail jobs nobody claimed within UNCLAIMED_MINUTES, with a spoken reason."""
    cutoff = (now or datetime.now(UTC)) - timedelta(minutes=UNCLAIMED_MINUTES)
    cur = conn.cursor(row_factory=class_row(Job))
    await cur.execute(
        "select * from jobs where state = 'queued' and updated_at < %s order by id", (cutoff,)
    )
    expired = []
    for job in await cur.fetchall():
        try:
            expired.append(
                await jobs.transition(
                    conn,
                    job.id,
                    JobState.FAILED,
                    summary=f"I can't do that one yet: {lower_first(label_of(job))}.",
                    error=f"no {job.type} worker claimed it within {UNCLAIMED_MINUTES} minutes",
                    expect=JobState.QUEUED,
                )
            )
        except jobs.IllegalTransition:
            pass  # a worker claimed it just now
    return expired


async def run_calls(pool) -> None:
    """Background loop started by the app lifespan."""
    while True:
        outcome = None
        try:
            async with pool.connection() as conn:
                outcome = await tick(conn)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("call loop error")
        await asyncio.sleep(RETRY_SECONDS if outcome == FAILED else POLL_SECONDS)
