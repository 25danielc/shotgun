"""Step 5.1 (D17): ETA from the Routes API, once at departure.

Pass checks (proposed in PLAN.md; Daniel's message was cut off before his list):
- coordinates -> minutes within 2 of Google Maps (live; Daniel compares the printed minutes),
- set_destination answers in under 8 s (offline with Routes mocked, and live),
- no location or no destination falls back to the "all terminal or held" arrival trigger.
"""

import json
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app import calls, drives, eta, jobs
from app.config import settings
from app.jobs import JobType
from tests.helpers import DANIEL, EVENTS_SECRET, TOOLS_SECRET, run

NOW = datetime(2026, 10, 3, 18, 30, tzinfo=UTC)
UNION = (42.2754, -83.7417)  # Michigan Union (same pair as check_keys.py)
AMTRAK = "325 Depot St, Ann Arbor, MI 48104"


@pytest.fixture(autouse=True)
def maps(monkeypatch):
    monkeypatch.setattr(settings, "google_maps_api_key", "test-maps-key")
    monkeypatch.setattr(settings, "home_address", "500 Main St, Ann Arbor, MI")


def routes(seconds=1320, status=200, seen=None):
    def handler(request):
        if seen is not None:
            seen.update(
                url=str(request.url), body=json.loads(request.content), headers=request.headers
            )
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "nope"}})
        return httpx.Response(
            200, json={"routes": [{"duration": f"{seconds}s", "distanceMeters": 18000}]}
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- the Routes request -----------------------------------------------------------------------


async def test_request_matches_the_routes_docs():
    seen = {}
    route = await eta.drive_time(*UNION, AMTRAK, client=routes(1320, seen=seen))
    assert seen["url"] == "https://routes.googleapis.com/directions/v2:computeRoutes"
    assert seen["headers"]["x-goog-api-key"] == "test-maps-key"
    assert seen["headers"]["x-goog-fieldmask"] == "routes.duration,routes.distanceMeters"
    assert seen["body"] == {
        "origin": {"location": {"latLng": {"latitude": 42.2754, "longitude": -83.7417}}},
        "destination": {"address": AMTRAK},
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE",
    }
    assert (route.seconds, route.meters, route.minutes) == (1320, 18000, 22)


@pytest.mark.parametrize(("value", "seconds"), [("1234s", 1234), ("3.5s", 4), ("0s", 0)])
def test_duration_strings(value, seconds):
    assert eta.parse_duration(value) == seconds


@pytest.mark.parametrize("value", ["", "12m", "s"])
def test_bad_duration_raises(value):
    with pytest.raises(eta.EtaError):
        eta.parse_duration(value)


@pytest.mark.parametrize(("status", "body"), [(403, None), (200, {"routes": []})])
async def test_routes_errors_raise(status, body):
    def handler(request):
        return httpx.Response(status, json=body or {"error": {}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(eta.EtaError):
        await eta.drive_time(*UNION, AMTRAK, client=client)


@pytest.mark.parametrize(
    ("said", "spoken", "address"),
    [
        ("home", "home", "500 Main St, Ann Arbor, MI"),
        ("Home.", "home", "500 Main St, Ann Arbor, MI"),
        ("my place", "home", "500 Main St, Ann Arbor, MI"),
        ("the  Michigan Union", "the Michigan Union", "the Michigan Union"),
    ],
)
def test_home_means_home_address_and_is_never_read_out(said, spoken, address):
    assert eta.resolve(said) == (spoken, address)


# --- a street address with no city (Daniel's 18:40 call: 53 min to Detroit, not 9 in Ann Arbor) --


def test_home_area_comes_from_home_address(monkeypatch):
    assert eta.home_area() == "Ann Arbor, MI"
    monkeypatch.setattr(settings, "home_address", "1780 Broadway St, Ann Arbor, MI 48105")
    assert eta.home_area() == "Ann Arbor, MI"
    monkeypatch.setattr(settings, "home_address", "")
    assert eta.home_area() is None


@pytest.mark.parametrize(
    ("said", "tried"),
    [
        ("333 East Jefferson", ["333 East Jefferson", "333 East Jefferson, Ann Arbor, MI"]),
        ("333 East Jefferson, Detroit", ["333 East Jefferson, Detroit"]),
        ("Detroit", ["Detroit"]),
        ("the Michigan Union", ["the Michigan Union"]),
    ],
)
def test_only_street_addresses_without_a_city_get_a_second_try(said, tried):
    assert eta.candidates(said) == tried


async def test_the_closer_match_wins():
    def handler(request):
        address = json.loads(request.content)["destination"]["address"]
        seconds = 559 if address.endswith("Ann Arbor, MI") else 3154  # live values, 2026-10-03
        return httpx.Response(200, json={"routes": [{"duration": f"{seconds}s"}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    route = await eta.best_route(42.2967, -83.7211, "333 East Jefferson", client=client)
    assert route.minutes == 10


def by_address(minutes: dict[str, int | None]):
    """A fake Routes: {address: minutes or None for no route}."""
    seen = []

    def handler(request):
        address = json.loads(request.content)["destination"]["address"]
        seen.append(address)
        found = minutes.get(address)
        routes = [{"duration": f"{found * 60}s"}] if found is not None else []
        return httpx.Response(200, json={"routes": routes})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler)), seen


async def test_a_place_name_falls_back_to_the_home_area_only_if_not_found():
    """Daniel 20:52: "The Landmark" got no route; "The Landmark, Ann Arbor, MI" is 15 min."""
    client, seen = by_address({"The Landmark, Ann Arbor, MI": 15})
    route = await eta.best_route(42.2967, -83.7211, "The Landmark", client=client)
    assert route.minutes == 15
    assert seen == ["The Landmark", "The Landmark, Ann Arbor, MI"]


async def test_a_place_name_that_routes_is_never_swapped_for_a_closer_one():
    client, seen = by_address({"Detroit": 50, "Detroit, Ann Arbor, MI": 4})
    route = await eta.best_route(42.2967, -83.7211, "Detroit", client=client)
    assert route.minutes == 50
    assert seen == ["Detroit"]


async def test_nothing_found_anywhere_is_still_an_error():
    client, _ = by_address({})
    with pytest.raises(eta.EtaError):
        await eta.best_route(42.2967, -83.7211, "Nowhere Special", client=client)


async def test_one_failed_version_doesnt_lose_the_other():
    def handler(request):
        address = json.loads(request.content)["destination"]["address"]
        if address.endswith("Ann Arbor, MI"):
            return httpx.Response(200, json={"routes": [{"duration": "559s"}]})
        return httpx.Response(200, json={"routes": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert (await eta.best_route(42.3, -83.7, "333 East Jefferson", client=client)).seconds == 559


# --- set_destination stores the ETA and the arrival time --------------------------------------


async def test_destination_sets_eta_and_arrival_call_3_min_before(db):
    drive = await drives.open_drive(db, lat=UNION[0], lng=UNION[1], now=NOW)
    seen = {}
    drive, reply = await eta.set_destination(
        db, drive, "home", now=NOW, client=routes(1320, seen=seen)
    )
    assert seen["body"]["destination"] == {"address": "500 Main St, Ann Arbor, MI"}
    assert drive.destination == "home"
    assert drive.eta == NOW + timedelta(minutes=22)
    assert drive.arrival_call_at == NOW + timedelta(minutes=19)
    assert reply == "About 22 minutes. I'll ring you just before you get there."


async def test_short_drive_arrival_call_is_due_at_once(db):
    drive = await drives.open_drive(db, lat=UNION[0], lng=UNION[1], now=NOW)
    drive, _ = await eta.set_destination(db, drive, "the corner store", now=NOW, client=routes(90))
    assert drive.arrival_call_at == NOW


async def test_no_location_stores_the_destination_and_falls_back(db, rang):
    now = datetime.now(UTC)  # real time: calls.tick only sees drives from the last 3 hours
    drive = await drives.open_drive(db, now=now)  # the Shortcut sent no location
    drive, reply = await eta.set_destination(db, drive, "home", now=now, client=routes())
    assert (drive.destination, drive.eta, drive.arrival_call_at) == ("home", None, None)
    assert reply == "Got it. I don't have your location, so I'll ring once everything's done."
    # Fallback: the arrival call rings once every job is settled.
    job = await jobs.create_job(db, JobType.RESEARCH, drive_id=drive.id)
    await run(db, job.id, "running")
    assert await calls.tick(db) is None
    await run(db, job.id, "done", summary="It's 54 degrees.")
    assert await calls.tick(db) == "arrival"


async def test_routes_failure_falls_back_too(db):
    drive = await drives.open_drive(db, lat=UNION[0], lng=UNION[1], now=NOW)
    drive, reply = await eta.set_destination(db, drive, "home", now=NOW, client=routes(status=500))
    assert drive.arrival_call_at is None
    assert reply.startswith("Got it. I couldn't get a route")


async def test_arrival_rings_at_eta_minus_3_not_when_work_finishes(db, rang):
    drive = await drives.open_drive(db, lat=UNION[0], lng=UNION[1], now=NOW)
    drive, _ = await eta.set_destination(db, drive, "home", now=NOW, client=routes(1320))
    job = await jobs.create_job(db, JobType.RESEARCH, drive_id=drive.id)
    await run(db, job.id, "running", "done", summary="It's 54 degrees.")
    assert await calls.tick(db, NOW + timedelta(minutes=2)) is None  # all done, but not yet
    assert await calls.tick(db, NOW + timedelta(minutes=19)) == "arrival"


# --- through the voice tool -------------------------------------------------------------------


async def test_set_destination_tool_answers_inline_on_the_plug_in_drive(
    http, db, rang, monkeypatch
):
    async def fake_drive_time(lat, lng, address, **kwargs):
        assert (lat, lng, address) == (*UNION, "500 Main St, Ann Arbor, MI")
        return eta.Route(seconds=1320, meters=18000)

    monkeypatch.setattr(eta, "drive_time", fake_drive_time)
    body = {
        "source": "test",
        "event": "carplay_connected",
        "location": {"lat": UNION[0], "lng": UNION[1]},
    }
    await http.post("/events", json=body, headers={"X-Shotgun-Secret": EVENTS_SECRET})
    drive = await drives.current_drive(db)
    assert rang[0]["greeting"] == "Hey, it's Shotgun. Where are you headed?"

    payload = {
        "destination": "home",
        "drive_id": str(drive.id),
        "caller": DANIEL,
        "called": "+15555550199",
    }
    start = time.perf_counter()
    response = await http.post(
        "/tools/set_destination", json=payload, headers={"X-Shotgun-Secret": TOOLS_SECRET}
    )
    assert time.perf_counter() - start < 8
    assert (
        response.json()["message"] == "About 22 minutes. I'll ring you just before you get there."
    )
    assert (await drives.get_drive(db, drive.id)).arrival_call_at is not None


async def test_set_destination_without_drive_id_uses_the_open_drive(http, db):
    drive = await drives.open_drive(db)
    payload = {"destination": "home", "drive_id": "", "caller": DANIEL, "called": "+15555550199"}
    response = await http.post(
        "/tools/set_destination", json=payload, headers={"X-Shotgun-Secret": TOOLS_SECRET}
    )
    assert response.json()["drive_id"] == drive.id
    assert (await drives.get_drive(db, drive.id)).destination == "home"


# --- live -------------------------------------------------------------------------------------


@pytest.mark.live
async def test_live_coordinates_to_minutes(monkeypatch):
    """Daniel compares the printed minutes with Google Maps for the same trip (within 2)."""
    monkeypatch.undo()  # the real key
    start = time.perf_counter()
    route = await eta.drive_time(*UNION, AMTRAK)
    elapsed = time.perf_counter() - start
    print(
        f"Michigan Union -> Ann Arbor Amtrak: {route.minutes} min "
        f"({route.seconds} s, {route.meters} m) in {elapsed:.1f} s"
    )
    assert elapsed < 8
    assert 1 <= route.minutes <= 30


# --- drives started without a location (Daniel 20:00: simulated plug-in, "I don't have your
# location", although the Shortcut had sent it 80 minutes earlier) ------------------------------


async def test_a_drive_without_a_location_uses_the_last_known_one(db):
    now = datetime.now(UTC)
    shortcut = await drives.open_drive(
        db, lat=UNION[0], lng=UNION[1], now=now - timedelta(minutes=80)
    )
    assert shortcut.location_source == "plug_in"
    simulated = await drives.open_drive(db, source="dashboard_demo", now=now)
    assert (simulated.start_lat, simulated.start_lng) == UNION
    assert simulated.location_source == "last_known"


async def test_simulated_plug_in_gets_a_real_eta(db):
    now = datetime.now(UTC)
    await drives.open_drive(db, lat=UNION[0], lng=UNION[1], now=now - timedelta(minutes=80))
    simulated = await drives.open_drive(db, source="dashboard_demo", now=now)
    drive, reply = await eta.set_destination(
        db, simulated, "333 East Jefferson Street", now=now, client=routes(559)
    )
    assert reply == "About 10 minutes. I'll ring you just before you get there."
    assert drive.arrival_call_at is not None


async def test_last_known_location_expires_and_is_never_copied_forward(db):
    now = datetime.now(UTC)
    await drives.open_drive(
        db,
        lat=UNION[0],
        lng=UNION[1],
        now=now - timedelta(hours=drives.LAST_KNOWN_HOURS, minutes=1),
    )
    stale = await drives.open_drive(db, now=now)
    assert (stale.start_lat, stale.location_source) == (None, None)

    await drives.open_drive(db, lat=UNION[0], lng=UNION[1], now=now - timedelta(hours=2))
    copied = await drives.open_drive(db, now=now - timedelta(hours=1))
    assert copied.location_source == "last_known"
    # Only real plug-in locations count as "known": a copy is never the source of another copy.
    assert await drives.last_known_location(db, now) == UNION
    await db.execute("update drives set start_lat = 0, start_lng = 0 where id = %s", (copied.id,))
    assert await drives.last_known_location(db, now) == UNION


async def test_a_real_plug_in_location_always_wins(db):
    now = datetime.now(UTC)
    await drives.open_drive(db, lat=1.0, lng=2.0, now=now - timedelta(minutes=10))
    fresh = await drives.open_drive(db, lat=UNION[0], lng=UNION[1], now=now)
    assert ((fresh.start_lat, fresh.start_lng), fresh.location_source) == (UNION, "plug_in")
