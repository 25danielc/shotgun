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
the address.

A street address with no city ("333 East Jefferson") is ambiguous, and Routes picks its own
city: on 2026-10-03 it routed Daniel from Ann Arbor to 333 E Jefferson in Detroit (76 km, 53 min)
instead of Ann Arbor (4 km, 9 min). So such an address is also tried in the home area (the city
and state of HOME_ADDRESS), both requests run in parallel, and the closer one wins.
A place name with no city ("The Landmark") is sent as said, and only if Routes finds nothing is it
tried in the home area (live 2026-10-03 20:52: "The Landmark" -> no route; with ", Ann Arbor, MI"
-> 15 min). The closer-wins rule isn't used for names: "Detroit" must stay Detroit, not become a
Detroit Street nearby.
Calendar and trip-history destinations are out of scope (later stretch).

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

import asyncio
import logging
import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from psycopg import AsyncConnection
from psycopg.rows import class_row

from app.config import settings
from app.drives import Drive, near

log = logging.getLogger(__name__)

ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
FIELD_MASK = "routes.duration,routes.distanceMeters"
ARRIVAL_LEAD = timedelta(minutes=3)
TIMEOUT = 6.0  # inside the 8 s inline budget
HOME_WORDS = re.compile(r"^(?:my\s+)?(?:home|house|place|the house)[.!]?$", re.IGNORECASE)
STREET_ADDRESS = re.compile(r"^\d+[A-Za-z]?\s+\S")


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


def home_area() -> str | None:
    """City and state of HOME_ADDRESS: "500 Main St, Ann Arbor, MI 48104" -> "Ann Arbor, MI"."""
    parts = [part.strip() for part in settings.home_address.split(",") if part.strip()]
    if len(parts) < 3:
        return None
    state = parts[2].split()[0] if parts[2].split() else ""
    return f"{parts[1]}, {state}" if state else parts[1]


def candidates(address: str) -> list[str]:
    """What to send Routes: the address as said, plus the home-area version of a street address
    that names no city."""
    area = home_area()
    if area and "," not in address and STREET_ADDRESS.match(address):
        return [address, f"{address}, {area}"]
    return [address]


def is_place_name(address: str) -> bool:
    """A name to look up ("The Landmark"), not a street address or one that names its city."""
    return bool(address) and "," not in address and not STREET_ADDRESS.match(address)


def in_home_area(address: str) -> str | None:
    """The home-area version of a place name that names no city, else None."""
    area = home_area()
    if area and "," not in address and not STREET_ADDRESS.match(address):
        return f"{address}, {area}"
    return None


async def best_route(
    lat: float, lng: float, address: str, *, client: httpx.AsyncClient | None = None
) -> Route:
    """The route to the destination (see the module docstring). Raises EtaError if none."""
    try:
        return await closest_route(lat, lng, address, client=client)
    except EtaError:
        local = in_home_area(address)
        if local is None:
            raise
        log.info("set_destination: no route for the name as said, trying the home area")
        return await drive_time(lat, lng, local, client=client)


async def closest_route(
    lat: float, lng: float, address: str, *, client: httpx.AsyncClient | None = None
) -> Route:
    """The closest route among the candidates (see candidates()). Raises EtaError if none."""
    tried = candidates(address)
    results = await asyncio.gather(
        *(drive_time(lat, lng, each, client=client) for each in tried), return_exceptions=True
    )
    routes = [r for r in results if isinstance(r, Route)]
    if not routes:
        raise next(r for r in results if isinstance(r, Exception))
    best = min(routes, key=lambda r: r.seconds)
    if len(tried) > 1:
        minutes = [r.minutes if isinstance(r, Route) else None for r in results]
        log.info(
            "set_destination: tried %d versions, minutes %s, using %d",
            len(tried),
            minutes,
            best.minutes,
        )
    return best


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
        if is_place_name(address):
            from app import inline  # imported here: app.inline imports this module's home_area

            found = await inline.find_address(address, near(drive))
            if found:
                log.info("set_destination: %r is at %s", address, found)
                address = found
        try:
            route = await best_route(drive.start_lat, drive.start_lng, address, client=client)
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
        return drive, f"Got it. {why}, so I'll ring once everything's done."
    return drive, f"About {route.minutes} minutes. I'll ring you just before you get there."
