"""FastAPI app: mounts every router and serves GET /health.

Step 1.4 deploys this to Railway; its pass check is GET /health on the public URL returning 200.
Routers:
- app.events        POST /events             (step 1.5)
- app.voice_tools   POST /tools/*            (step 2.2)
- app.workers.coder POST /github/hook        (step 3.1, stub)

On startup, if DATABASE_URL is set, the Postgres pool opens and the job tables are created or
updated (idempotent). A database outage doesn't stop the app: /health stays up and the tools
answer 503 until the pool is back.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import db, events, jobs, voice_tools
from app.config import settings
from app.workers import coder

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if settings.database_url:
        try:
            pool = await db.open_pool()
            async with pool.connection() as conn:
                await jobs.init_schema(conn)
            log.info("database ready")
        except Exception:
            log.exception("database unavailable at startup; tools will answer 503")
    else:
        log.warning("DATABASE_URL not set; tools will answer 503")
    yield
    await db.close_pool()


app = FastAPI(title="Shotgun", lifespan=lifespan)
app.include_router(events.router)
app.include_router(voice_tools.router)
app.include_router(coder.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
