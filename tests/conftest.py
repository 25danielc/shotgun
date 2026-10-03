"""Shared fixtures.

- Tests marked `live` hit real APIs or ring a phone; they only run with RUN_LIVE=1
  (`make test-live`).
- `db` gives an AsyncConnection inside a transaction that is always rolled back. It runs on an
  embedded Postgres (pgserver) by default, or on Neon with USE_NEON=1 (`make test-neon`), which
  is how DB pass checks are proven "in Neon".
"""

import os

import pytest
from psycopg import AsyncConnection

from app.jobs import init_schema


def pytest_collection_modifyitems(config, items):
    if os.environ.get("RUN_LIVE") == "1":
        return
    skip_live = pytest.mark.skip(reason="live test: set RUN_LIVE=1 to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


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
