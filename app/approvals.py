"""Pre-approval: a spoken yes given at dispatch, checked when a worker reaches its irreversible
step (merge, send, order, pay). Build step 4.2 (D17).

A worker that is about to do something irreversible calls `settle()` with the facts it has (did
the tests pass, what does it cost) and gets the job moved to one of:
- approved        the job carries a preapproval and the facts are inside it: go ahead, no call.
- exception       the job carries a preapproval and a fact breaks it. The driver hears about it
                  on an exception call (or the arrival call) and says yes or no.
- needs_approval  no preapproval: held for the arrival call.

Only the structured fields are enforced (Daniel, D17): require_tests_pass and max_usd.
`condition` is what the driver agreed to in their words; it is stored and read back, never judged.
A field the worker can't report counts as broken: "merge if the tests pass" with no test result
is not a yes.
"""

from __future__ import annotations

from typing import Any

from psycopg import AsyncConnection

from app import jobs
from app.jobs import Job, JobState


def broken_by(
    preapproval: dict[str, Any],
    *,
    tests_passed: bool | None = None,
    total_usd: float | None = None,
) -> str | None:
    """None if the facts are inside the preapproval, else a short spoken reason."""
    if preapproval.get("require_tests_pass"):
        if tests_passed is None:
            return "the tests never reported"
        if not tests_passed:
            return "the tests failed"
    limit = preapproval.get("max_usd")
    if limit is not None:
        if total_usd is None:
            return "I couldn't get a price"
        if total_usd > limit:
            return f"it comes to {total_usd:.2f} dollars, over your {limit:g} dollar limit"
    return None


async def settle(
    conn: AsyncConnection,
    job: Job,
    *,
    question: str,
    exception_summary: str | None = None,
    tests_passed: bool | None = None,
    total_usd: float | None = None,
) -> Job:
    """Move a running job at its irreversible step to approved, exception or needs_approval.

    `question` is what the driver hears when the job is held ("I opened a pull request: X.
    Merge it?"). `exception_summary`, if given, is used when the preapproval is broken; the
    default is "<Reason> on <question>".
    """
    if not job.preapproval:
        return await jobs.transition(
            conn, job.id, JobState.NEEDS_APPROVAL, summary=question, note="held for a yes"
        )
    reason = broken_by(job.preapproval, tests_passed=tests_passed, total_usd=total_usd)
    if reason is None:
        return await jobs.transition(
            conn,
            job.id,
            JobState.APPROVED,
            note=f"pre-approved: {job.preapproval.get('condition', '')}",
        )
    summary = exception_summary or f"{reason[0].upper()}{reason[1:]}. {question}"
    return await jobs.transition(
        conn, job.id, JobState.EXCEPTION, summary=summary, note=f"pre-approval broken: {reason}"
    )
