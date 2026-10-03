"""POST /events: the car-as-sensor entry point, plus the departure call.

Build steps: 1.5 (/events rings the phone, wrong secret -> 401), 4.3 (departure call policy,
core since D17), 4.4 (unplug recap), 5.1 (the plug-in location feeds the ETA).

Contract (source-agnostic so other triggers can be added later):
    POST /events
    Header: X-Shotgun-Secret: <EVENTS_SHARED_SECRET>
    Body:   {"source": "ios_shortcut", "event": "carplay_connected" | "carplay_disconnected",
             "location": {"lat": float, "lng": float} | null}
    -> 202 {"accepted": true, "calling": bool}

Rules:
- Missing or wrong secret -> 401 (constant-time compare). Secret not configured -> 503, never
  open.
- carplay_connected: open a drive with the location (closing any still open), ask the call
  policy (app/policy.py) whether to ring, answer, then ring in the background via
  app/telephony.py. The Shortcut never waits on the call. Without a database: always ring
  (the step 1.5 behaviour), with no drive.
- carplay_disconnected (alias car_disconnected): close the drive, which cancels its arrival
  call, and push the recap via ntfy in the background (app/recap.py, step 4.4).
- The departure greeting leads with facts, never "How can I help?": what's waiting on the
  driver, if anything (and its question goes in pending_job_id). Otherwise it asks "Where are
  you headed?"; the agent sends the answer to set_destination for the ETA (step 5.1), and asks
  it after the pending question when there is one.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status
from psycopg.rows import class_row
from pydantic import BaseModel, ValidationError

from app import calls, db, drives, jobs, policy, recap, telephony
from app.config import settings
from app.jobs import Job
from app.security import check_secret

log = logging.getLogger(__name__)
router = APIRouter()

CONNECTED = "carplay_connected"
DISCONNECTED = {"carplay_disconnected", "car_disconnected"}  # the D17 spec's name is an alias
GREETING = "Hey, it's Shotgun. Where are you headed?"  # step 5.1: the answer sets the ETA


class Location(BaseModel):
    lat: float
    lng: float


class Event(BaseModel):
    source: str
    event: str
    location: Location | None = None


def departure_variables(drive: drives.Drive | None, waiting: list[Job]) -> dict[str, str]:
    """Greeting with what's waiting on the driver; at most one question (asked last)."""
    greeting, summary, question = GREETING, "", None
    if waiting:
        question = waiting[0]
        others = len(waiting) - 1
        summary = calls.job_sentence(question)
        if others:
            summary = f"{others} more thing{'s' if others > 1 else ''} waiting too. {summary}"
        greeting = f"Hey, it's Shotgun. {summary}"
    return telephony.call_variables(
        greeting,
        summary=summary,
        pending_job_id=question.id if question else None,
        drive_id=drive.id if drive else None,
        call_kind="departure",
    )


async def waiting_jobs(conn) -> list[Job]:
    cur = conn.cursor(row_factory=class_row(Job))
    await cur.execute(
        "select * from jobs where state = any(%s) order by updated_at, id",
        ([state.value for state in jobs.WAITING],),
    )
    return await cur.fetchall()


async def plan_departure(event: Event) -> tuple[bool, dict[str, str], drives.Drive | None]:
    """Open the drive and decide. Returns (call?, call variables, drive)."""
    pool = db.get_pool()
    if pool is None:
        return True, departure_variables(None, []), None
    now = datetime.now(UTC)
    loc = event.location
    async with pool.connection() as conn:
        drive = await drives.open_drive(
            conn,
            lat=loc.lat if loc else None,
            lng=loc.lng if loc else None,
            source=event.source,
            now=now,
        )
        waiting = await waiting_jobs(conn)
        last = await drives.last_call_at(conn)
    decision = policy.departure(now=now, pending=len(waiting), eta_minutes=None, last_call_at=last)
    log.info(
        "plug-in: drive %s, %s call (%s)",
        drive.id,
        "ringing" if decision.call else "no",
        decision.reason,
    )
    return decision.call, departure_variables(drive, waiting), drive


async def ring(variables: dict[str, str], drive: drives.Drive | None) -> None:
    """Background task: the departure call. Errors are logged, never raised to the Shortcut."""
    try:
        placed = await telephony.place_call(variables)
    except telephony.CallError as exc:
        log.error("plug-in call failed: %s", exc)
        return
    log.info("plug-in call placed: conversation %s", placed.conversation_id)
    pool = db.get_pool()
    if pool is None or drive is None:
        return
    try:
        async with pool.connection() as conn:
            await drives.record_call(
                conn, "departure", drive_id=drive.id, conversation_id=placed.conversation_id
            )
    except Exception:
        log.exception("plug-in: could not log the departure call")


async def end_drive() -> str | None:
    """Close the open drive; the recap text for it, or None (no drive, no jobs, no database)."""
    pool = db.get_pool()
    if pool is None:
        return None
    async with pool.connection() as conn:
        drive = await drives.close_drive(conn)
        if drive is None:
            return None
        found = await calls.drive_jobs(conn, drive.id)
    log.info("unplug: drive %s closed with %d job(s)", drive.id, len(found))
    return recap.recap_text(found)


async def send_recap(text: str) -> None:
    if await recap.push(text):
        log.info("unplug: recap pushed")


@router.post("/events", status_code=status.HTTP_202_ACCEPTED)
async def receive_event(
    request: Request,
    background: BackgroundTasks,
    x_shotgun_secret: str | None = Header(default=None),
) -> dict[str, bool]:
    # Secret first, body second: unauthenticated callers get 401, never a schema error.
    check_secret(x_shotgun_secret, settings.events_shared_secret)
    try:
        event = Event.model_validate_json(await request.body())
    except ValidationError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, exc.errors(include_url=False)
        ) from exc
    log.info("event %s from %s (location %s)", event.event, event.source, bool(event.location))
    calling = False
    if event.event == CONNECTED:
        try:
            calling, variables, drive = await plan_departure(event)
        except Exception:
            log.exception("plug-in: drive/policy failed; ringing anyway")
            calling, variables, drive = True, departure_variables(None, []), None
        if calling:
            background.add_task(ring, variables, drive)
    elif event.event in DISCONNECTED:
        try:
            text = await end_drive()
        except Exception:
            log.exception("unplug: could not close the drive")
            text = None
        if text:
            background.add_task(send_recap, text)
    return {"accepted": True, "calling": calling}
