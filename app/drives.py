"""Drives: one row per trip, from plug-in to unplug, and the calls placed during it (D17).
Every job belongs to a drive.

Build steps: 2.2 (table; dispatch_task attaches jobs to the current drive), 4.1 (arrival call),
4.3 (departure call policy), 4.4 (unplug closes the drive), 5.1 (destination and ETA).

Columns:
    started_at / ended_at     plug-in and unplug (ended_at null while the drive is open)
    start_lat / start_lng     where the plug-in Shortcut said the phone was (step 5.1)
    destination / eta         what the driver named and when Routes says they arrive (5.1)
    arrival_call_at           eta - 3 min: when the arrival call rings (4.1)
    arrival_called            true once the arrival call has been placed, or was cancelled

Rules:
- At most one open drive. Opening a drive closes any still open: the disconnect Shortcut can
  miss, and a stale drive must not swallow the next trip's jobs.
- A drive older than MAX_HOURS counts as closed even if no unplug arrived.
- A dispatch with no open drive (e.g. the driver tapped the contact instead of plugging in)
  opens one, so every job has a drive.

`calls` logs every outbound call we place (kind departure | arrival | exception), so the rules
"at most one exception call per 10 min" and "not called in the last 30 min" (4.3) survive a
restart, and tests can count calls per drive.

Functions take an open psycopg AsyncConnection, like app/jobs.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from psycopg import AsyncConnection
from psycopg.rows import class_row
from pydantic import BaseModel

MAX_HOURS = 3

SCHEMA = """
create table if not exists drives (
    id               bigint generated always as identity primary key,
    source           text not null default 'carplay',
    started_at       timestamptz not null default now(),
    ended_at         timestamptz,
    start_lat        double precision,
    start_lng        double precision,
    destination      text,
    eta              timestamptz,
    arrival_call_at  timestamptz,
    arrival_called   boolean not null default false
);
create index if not exists drives_open_idx on drives (started_at desc) where ended_at is null;
create table if not exists calls (
    id               bigint generated always as identity primary key,
    drive_id         bigint references drives (id),
    kind             text not null check (kind in ('departure', 'arrival', 'exception')),
    conversation_id  text,
    job_ids          bigint[] not null default '{}',
    at               timestamptz not null default now()
);
create index if not exists calls_at_idx on calls (at desc);
"""
CALL_KINDS = ("departure", "arrival", "exception")


class Drive(BaseModel):
    """One row of `drives`. Extra columns added by later steps are ignored."""

    id: int
    source: str
    started_at: datetime
    ended_at: datetime | None
    start_lat: float | None
    start_lng: float | None
    destination: str | None
    eta: datetime | None
    arrival_call_at: datetime | None
    arrival_called: bool


async def get_drive(conn: AsyncConnection, drive_id: int) -> Drive | None:
    cur = conn.cursor(row_factory=class_row(Drive))
    await cur.execute("select * from drives where id = %s", (drive_id,))
    return await cur.fetchone()


async def current_drive(conn: AsyncConnection, now: datetime | None = None) -> Drive | None:
    """The open drive, if one started within MAX_HOURS."""
    cutoff = (now or datetime.now(UTC)) - timedelta(hours=MAX_HOURS)
    cur = conn.cursor(row_factory=class_row(Drive))
    await cur.execute(
        """select * from drives where ended_at is null and started_at > %s
           order by started_at desc, id desc limit 1""",
        (cutoff,),
    )
    return await cur.fetchone()


async def open_drive(
    conn: AsyncConnection,
    *,
    lat: float | None = None,
    lng: float | None = None,
    source: str = "carplay",
    now: datetime | None = None,
) -> Drive:
    """Start a drive, closing any that is still open."""
    now = now or datetime.now(UTC)
    async with conn.transaction():
        await conn.execute("update drives set ended_at = %s where ended_at is null", (now,))
        cur = conn.cursor(row_factory=class_row(Drive))
        await cur.execute(
            """insert into drives (source, started_at, start_lat, start_lng)
               values (%s, %s, %s, %s) returning *""",
            (source, now, lat, lng),
        )
        return await cur.fetchone()


async def current_or_open(
    conn: AsyncConnection, *, source: str = "call", now: datetime | None = None
) -> Drive:
    """The open drive, or a new one (a call with no plug-in still gets a drive)."""
    return await current_drive(conn, now) or await open_drive(conn, source=source, now=now)


async def record_call(
    conn: AsyncConnection,
    kind: str,
    *,
    drive_id: int | None,
    conversation_id: str | None,
    job_ids: list[int] | None = None,
    now: datetime | None = None,
) -> None:
    await conn.execute(
        "insert into calls (drive_id, kind, conversation_id, job_ids, at) "
        "values (%s, %s, %s, %s, %s)",
        (drive_id, kind, conversation_id, job_ids or [], now or datetime.now(UTC)),
    )


async def last_call_at(conn: AsyncConnection, kind: str | None = None) -> datetime | None:
    """When we last rang the driver (of this kind, or any)."""
    cur = await conn.execute(
        "select max(at) from calls where %s::text is null or kind = %s", (kind, kind)
    )
    return (await cur.fetchone())[0]


async def calls_for(conn: AsyncConnection, drive_id: int) -> list[str]:
    """The kinds of call placed during a drive, in order."""
    cur = await conn.execute("select kind from calls where drive_id = %s order by id", (drive_id,))
    return [row[0] for row in await cur.fetchall()]
