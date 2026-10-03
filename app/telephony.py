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
                                       "caller_allowed": "yes"}}}
    Response: {"success": true, "conversation_id": "...", "callSid": "..."}

Per-call text goes in dynamic variables, which the agent's first_message ({{greeting}}) and
prompt reference. That needs no override permission, unlike conversation_config_override.
Every call sends all five variables so none falls back to a stale placeholder. Because the request
includes conversation_initiation_client_data, ElevenLabs does not call our /tools/init webhook for
these outbound calls (docs: "Conversation initiation webhooks", read 2026-10-03).
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass

import httpx

from app.config import settings

OUTBOUND_URL = "https://api.elevenlabs.io/v1/convai/twilio/outbound-call"
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
) -> dict[str, str]:
    """All dynamic variables the agent defines, as strings."""
    return {
        "greeting": greeting,
        "summary": summary,
        "eta_minutes": "" if eta_minutes is None else str(eta_minutes),
        "pending_job_id": "" if pending_job_id is None else str(pending_job_id),
        "caller_allowed": "yes",  # we only ever call MY_PHONE_NUMBER
    }


async def place_call(
    variables: dict[str, str],
    *,
    to_number: str | None = None,
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
        "agent_id": settings.elevenlabs_agent_id,
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


if __name__ == "__main__":
    greeting = " ".join(sys.argv[1:]) or "Hey, it's Shotgun. This is a test call."
    placed = asyncio.run(place_call(call_variables(greeting)))
    print(f"calling {settings.my_phone_number[:-4]}****: conversation {placed.conversation_id}")
