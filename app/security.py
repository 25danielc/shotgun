"""Shared-secret and caller checks for the public webhooks (/events, /tools/*).

Both fail closed: if the server has no secret or allowed number configured, every request is
refused (503) rather than let through.
"""

from __future__ import annotations

import hmac
import re

from fastapi import HTTPException, status


def check_secret(given: str | None, expected: str) -> None:
    """401 on a missing or wrong secret; 503 if the server has none configured."""
    if not expected:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "secret not configured")
    if not given or not hmac.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "bad secret")


def normalize_number(number: str | None) -> str:
    """'+1 (734) 555-0100' -> '+17345550100'. Empty for None."""
    if not number:
        return ""
    digits = re.sub(r"\D", "", number)
    return f"+{digits}" if digits else ""


def check_caller(caller: str | None, called: str | None, allowed: str) -> None:
    """403 unless the allowed number is on this call, as caller (inbound) or callee (outbound).

    ElevenLabs passes system__caller_id and system__called_number. Which one holds Daniel's
    number on an outbound callback is unverified, so either counts. A stranger dialling in has
    their own number as caller and our Twilio number as callee, so they are still refused.
    """
    allowed = normalize_number(allowed)
    if not allowed:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "caller allowlist not configured")
    if allowed not in {normalize_number(caller), normalize_number(called)}:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "caller not allowed")
