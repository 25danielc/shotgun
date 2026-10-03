"""Shared constants and helpers for the drive and call tests (steps 4.1, 4.3, 4.4)."""

from app import calls, jobs

EVENTS_SECRET = "e" * 32
TOOLS_SECRET = "t" * 32
DANIEL = "+15555550100"
TESTS_YES = {"condition": "merge it if the tests pass", "require_tests_pass": True}


async def run(db, job_id, *steps, summary=None):
    """Move a job through states as its worker would; the last step gets the summary."""
    job = None
    for i, step in enumerate(steps):
        last = i == len(steps) - 1
        job = await jobs.transition(db, job_id, step, summary=summary if last else None)
    return job


async def ticks(db, n=5, now=None):
    return [await calls.tick(db, now) for _ in range(n)]
