"""Job table on Neon Postgres and its state machine. Shared state for every agent.

Build step 2.1: create job; legal transitions pass; illegal ones raise. Step 4.2 (D17) adds
pre-approval and the `exception` state.

States:
    queued -> running -> needs_approval -> approved -> done   (yes asked on the arrival call)
                     \\-> approved -> done                    (pre-approved: yes given at dispatch)
                     \\-> exception -> approved -> done       (result broke the pre-approval)
                     \\-> done        (read-only jobs, e.g. research)
    any non-terminal state -> failed (a spoken "no" also ends here, summary "Cancelled")
done and failed are terminal. Every path to an irreversible action goes through approved, and
approved is reached only by a spoken yes: approve_action (from needs_approval or exception), or
running -> approved, which transition() allows only for a job that carries a preapproval.

Job types: the four worker types, plus `plan`, a raw spoken request stored by dispatch_task that
the orchestrator (step 2.4) claims and turns into worker jobs.

Each job belongs to a drive (`drive_id`, app/drives.py) and may carry a `preapproval` given at
dispatch: {condition, require_tests_pass?, max_usd?} (D17). Only the structured fields are
enforced by code; `condition` is what the driver agreed to, read back to them.

Every state change goes through `transition()`, which locks the row, checks the edge, and logs it
in job_events (the approval log for step 4.2). Workers claim work with `claim_next()`
(SELECT ... FOR UPDATE SKIP LOCKED), so the local Mac food worker and the Railway process can
share the table without an agent-to-agent protocol.

Functions take an open psycopg AsyncConnection (see app/db.py) and use `conn.transaction()`, so
they commit on an autocommit pool connection and nest as savepoints inside a test transaction.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import class_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from app import drives


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    NEEDS_APPROVAL = "needs_approval"
    EXCEPTION = "exception"  # the result broke its pre-approval; waiting for a spoken yes or no
    APPROVED = "approved"
    DONE = "done"
    FAILED = "failed"


class JobType(StrEnum):
    PLAN = "plan"  # a raw spoken request; the orchestrator (step 2.4) turns it into worker jobs
    CODER = "coder"
    EMAIL = "email"
    FOOD = "food"
    RESEARCH = "research"


WORKER_TYPES = frozenset(JobType) - {JobType.PLAN}


TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.QUEUED: frozenset({JobState.RUNNING, JobState.FAILED}),
    JobState.RUNNING: frozenset(
        {
            JobState.NEEDS_APPROVAL,
            JobState.EXCEPTION,
            JobState.APPROVED,  # pre-approved jobs only, enforced in transition()
            JobState.DONE,
            JobState.FAILED,
        }
    ),
    JobState.NEEDS_APPROVAL: frozenset({JobState.APPROVED, JobState.FAILED}),
    JobState.EXCEPTION: frozenset({JobState.APPROVED, JobState.FAILED}),
    JobState.APPROVED: frozenset({JobState.DONE, JobState.FAILED}),
    JobState.DONE: frozenset(),
    JobState.FAILED: frozenset(),
}
TERMINAL = frozenset(state for state, nexts in TRANSITIONS.items() if not nexts)
OPEN = frozenset(JobState) - TERMINAL
WAITING = frozenset({JobState.NEEDS_APPROVAL, JobState.EXCEPTION})  # held for a spoken yes or no


class IllegalTransition(ValueError):
    def __init__(self, current: JobState, new: JobState):
        super().__init__(f"illegal job transition {current} -> {new}")
        self.current, self.new = current, new


class JobNotFound(LookupError):
    pass


def check_transition(current: JobState | str, new: JobState | str) -> None:
    """Raise IllegalTransition unless current -> new is an edge of the state machine."""
    current, new = JobState(current), JobState(new)
    if new not in TRANSITIONS[current]:
        raise IllegalTransition(current, new)


class Job(BaseModel):
    """One row of `jobs`. Extra columns added by later steps are ignored."""

    id: int
    type: JobType
    state: JobState
    request: str | None
    details: dict[str, Any]
    summary: str | None
    result: dict[str, Any] | None
    error: str | None
    deadline: datetime | None
    source: str
    created_at: datetime
    updated_at: datetime
    drive_id: int | None = None
    preapproval: dict[str, Any] | None = None
    announced_state: JobState | None = None  # what the driver has heard (app/calls.py)


def _sql_list(values: Iterable[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


SCHEMA = f"""
create table if not exists jobs (
    id          bigint generated always as identity primary key,
    type        text not null,
    state       text not null default 'queued',
    request     text,
    details     jsonb not null default '{{}}'::jsonb,
    summary     text,
    result      jsonb,
    error       text,
    deadline    timestamptz,
    source      text not null default 'voice',
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);
-- Columns added by later steps (idempotent).
alter table jobs add column if not exists announced_state text;  -- step 4.1 calls
alter table jobs add column if not exists drive_id bigint references drives (id);  -- D17
alter table jobs add column if not exists preapproval jsonb;  -- D17
create index if not exists jobs_drive_idx on jobs (drive_id, id);
-- Named checks, re-applied on every init so adding a type or state updates an existing table.
alter table jobs drop constraint if exists jobs_type_check;
alter table jobs add constraint jobs_type_check check (type in ({_sql_list(JobType)}));
alter table jobs drop constraint if exists jobs_state_check;
alter table jobs add constraint jobs_state_check check (state in ({_sql_list(JobState)}));
create index if not exists jobs_open_idx on jobs (state, deadline, created_at)
    where state not in ({_sql_list(TERMINAL)});
create table if not exists job_events (
    id          bigint generated always as identity primary key,
    job_id      bigint not null references jobs (id) on delete cascade,
    from_state  text,
    to_state    text not null,
    note        text,
    at          timestamptz not null default now()
);
create index if not exists job_events_job_idx on job_events (job_id, id);
"""


async def init_schema(conn: AsyncConnection) -> None:
    """Create the tables if they don't exist. Safe to run on every deploy."""
    async with conn.transaction():
        await conn.execute(drives.SCHEMA)  # jobs.drive_id references it
        await conn.execute(SCHEMA)


async def _log(
    conn: AsyncConnection, job_id: int, from_state: str | None, to_state: str, note: str | None
) -> None:
    await conn.execute(
        "insert into job_events (job_id, from_state, to_state, note) values (%s, %s, %s, %s)",
        (job_id, from_state, to_state, note),
    )


async def create_job(
    conn: AsyncConnection,
    type: JobType | str,
    details: dict[str, Any] | None = None,
    *,
    request: str | None = None,
    source: str = "voice",
    deadline: datetime | None = None,
    drive_id: int | None = None,
    preapproval: dict[str, Any] | None = None,
) -> Job:
    """Insert a queued job and log its creation."""
    job_type = JobType(type)
    async with conn.transaction():
        cur = conn.cursor(row_factory=class_row(Job))
        await cur.execute(
            "insert into jobs (type, request, details, source, deadline, drive_id, preapproval) "
            "values (%s, %s, %s, %s, %s, %s, %s) returning *",
            (
                job_type.value,
                request,
                Jsonb(details or {}),
                source,
                deadline,
                drive_id,
                Jsonb(preapproval) if preapproval is not None else None,
            ),
        )
        job = await cur.fetchone()
        await _log(conn, job.id, None, job.state, "created")
    return job


async def get_job(conn: AsyncConnection, job_id: int) -> Job | None:
    cur = conn.cursor(row_factory=class_row(Job))
    await cur.execute("select * from jobs where id = %s", (job_id,))
    return await cur.fetchone()


async def list_jobs(
    conn: AsyncConnection,
    states: Iterable[JobState | str] | None = None,
    *,
    drive_id: int | None = None,
) -> list[Job]:
    """Jobs in the given states (default: all open jobs), oldest first, optionally one drive's."""
    wanted = [JobState(state).value for state in (states if states is not None else OPEN)]
    cur = conn.cursor(row_factory=class_row(Job))
    await cur.execute(
        """select * from jobs where state = any(%s) and (%s::bigint is null or drive_id = %s)
           order by created_at, id""",
        (wanted, drive_id, drive_id),
    )
    return await cur.fetchall()


async def transition(
    conn: AsyncConnection,
    job_id: int,
    new_state: JobState | str,
    *,
    summary: str | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    note: str | None = None,
    expect: JobState | str | Iterable[JobState | str] | None = None,
) -> Job:
    """Move a job to new_state if the state machine allows it; log the change.

    `expect` additionally requires the job to be in that state (or one of those states) right
    now: approve_action uses it so a "no" can't cancel a job that was never waiting for one.
    running -> approved is allowed only when the job carries a preapproval.
    Raises JobNotFound or IllegalTransition (the row is left untouched). summary, result and
    error overwrite their columns only when given.
    """
    new_state = JobState(new_state)
    async with conn.transaction():
        cur = await conn.execute(
            "select state, preapproval is not null from jobs where id = %s for update", (job_id,)
        )
        row = await cur.fetchone()
        if row is None:
            raise JobNotFound(job_id)
        current, preapproved = JobState(row[0]), row[1]
        if expect is not None:
            wanted = {JobState(expect)} if isinstance(expect, str) else set(map(JobState, expect))
            if current not in wanted:
                raise IllegalTransition(current, new_state)
        check_transition(current, new_state)
        if (current, new_state) == (JobState.RUNNING, JobState.APPROVED) and not preapproved:
            raise IllegalTransition(current, new_state)  # a yes must come from the driver
        cur = conn.cursor(row_factory=class_row(Job))
        await cur.execute(
            """update jobs set state = %s,
                    summary = coalesce(%s, summary),
                    result = coalesce(%s, result),
                    error = coalesce(%s, error),
                    updated_at = now()
                where id = %s returning *""",
            (
                new_state.value,
                summary,
                Jsonb(result) if result is not None else None,
                error,
                job_id,
            ),
        )
        job = await cur.fetchone()
        await _log(conn, job_id, current.value, new_state.value, note)
    return job


async def claim_next(conn: AsyncConnection, types: Iterable[JobType | str]) -> Job | None:
    """Atomically take the most urgent queued job of the given types and mark it running.

    Earliest deadline first, then oldest. SKIP LOCKED lets several workers poll at once.
    """
    wanted = [JobType(t).value for t in types]
    async with conn.transaction():
        cur = await conn.execute(
            """select id from jobs where state = 'queued' and type = any(%s)
               order by deadline nulls last, created_at, id
               limit 1 for update skip locked""",
            (wanted,),
        )
        row = await cur.fetchone()
        if row is None:
            return None
        return await transition(conn, row[0], JobState.RUNNING, note="claimed")


async def update_result(conn: AsyncConnection, job_id: int, patch: dict[str, Any]) -> Job:
    """Merge keys into a job's result without changing its state (e.g. an issue number while the
    job is still running). Raises JobNotFound."""
    cur = conn.cursor(row_factory=class_row(Job))
    await cur.execute(
        "update jobs set result = coalesce(result, '{}'::jsonb) || %s where id = %s returning *",
        (Jsonb(patch), job_id),
    )
    job = await cur.fetchone()
    if job is None:
        raise JobNotFound(job_id)
    return job


async def set_announced(conn: AsyncConnection, job_id: int, state: JobState | str) -> None:
    """Record that the driver has heard about this job in `state` (no-op if it moved on)."""
    state = JobState(state).value
    await conn.execute(
        "update jobs set announced_state = %s where id = %s and state = %s",
        (state, job_id, state),
    )


async def events(conn: AsyncConnection, job_id: int) -> list[tuple[str | None, str, str | None]]:
    """(from_state, to_state, note) for a job, in order."""
    cur = await conn.execute(
        "select from_state, to_state, note from job_events where job_id = %s order by id",
        (job_id,),
    )
    return await cur.fetchall()
