"""Demo tooling (step 4.5, D17): simulate the plug-in, fire the arrival call now, watch it live.

    make demo-call                # POST /events carplay_connected to BASE (default PUBLIC_BASE_URL)
    make demo-arrive              # the open drive's arrival call rings within ~3 s
    make watch                    # live terminal view of the drive, its jobs and calls

demo-call goes through the real /events endpoint with EVENTS_SHARED_SECRET, so the call policy
(step 4.3) applies; set CALL_POLICY=always on the server for rehearsals. demo-arrive sets
`arrival_call_at = now` on the open drive in DATABASE_URL; the deployed app's call loop
(app/calls.py) does the ringing, by the usual rules (a drive with no jobs gets no call).
watch only reads. None of them print secrets.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import calls, db, drives, jobs  # noqa: E402
from app.config import settings  # noqa: E402
from app.drives import Drive  # noqa: E402
from app.jobs import Job  # noqa: E402

CLEAR = "\033[2J\033[H"
STATE_MARK = {
    "queued": "·",
    "running": "▶",
    "needs_approval": "?",
    "exception": "!",
    "approved": "✓",
    "done": "●",
    "failed": "✗",
}


# --- demo-call --------------------------------------------------------------------------------


async def demo_call(
    base: str, *, location: dict | None = None, client: httpx.AsyncClient | None = None
) -> dict[str, Any]:
    """POST the plug-in event, like the iPhone Shortcut does."""
    body = {"source": "demo", "event": "carplay_connected", "location": location}
    headers = {"X-Shotgun-Secret": settings.events_shared_secret}
    owns = client is None
    client = client or httpx.AsyncClient(timeout=10)
    try:
        response = await client.post(f"{base.rstrip('/')}/events", json=body, headers=headers)
    finally:
        if owns:
            await client.aclose()
    response.raise_for_status()
    return response.json()


# --- demo-arrive ------------------------------------------------------------------------------


async def demo_arrive(conn, now: datetime | None = None) -> tuple[Drive | None, str]:
    """Make the open drive's arrival call due now. Returns (drive, what will happen)."""
    now = now or datetime.now(UTC)
    drive = await drives.current_drive(conn, now)
    if drive is None:
        return None, "no open drive: plug in first (make demo-call)"
    if drive.arrival_called:
        return drive, f"drive {drive.id} already had its arrival call"
    await conn.execute("update drives set arrival_call_at = %s where id = %s", (now, drive.id))
    found = await calls.drive_jobs(conn, drive.id)
    if not found:
        return drive, f"drive {drive.id} has no jobs, so no arrival call (D17)"
    return drive, f"drive {drive.id}: arrival call due now with {len(found)} job(s)"


# --- watch ------------------------------------------------------------------------------------


async def snapshot(conn) -> tuple[Drive | None, list[Job], list[tuple[str, datetime]]]:
    drive = await drives.current_drive(conn)
    if drive is None:
        cur = await conn.execute("select id from drives order by id desc limit 1")
        row = await cur.fetchone()
        drive = await drives.get_drive(conn, row[0]) if row else None
    if drive is None:
        return None, [], []
    found = await jobs.list_jobs(conn, jobs.JobState, drive_id=drive.id)
    cur = await conn.execute(
        "select kind, at from calls where drive_id = %s order by id", (drive.id,)
    )
    return drive, found, await cur.fetchall()


def _clock(at: datetime | None) -> str:
    return at.astimezone(settings.tz).strftime("%H:%M:%S") if at else "-"


def render(
    drive: Drive | None,
    found: list[Job],
    placed: list[tuple[str, datetime]],
    now: datetime,
    width: int = 100,
) -> str:
    """The watch screen as text (no colours, so it reads the same in a log)."""
    if drive is None:
        return "No drives yet. Plug in (make demo-call)."
    status = "open" if drive.ended_at is None else f"closed {_clock(drive.ended_at)}"
    lines = [f"Drive {drive.id}  {status}  started {_clock(drive.started_at)}"]
    if drive.start_lat is not None:
        lines[0] += f"  at {drive.start_lat:.4f},{drive.start_lng:.4f}"
    else:
        lines[0] += "  (no location from the Shortcut)"
    where = drive.destination or "destination unknown"
    eta = f"ETA {_clock(drive.eta)}" if drive.eta else "no ETA"
    if drive.arrival_called:
        arrival = "arrival call placed"
    elif drive.arrival_call_at:
        left = int((drive.arrival_call_at - now).total_seconds())
        arrival = f"arrival call {_clock(drive.arrival_call_at)} ({max(left, 0)} s)"
    else:
        arrival = "arrival call when all jobs settle"
    lines.append(f"  {where} · {eta} · {arrival}")
    lines.append("")
    lines.append(f"Jobs ({len(found)})")
    for job in found:
        mark = STATE_MARK.get(job.state.value, " ")
        label = calls.label_of(job)
        pre = " [pre-approved]" if job.preapproval else ""
        line = f"  {mark} #{job.id:<4} {job.type.value:<8} {job.state.value:<14} {label}{pre}"
        lines.append(line[:width])
        if job.summary:
            lines.append(f"      {job.summary}"[:width])
    if not found:
        lines.append("  (none)")
    lines.append("")
    calls_text = ", ".join(f"{kind} {_clock(at)}" for kind, at in placed) or "none"
    lines.append(f"Calls: {calls_text}")
    return "\n".join(lines)


async def watch(interval: float) -> None:
    pool = await db.open_pool()
    try:
        while True:
            async with pool.connection() as conn:
                screen = render(*await snapshot(conn), now=datetime.now(UTC))
            print(CLEAR + screen + f"\n\n(refreshing every {interval:g} s, Ctrl-C to stop)")
            await asyncio.sleep(interval)
    finally:
        await db.close_pool()


async def _arrive() -> None:
    pool = await db.open_pool()
    try:
        async with pool.connection() as conn:
            _, message = await demo_arrive(conn)
        print(message)
    finally:
        await db.close_pool()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    call = sub.add_parser("call", help="simulate the plug-in")
    call.add_argument("--base", default=settings.public_base_url or "http://localhost:8000")
    sub.add_parser("arrive", help="fire the arrival call now")
    live = sub.add_parser("watch", help="live view of the drive and jobs")
    live.add_argument("--interval", type=float, default=2.0)
    args = parser.parse_args()
    try:
        if args.command == "call":
            reply = asyncio.run(demo_call(args.base))
            print(f"{args.base}/events -> {reply}")
        elif args.command == "arrive":
            asyncio.run(_arrive())
        else:
            asyncio.run(watch(args.interval))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
