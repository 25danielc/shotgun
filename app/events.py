"""POST /events: the car-as-sensor entry point, plus the call trigger.

Build steps: 1.5 (/events rings the phone, wrong secret -> 401), 4.3 (call policy, stretch).

Contract (source-agnostic so other triggers can be added later):
    POST /events
    Header: X-Shotgun-Secret: <EVENTS_SHARED_SECRET>
    Body:   {"source": "ios_shortcut", "event": "carplay_connected" | "carplay_disconnected",
             "location": {"lat": float, "lng": float} | null}

Rules:
- Reject a missing or wrong secret with 401 (constant-time compare).
- On carplay_connected: compute the ETA in the background (app/eta.py), then ring via
  app/telephony.py with a greeting like "Morning. 31-minute drive home. Anything you want handled?"
- Respond fast; never make the Shortcut wait on the ETA or the call.
- Demo policy: always call. Cooldown, drive >= 10 min and pending items are step 4.3 (stretch).
"""

from fastapi import APIRouter

router = APIRouter()
