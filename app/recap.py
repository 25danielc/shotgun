"""Unplug recap: when CarPlay disconnects, close the drive and push what happened (step 4.4, D17).

POST /events {"event": "carplay_disconnected"} (alias "car_disconnected") closes the open drive,
which cancels its arrival call if it hasn't rung yet (app/calls.py only rings for open drives),
then sends one ntfy push: done, waiting on you, still running, didn't work. A drive with no jobs
sends nothing. Anything still waiting is raised again on the next departure call (step 4.3).

This is the only push in the product; calls stay the main channel (D6, D17).

ntfy publish API (docs/publish.md in binwiederhier/ntfy, read 2026-10-03):
    POST {NTFY_SERVER}/{NTFY_TOPIC}   body = message text (UTF-8)
    headers X-Title, X-Tags (comma-separated tags or emoji short codes)
Topics on ntfy.sh are public by name, so NTFY_TOPIC is a secret: long and random, in .env and
Railway only, never in git.
"""

from __future__ import annotations

import logging

import httpx

from app import calls
from app.config import settings
from app.jobs import Job, JobState, JobType

log = logging.getLogger(__name__)

TITLE = "Shotgun: drive recap"
TIMEOUT = 10.0
SECTIONS = (
    ("Done", (JobState.DONE,)),
    ("Waiting on you", (JobState.EXCEPTION, JobState.NEEDS_APPROVAL)),
    ("Still running", (JobState.QUEUED, JobState.RUNNING, JobState.APPROVED)),
    ("Didn't work", (JobState.FAILED,)),
)


def recap_text(drive_jobs: list[Job]) -> str | None:
    """One line per job, grouped; None if there is nothing to recap."""
    heard = [j for j in drive_jobs if not (j.type is JobType.PLAN and j.state is JobState.DONE)]
    lines = []
    for heading, states in SECTIONS:
        for job in (j for j in heard if j.state in states):
            lines.append(f"{heading}: {calls.job_sentence(job)}")
    return "\n".join(lines) or None


async def push(text: str, *, client: httpx.AsyncClient | None = None) -> bool:
    """Send the recap to ntfy. Logs and returns False on any failure (never raises)."""
    if not settings.ntfy_topic:
        log.warning("recap: NTFY_TOPIC not set, nothing sent")
        return False
    url = f"{settings.ntfy_server.rstrip('/')}/{settings.ntfy_topic}"
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT)
    try:
        response = await client.post(
            url, content=text.encode(), headers={"X-Title": TITLE, "X-Tags": "red_car"}
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        log.error("recap: ntfy push failed: %s", type(exc).__name__)
        return False
    finally:
        if owns_client:
            await client.aclose()
    return True
