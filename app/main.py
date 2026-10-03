"""FastAPI app: mounts every router and serves GET /health.

Step 1.4 deploys this to Railway; its pass check is GET /health on the public URL returning 200.
Routers (stubs until their build steps):
- app.events        POST /events             (step 1.5)
- app.voice_tools   POST /tools/*            (step 2.2)
- app.workers.coder POST /github/hook        (step 3.1)
"""

from fastapi import FastAPI

from app import events, voice_tools
from app.workers import coder

app = FastAPI(title="Shotgun")
app.include_router(events.router)
app.include_router(voice_tools.router)
app.include_router(coder.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
