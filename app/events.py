"""POST /events: the car-as-sensor entry point, plus the call trigger.

Build steps: 1.5 (/events rings the phone, wrong secret -> 401), 4.3 (call policy, stretch).

Contract (source-agnostic so other triggers can be added later):
    POST /events
    Header: X-Shotgun-Secret: <EVENTS_SHARED_SECRET>
    Body:   {"source": "ios_shortcut", "event": "carplay_connected" | "carplay_disconnected",
             "location": {"lat": float, "lng": float} | null}
    -> 202 {"accepted": true, "calling": bool}

Rules:
- Missing or wrong secret -> 401 (constant-time compare). Secret not configured -> 503, never
  open.
- carplay_connected: respond immediately, then ring in the background via app/telephony.py.
  The Shortcut never waits on the ETA or the call.
- Demo policy: always call. Cooldown, drive >= 10 min and pending items are step 4.3 (stretch).
- The greeting gets the drive time once ETA (step 5.1) exists:
  "Morning. 31-minute drive home. Anything you want handled?"
- TODO(step 5.1): store the latest trip (location, time) so the ETA and the planner can use it.
  Today the location is validated and logged, then dropped (docs/DECISIONS.md section 9.12).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status
from pydantic import BaseModel, ValidationError

from app import db, drives, telephony
from app.config import settings
from app.security import check_secret

log = logging.getLogger(__name__)
router = APIRouter()

CONNECTED = "carplay_connected"


class Location(BaseModel):
    lat: float
    lng: float


class Event(BaseModel):
    source: str
    event: str
    location: Location | None = None


def greeting_for(event: Event) -> str:
    return "Hey, it's Shotgun, riding along."  # D17: no "How can I help?"; 4.3 adds the facts


async def start_drive(event: Event) -> drives.Drive | None:
    """Open a drive for this plug-in (closing any still open). None without a database."""
    pool = db.get_pool()
    if pool is None:
        return None
    loc = event.location
    try:
        async with pool.connection() as conn:
            return await drives.open_drive(
                conn,
                lat=loc.lat if loc else None,
                lng=loc.lng if loc else None,
                source=event.source,
            )
    except Exception:
        log.exception("plug-in: could not open a drive")
        return None


async def log_call(drive: drives.Drive | None, conversation_id: str | None) -> None:
    pool = db.get_pool()
    if pool is None or drive is None:
        return
    try:
        async with pool.connection() as conn:
            await drives.record_call(
                conn, "departure", drive_id=drive.id, conversation_id=conversation_id
            )
    except Exception:
        log.exception("plug-in: could not log the departure call")


async def ring_on_plug_in(event: Event) -> None:
    """Background task: open a drive, then ring the car (the departure call).

    Errors are logged, never raised to the Shortcut.
    """
    drive = await start_drive(event)
    variables = telephony.call_variables(
        greeting_for(event), drive_id=drive.id if drive else None, call_kind="departure"
    )
    try:
        placed = await telephony.place_call(variables)
    except telephony.CallError as exc:
        log.error("plug-in call failed: %s", exc)
        return
    log.info("plug-in call placed: conversation %s", placed.conversation_id)
    await log_call(drive, placed.conversation_id)


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
    calling = event.event == CONNECTED
    if calling:
        background.add_task(ring_on_plug_in, event)
    return {"accepted": True, "calling": calling}
