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

from app import telephony
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
    return "Hey, it's Shotgun. Anything you want handled?"


async def ring_on_plug_in(event: Event) -> None:
    """Background task: ring the car. Errors are logged, never raised to the Shortcut."""
    try:
        placed = await telephony.place_call(telephony.call_variables(greeting_for(event)))
        log.info("plug-in call placed: conversation %s", placed.conversation_id)
    except telephony.CallError as exc:
        log.error("plug-in call failed: %s", exc)


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
