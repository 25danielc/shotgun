"""Callback watcher: when jobs finish or need a "yes", ring the car with a short summary.

Build steps: 4.1 (job flipped to done by hand -> phone rings and reads the summary),
4.2 (spoken approval loop). Try it by hand:
    uv run python -m app.callbacks --demo "Your pull request is ready."   # rings MY_PHONE_NUMBER

Rules:
- A job is announced when it reaches done, needs_approval or failed, once per state
  (jobs.announced_state). Plan jobs are announced only when they fail; a finished plan was
  already acknowledged on the call ("On it").
- Everything ready is batched into one call. At most one needs_approval job per call (its id goes
  in pending_job_id), and no new question while an earlier one is still unanswered.
- A job is marked announced only after ElevenLabs accepts the call; a failed call is retried on
  the next tick.
- Collision guard: at least MIN_GAP_SECONDS between callbacks so a new call can't ring into one
  still in progress. Not the step 4.3 call policy (cooldown, drive length, pending items).

Runs as a background loop in the FastAPI process (Railway keeps the container running; see
docs/DECISIONS.md D5). Calls go through app/telephony.py with the text as dynamic variables.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import time
from collections.abc import Callable

from psycopg import AsyncConnection
from psycopg.rows import class_row

from app import jobs, telephony
from app.jobs import Job, JobState, JobType

log = logging.getLogger(__name__)

POLL_SECONDS = 3.0
MIN_GAP_SECONDS = 60.0
MAX_UPDATES_PER_CALL = 3
ANNOUNCE_STATES = (JobState.DONE, JobState.NEEDS_APPROVAL, JobState.FAILED)


class Watcher:
    """One instance per process. `clock` is injectable for tests."""

    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.last_call_at: float | None = None

    def gap_ok(self) -> bool:
        return self.last_call_at is None or self.clock() - self.last_call_at >= MIN_GAP_SECONDS

    async def tick(self, conn: AsyncConnection) -> list[Job]:
        """Place at most one callback. Returns the jobs it announced (empty if none)."""
        if not self.gap_ok():
            return []
        batch = await pick_batch(conn)
        if not batch:
            return []
        variables = callback_variables(batch)
        try:
            placed = await telephony.place_call(variables)
        except telephony.CallError as exc:
            log.error("callback for jobs %s failed: %s", [j.id for j in batch], exc)
            self.last_call_at = self.clock()  # back off a full gap before retrying
            return []
        self.last_call_at = self.clock()
        await mark_announced(conn, batch)
        log.info("callback %s announced jobs %s", placed.conversation_id, [j.id for j in batch])
        return batch


async def pending_jobs(conn: AsyncConnection) -> list[Job]:
    """Jobs whose current state hasn't been announced yet, oldest change first."""
    cur = conn.cursor(row_factory=class_row(Job))
    await cur.execute(
        """select * from jobs
           where state = any(%s)
             and announced_state is distinct from state
             and not (type = 'plan' and state = 'done')
           order by updated_at, id""",
        ([s.value for s in ANNOUNCE_STATES],),
    )
    return await cur.fetchall()


async def question_outstanding(conn: AsyncConnection) -> bool:
    """True if we already asked about a job and it's still waiting for the answer."""
    cur = await conn.execute(
        "select 1 from jobs where state = 'needs_approval' "
        "and announced_state = 'needs_approval' limit 1"
    )
    return await cur.fetchone() is not None


async def pick_batch(conn: AsyncConnection) -> list[Job]:
    """Finished jobs (up to MAX_UPDATES_PER_CALL) plus at most one question, question last."""
    pending = await pending_jobs(conn)
    updates = [j for j in pending if j.state is not JobState.NEEDS_APPROVAL]
    questions = [j for j in pending if j.state is JobState.NEEDS_APPROVAL]
    batch = updates[:MAX_UPDATES_PER_CALL]
    if questions and not await question_outstanding(conn):
        batch.append(questions[0])
    return batch


def job_sentence(job: Job) -> str:
    if job.summary:
        return job.summary.strip()
    label = job.details.get("label") or job.request or f"Job {job.id}"
    if job.state is JobState.FAILED:
        return f"{label} didn't work out."
    if job.state is JobState.NEEDS_APPROVAL:
        return f"{label} is ready. Should I go ahead?"
    return f"{label} is done."


def callback_variables(batch: list[Job]) -> dict[str, str]:
    """Dynamic variables for the call: the greeting is what the car says first."""
    sentences = [job_sentence(job) for job in batch]
    question = next((j for j in batch if j.state is JobState.NEEDS_APPROVAL), None)
    greeting = "Shotgun here. " + " ".join(sentences)
    return telephony.call_variables(
        greeting,
        summary=" ".join(sentences),
        pending_job_id=question.id if question else None,
    )


async def mark_announced(conn: AsyncConnection, batch: list[Job]) -> None:
    """Record the announced state, unless the job moved on while we were calling."""
    async with conn.transaction():
        for job in batch:
            await jobs.set_announced(conn, job.id, job.state)


async def run_callbacks(pool) -> None:
    """Background loop started by the app lifespan."""
    watcher = Watcher()
    while True:
        try:
            async with pool.connection() as conn:
                await watcher.tick(conn)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("callback loop error")
        await asyncio.sleep(POLL_SECONDS)


async def _demo(summary: str) -> None:
    """Step 4.1 by hand: make a job, flip it to done with a summary, run one tick."""
    from app import db

    pool = await db.open_pool()
    try:
        async with pool.connection() as conn:
            await jobs.init_schema(conn)
            job = await jobs.create_job(conn, JobType.CODER, request="step 4.1 demo", source="demo")
            await jobs.transition(conn, job.id, JobState.RUNNING)
            await jobs.transition(conn, job.id, JobState.DONE, summary=summary, note="by hand")
            announced = await Watcher().tick(conn)
        print(f"job {job.id}: {'calling now' if announced else 'no call placed (see log)'}")
    finally:
        await db.close_pool()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Step 4.1 demo: flip a job to done and ring.")
    parser.add_argument("--demo", metavar="SUMMARY", required=True)
    asyncio.run(_demo(parser.parse_args().demo))
