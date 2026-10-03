"""FastAPI app: mounts every router and serves GET /health.

Step 1.4 deploys this to Railway; its pass check is GET /health on the public URL returning 200.
Routers:
- app.events        POST /events             (step 1.5)
- app.voice_tools   POST /tools/*            (step 2.2)
- app.workers.coder POST /github/hook        (step 3.1)
- app.dashboard     GET /dashboard, /dashboard/state, POST /dashboard/demo/* (mission control)

On startup, if DATABASE_URL is set, the Postgres pool opens and the job tables are created or
updated (idempotent). A database outage doesn't stop the app: /health stays up and the tools
answer 503 until the pool is back. Background loops, cancelled on shutdown: the planner
(app/orchestrator.py, needs ANTHROPIC_API_KEY), the arrival and exception calls (app/calls.py,
needs the ElevenLabs agent, phone number id and MY_PHONE_NUMBER), the coder worker
(app/workers/coder.py, needs GITHUB_TOKEN and GITHUB_DEMO_REPO) and the research worker
(app/workers/research.py, needs ANTHROPIC_API_KEY) and the dashboard monitor (app/dashboard.py:
 service health every 30 s, the live call every 5 s while a drive is open).
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import calls, dashboard, db, events, jobs, orchestrator, voice_tools
from app.config import settings
from app.workers import coder, research

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    background: list[asyncio.Task] = []
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
    pool = db.get_pool()
    if pool is not None and settings.anthropic_api_key:
        background.append(asyncio.create_task(orchestrator.run_planner(pool), name="planner"))
        background.append(asyncio.create_task(research.run_research(pool), name="research"))
    else:
        log.warning("planner + research not running (needs DATABASE_URL and ANTHROPIC_API_KEY)")
    calls_configured = all(
        [
            settings.elevenlabs_api_key,
            settings.elevenlabs_agent_id,
            settings.elevenlabs_phone_number_id,
            settings.my_phone_number,
        ]
    )
    if pool is not None and calls_configured:
        background.append(asyncio.create_task(calls.run_calls(pool), name="calls"))
    else:
        log.warning("calls not running (needs DATABASE_URL and the ElevenLabs/phone settings)")
    if pool is not None and settings.github_token and settings.github_demo_repo:
        background.append(asyncio.create_task(coder.run_coder(pool), name="coder"))
    else:
        log.warning("coder worker not running (needs DATABASE_URL, GITHUB_TOKEN, GITHUB_DEMO_REPO)")
    background.append(asyncio.create_task(dashboard.run_monitor(pool), name="dashboard"))
    yield
    for task in background:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await db.close_pool()


app = FastAPI(title="Shotgun", lifespan=lifespan)
app.include_router(events.router)
app.include_router(voice_tools.router)
app.include_router(coder.router)
app.include_router(dashboard.router)
app.add_middleware(dashboard.ToolCallRecorder)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
