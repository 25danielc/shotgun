"""ElevenLabs outbound-call client (Twilio number imported into ElevenLabs).

Build step 1.2: one call makes the phone ring within 5 s. From the shell:
    uv run python -m app.telephony "Test call from Shotgun."      # rings MY_PHONE_NUMBER

Per the ElevenLabs API docs (read 2026-10-03, see docs/DECISIONS.md section 11):
    POST https://api.elevenlabs.io/v1/convai/twilio/outbound-call
    Header: xi-api-key: <ELEVENLABS_API_KEY>
    Body:   {"agent_id": ELEVENLABS_AGENT_ID,
             "agent_phone_number_id": ELEVENLABS_PHONE_NUMBER_ID,
             "to_number": MY_PHONE_NUMBER,
             "conversation_initiation_client_data": {
                 "dynamic_variables": {"greeting": "...", "summary": "...",
                                       "eta_minutes": "31", "pending_job_id": "",
                                       "drive_id": "12", "call_kind": "arrival",
                                       "caller_allowed": "yes"}}}
    Response: {"success": true, "conversation_id": "...", "callSid": "..."}

Per-call text goes in dynamic variables, which the agent's first_message ({{greeting}}) and
prompt reference. That needs no override permission, unlike conversation_config_override.
Every call sends all seven variables so none falls back to a stale placeholder. Because the request
includes conversation_initiation_client_data, ElevenLabs does not call our /tools/init webhook for
these outbound calls (docs: "Conversation initiation webhooks", read 2026-10-03).
"""

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass

import httpx

from app.config import settings

OUTBOUND_URL = "https://api.elevenlabs.io/v1/convai/twilio/outbound-call"
CONVERSATIONS_URL = "https://api.elevenlabs.io/v1/convai/conversations"
# Conversation statuses (API reference, read 2026-10-03): initiated, in-progress, processing,
# done, failed. "processing" is post-call analysis, so the line is already free.
LIVE_STATUSES = frozenset({"initiated", "in-progress"})
STALE_CALL_SECONDS = 15 * 60  # a "live" call older than this is a stuck status, not a call
TIMEOUT = 10.0


class CallError(RuntimeError):
    pass


@dataclass(frozen=True)
class PlacedCall:
    conversation_id: str | None
    call_sid: str | None


def call_variables(
    greeting: str,
    *,
    summary: str = "",
    eta_minutes: int | None = None,
    pending_job_id: int | str | None = None,
    drive_id: int | None = None,
    call_kind: str = "",
) -> dict[str, str]:
    """All dynamic variables the agent defines, as strings.

    call_kind (D17): "departure", "arrival", "exception", or "inbound" (set by /tools/init).
    """
    return {
        "greeting": greeting,
        "summary": summary,
        "eta_minutes": "" if eta_minutes is None else str(eta_minutes),
        "pending_job_id": "" if pending_job_id is None else str(pending_job_id),
        "drive_id": "" if drive_id is None else str(drive_id),
        "call_kind": call_kind,
        "caller_allowed": "yes",  # we only ever call MY_PHONE_NUMBER
    }


def agent_ids() -> list[str]:
    """Every agent that can be on a call with the driver (main, then the callback agent)."""
    ids = [settings.elevenlabs_agent_id, settings.elevenlabs_callback_agent_id]
    return [i for i in dict.fromkeys(ids) if i]


async def place_call(
    variables: dict[str, str],
    *,
    to_number: str | None = None,
    agent_id: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> PlacedCall:
    """Ring `to_number` (default MY_PHONE_NUMBER) with the voice agent. Raises CallError."""
    to_number = to_number or settings.my_phone_number
    required = {
        "ELEVENLABS_API_KEY": settings.elevenlabs_api_key,
        "ELEVENLABS_AGENT_ID": settings.elevenlabs_agent_id,
        "ELEVENLABS_PHONE_NUMBER_ID": settings.elevenlabs_phone_number_id,
        "MY_PHONE_NUMBER": to_number,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise CallError(f"cannot place call, missing {', '.join(missing)}")

    body = {
        "agent_id": agent_id or settings.elevenlabs_agent_id,
        "agent_phone_number_id": settings.elevenlabs_phone_number_id,
        "to_number": to_number,
        "conversation_initiation_client_data": {"dynamic_variables": variables},
    }
    headers = {"xi-api-key": settings.elevenlabs_api_key}
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT)
    try:
        response = await client.post(OUTBOUND_URL, json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise CallError(f"outbound call request failed: {type(exc).__name__}") from exc
    finally:
        if owns_client:
            await client.aclose()

    if response.status_code >= 400:
        raise CallError(f"outbound call HTTP {response.status_code}: {response.text[:200]}")
    data = response.json()
    if not data.get("success", False):
        raise CallError(f"outbound call refused: {data.get('message', data)}")
    return PlacedCall(conversation_id=data.get("conversation_id"), call_sid=data.get("callSid"))


async def call_in_progress(*, client: httpx.AsyncClient | None = None) -> bool:
    """True if either agent is on a call right now, so a callback would ring into it.

    Fails open (False) when ElevenLabs can't be reached: a late callback beats none.
    """
    conversations = await recent_conversations(client=client)
    now = time.time()
    return any(
        c.get("status") in LIVE_STATUSES
        and now - (c.get("start_time_unix_secs") or now) < STALE_CALL_SECONDS
        for c in conversations
    )


async def recent_conversations(*, client: httpx.AsyncClient | None = None) -> list[dict]:
    """The last few conversations of every agent (agent_ids()), newest first. [] on errors."""
    headers = {"xi-api-key": settings.elevenlabs_api_key}
    owns_client = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT)
    found: list[dict] = []
    try:
        for agent_id in agent_ids() or [settings.elevenlabs_agent_id]:
            response = await client.get(
                CONVERSATIONS_URL, params={"agent_id": agent_id, "page_size": 5}, headers=headers
            )
            response.raise_for_status()
            found += response.json().get("conversations", [])
    except (httpx.HTTPError, ValueError):
        return found
    finally:
        if owns_client:
            await client.aclose()
    return sorted(found, key=lambda c: c.get("start_time_unix_secs") or 0, reverse=True)


if __name__ == "__main__":
    greeting = " ".join(sys.argv[1:]) or "Hey, it's Shotgun. This is a test call."
    placed = asyncio.run(place_call(call_variables(greeting)))
    print(f"calling {settings.my_phone_number[:-4]}****: conversation {placed.conversation_id}")
