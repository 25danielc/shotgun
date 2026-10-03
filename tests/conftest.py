"""Shared fixtures.

- Tests marked `live` hit real APIs or ring a phone; they only run with RUN_LIVE=1
  (`make test-live`).
- `rang` mocks ElevenLabs: telephony.place_call records each call's dynamic variables, and
  call_in_progress reports a free line unless `rang.busy` is set. `http` is the real app with
  the `db` connection as its pool (for /events and /tools), secrets and allowlist set.
- `db` gives an AsyncConnection inside a transaction that is always rolled back. It runs on an
  embedded Postgres (pgserver) by default, or on Neon with USE_NEON=1 (`make test-neon`), which
  is how DB pass checks are proven "in Neon".
"""

import os
from contextlib import asynccontextmanager

import httpx
import pytest
from psycopg import AsyncConnection

from app import db as app_db
from app import telephony, voice_tools
from app.config import settings
from app.jobs import init_schema
from app.main import app
from tests.helpers import DANIEL, EVENTS_SECRET, TOOLS_SECRET


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RUN_LIVE") == "1":
        return
    skip_live = pytest.mark.skip(reason="live test: set RUN_LIVE=1 to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture(autouse=True)
def default_call_policy(monkeypatch):
    """Tests see the default departure policy, whatever CALL_POLICY the local .env sets."""
    monkeypatch.setattr(settings, "call_policy", "auto")


@pytest.fixture(scope="session")
def pg_uri(tmp_path_factory):
    if os.environ.get("USE_NEON") == "1":
        from app.config import settings

        if not settings.database_url:
            pytest.skip("USE_NEON=1 but DATABASE_URL is not set")
        yield settings.database_url
        return
    pgserver = pytest.importorskip("pgserver")
    server = pgserver.get_server(tmp_path_factory.mktemp("pg"), cleanup_mode="delete")
    yield server.get_uri()
    server.cleanup()


@pytest.fixture
async def db(pg_uri):
    conn = await AsyncConnection.connect(pg_uri, autocommit=False, prepare_threshold=None)
    try:
        await conn.execute("select 1")  # open the outer transaction; our code nests savepoints
        await init_schema(conn)
        yield conn
    finally:
        await conn.rollback()
        await conn.close()


class Rang(list):
    """Every call placed, as its dynamic variables."""

    busy = False
    refuse = False


@pytest.fixture
def rang(monkeypatch):
    placed = Rang()

    async def fake_place_call(variables, **kwargs):
        if placed.refuse:
            raise telephony.CallError("HTTP 500")
        placed.append(variables)
        return telephony.PlacedCall(conversation_id=f"conv_{len(placed)}", call_sid="CA")

    async def line_busy(**kwargs):
        return placed.busy

    monkeypatch.setattr(telephony, "place_call", fake_place_call)
    monkeypatch.setattr(telephony, "call_in_progress", line_busy)
    return placed


@pytest.fixture
async def http(db, monkeypatch, rang):
    """The real app, with the test connection as its pool and the tools DB dependency."""

    class Pool:
        @asynccontextmanager
        async def connection(self, timeout=None):
            yield db

    monkeypatch.setattr(app_db, "get_pool", lambda: Pool())
    monkeypatch.setattr(settings, "events_shared_secret", EVENTS_SECRET)
    monkeypatch.setattr(settings, "tools_shared_secret", TOOLS_SECRET)
    monkeypatch.setattr(settings, "allowed_caller_number", DANIEL)

    async def test_conn():
        yield db

    app.dependency_overrides[voice_tools.get_conn] = test_conn
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()
