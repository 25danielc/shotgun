"""POST /tools/*: webhook tools the ElevenLabs voice agent calls mid-conversation.

Build steps: 2.2 (webhooks answer < 500 ms and write rows), 2.3 (wired into ElevenLabs,
caller allowlist), 4.2 (spoken approval loop).

Tools (schemas live in config/elevenlabs_agent.json):
- dispatch_task(request | type+details): insert job row(s), return immediately. Planning by
  app/orchestrator.py runs in the background, never inside this request.
- get_status(): short spoken-friendly summary of open jobs.
- approve_action(job_id, approved: bool): needs_approval -> approved (or failed/cancelled).
  The only path to an irreversible action.

- init: ElevenLabs "conversation initiation client data" webhook, called when a call starts.
  Receives {caller_id, agent_id, called_number, call_sid, conversation_id} and returns
  {"type": "conversation_initiation_client_data", "dynamic_variables": {...}}. This is the caller
  allowlist: an unknown caller_id gets a refusal greeting and the agent calls end_call.
  ElevenLabs has no built-in caller allowlist (docs read 2026-10-03). TODO(verify) whether this
  webhook can reject a call outright.

Hard rules: answer in < 500 ms; never block on a worker; check X-Shotgun-Secret
(TOOLS_SHARED_SECRET); every tool body carries `caller` (bound to system__caller_id) and must
equal ALLOWED_CALLER_NUMBER.
"""

from fastapi import APIRouter

router = APIRouter(prefix="/tools")
