"""Departure call policy: does plugging in ring the car? Build step 4.3 (core since D17).

Ring only when it's worth it (DECISIONS.md D17):
1. pending items: something is waiting on the driver (a job in needs_approval or exception,
   from any drive), or
2. a known long drive: the ETA is known and at least DEPARTURE_MIN_DRIVE_MINUTES, or
3. quiet: we haven't called the driver in the last DEPARTURE_QUIET_MINUTES.
Otherwise stay silent. The drive is opened either way, so a later tap on the contact still
lands in it.

CALL_POLICY=always (settings.call_policy) rings on every plug-in: for rehearsals, where a quick
replug would otherwise be silenced and the plug-in ring is never-cut. Thresholds are settings.

Pure function; app/events.py gathers the facts.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.config import settings


@dataclass(frozen=True)
class Decision:
    call: bool
    reason: str


def departure(
    *,
    now: datetime,
    pending: int,
    eta_minutes: int | None,
    last_call_at: datetime | None,
) -> Decision:
    """Whether the plug-in rings, and why (logged)."""
    if settings.call_policy == "always":
        return Decision(True, "CALL_POLICY=always")
    if pending:
        return Decision(True, f"{pending} item(s) waiting on the driver")
    if eta_minutes is not None and eta_minutes >= settings.departure_min_drive_minutes:
        return Decision(True, f"{eta_minutes}-minute drive")
    quiet = timedelta(minutes=settings.departure_quiet_minutes)
    if last_call_at is None or now - last_call_at >= quiet:
        return Decision(True, f"no call in {settings.departure_quiet_minutes} min")
    return Decision(False, "nothing pending, short or unknown drive, called recently")
