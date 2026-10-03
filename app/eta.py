"""Traffic-aware ETA from the Google Routes API, computed once at departure (D17).

Build step 5.1. The plug-in Shortcut sends the phone's location (stored on the drive as
start_lat/lng). On the departure call the agent asks "Where are you headed?" and calls the inline
tool set_destination(text) (app/voice_tools.py). That sends the answer to Routes as a free-text
address, from the plug-in location, and stores on the drive:
    destination, eta = now + duration, arrival_call_at = now + duration - ARRIVAL_LEAD.
app/calls.py then rings at arrival_call_at. There is no in-drive location, so the ETA is never
updated; unplugging cancels the arrival call (step 4.4).

Fallback: no location or no destination (or Routes fails) leaves arrival_call_at empty, and the
arrival call rings once every job is terminal or held (step 4.1).

"home" (or "my place", "the house") means HOME_ADDRESS (D16), and the agent says "home", never
the address. Calendar and trip-history destinations are out of scope (later stretch).

Routes API, checked 2026-10-03 against developers.google.com/maps/documentation/routes
(computeRoutes, Waypoint):
    POST https://routes.googleapis.com/directions/v2:computeRoutes
    headers X-Goog-Api-Key, X-Goog-FieldMask: routes.duration,routes.distanceMeters (required)
    body {origin: {location: {latLng: {latitude, longitude}}},
          destination: {address: "<free text or plus code>"},
          travelMode: "DRIVE", routingPreference: "TRAFFIC_AWARE"}
    routes[0].duration is a string like "1234s". No Geocoding API: Waypoint.address takes the text.
TRAFFIC_AWARE is the Pro SKU (5k free a month); one request per drive.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from psycopg import AsyncConnection
from psycopg.rows import class_row

from app.config import settings
from app.drives import Drive

log = logging.getLogger(__name__)

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
FIELD_MASK = "routes.duration,routes.distanceMeters"
ARRIVAL_LEAD = timedelta(minutes=3)
TIMEOUT = 6.0  # inside the 8 s inline budget
HOME_WORDS = re.compile(r"^(?:my\s+)?(?:home|house|place|the house)[.!]?$", re.IGNORECASE)


class EtaError(RuntimeError):
    pass


@dataclass(frozen=True)
class Route:
    seconds: int
    meters: int | None

    @property
    def minutes(self) -> int:
        return max(1, math.ceil(self.seconds / 60))


def resolve(text: str) -> tuple[str, str]:
    """(what the agent says, what Routes gets). "home" -> HOME_ADDRESS, spoken as "home"."""
    text = " ".join(text.split())
    if HOME_WORDS.match(text) and settings.home_address:
        return "home", settings.home_address
    return text, text


def parse_duration(value: str) -> int:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)s", value or "")
    if not match:
        raise EtaError(f"unexpected duration {value!r}")
    return round(float(match.group(1)))


async def drive_time(
    lat: float, lng: float, address: str, *, client: httpx.AsyncClient | None = None
) -> Route:
    """Traffic-aware drive time from a point to an address. Raises EtaError."""
    if not settings.google_maps_api_key:
        raise EtaError("GOOGLE_MAPS_API_KEY not set")
    body = {
        "origin": {"location": {"latLng": {"latitude": lat, "longitude": lng}}},
        "destination": {"address": address},
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
    }
    headers = {"X-Goog-Api-Key": settings.google_maps_api_key, "X-Goog-FieldMask": FIELD_MASK}
    owns = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT)
    try:
        response = await client.post(ROUTES_URL, json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise EtaError(f"Routes request failed: {type(exc).__name__}") from exc
    finally:
        if owns:
            await client.aclose()
    if response.status_code >= 400:
        raise EtaError(f"Routes HTTP {response.status_code}: {response.text[:160]}")
    routes = response.json().get("routes") or []
    if not routes:
        raise EtaError("Routes found no route")
    return Route(parse_duration(routes[0].get("duration", "")), routes[0].get("distanceMeters"))


async def set_destination(
    conn: AsyncConnection,
    drive: Drive,
    text: str,
    *,
    now: datetime | None = None,
    client: httpx.AsyncClient | None = None,
) -> tuple[Drive, str]:
    """Store the destination (and the ETA when it can be computed). Returns (drive, reply)."""
    now = now or datetime.now(UTC)
    spoken, address = resolve(text)
    route = None
    if drive.start_lat is not None and drive.start_lng is not None:
        try:
            route = await drive_time(drive.start_lat, drive.start_lng, address, client=client)
        except EtaError as exc:
            log.warning("set_destination: no ETA for drive %s: %s", drive.id, exc)
    eta = now + timedelta(seconds=route.seconds) if route else None
    call_at = max(eta - ARRIVAL_LEAD, now) if eta else None
    cur = conn.cursor(row_factory=class_row(Drive))
    await cur.execute(
        """update drives set destination = %s, eta = %s, arrival_call_at = %s
           where id = %s returning *""",
        (spoken, eta, call_at, drive.id),
    )
    drive = await cur.fetchone()
    if route is None:
        why = "I don't have your location" if drive.start_lat is None else "I couldn't get a route"
        return drive, (f"Got it, heading {to(spoken)}. {why}, so I'll call once everything's done.")
    return drive, (
        f"About {route.minutes} minutes {to(spoken)}. "
        "I'll call about 3 minutes before you get there."
    )


def to(place: str) -> str:
    return "home" if place == "home" else f"to {place}"
