"""ElevenLabs outbound-call client (Twilio number imported into ElevenLabs).

Build step 1.2: one call makes the phone ring within 5 s.

Per the ElevenLabs API docs (read 2026-10-03, see docs/DECISIONS.md section 11):
    POST https://api.elevenlabs.io/v1/convai/twilio/outbound-call
    Header: xi-api-key: <ELEVENLABS_API_KEY>
    Body:   {"agent_id": ELEVENLABS_AGENT_ID,
             "agent_phone_number_id": ELEVENLABS_PHONE_NUMBER_ID,
             "to_number": MY_PHONE_NUMBER,
             "conversation_initiation_client_data": {
                 "dynamic_variables": {"greeting": "...", "summary": "...",
                                       "eta_minutes": "31", "pending_job_id": ""}}}
    Response: {"success": true, "conversation_id": "...", "callSid": "..."}

Per-call text goes in dynamic variables, which the agent's first_message ({{greeting}}) and
prompt reference. That needs no override permission, unlike conversation_config_override.
"""
