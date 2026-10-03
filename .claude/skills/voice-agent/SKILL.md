---
name: voice-agent
description: Use when creating, changing or debugging the Shotgun ElevenLabs voice agent - prompt, voice, LLM, webhook tools, the Twilio number, caller allowlist, or how outbound callbacks pass a summary (steps 1.1, 1.2, 2.3, 4.1, 4.2). Source of truth is config/elevenlabs_agent.json.
---

# The ElevenLabs voice agent

Config lives in `config/elevenlabs_agent.json` (agent body + tool definitions). Change the file first, then apply it with `uv run python scripts/apply_agent.py --stage greet|full` (add `--dry-run` to preview), so the repo always matches what's live. The script is idempotent: it creates or updates the workspace secret, the tools (matched by name), the agent (`ELEVENLABS_AGENT_ID`, or creates one) and the phone number (imports it from Twilio, or reassigns it). API facts below were read from elevenlabs.io/docs and the OpenAPI spec on 2026-10-03; re-check anything that errors.

## Prompt principles (D17: the agent stays on the call)
The prompt lives in `config/elevenlabs_prompt.md`; `apply_agent.py` inlines it (`prompt_file`).
- **Short:** one or two sentences per turn, spoken style, no lists or URLs. Never open with "How can I help?".
- **Inline first:** quick questions go to `search_web`, messages to `draft_message` (read back word for word). Say a filler first; `pre_tool_speech: "force"` on both tools backs that up. Timeout 15 s; the server answers within 8 s or returns a spoken fallback.
- **Dispatch with a yes up front:** one `dispatch_task(type, details, label, preapproval?)` per job. For anything irreversible, ask for the yes now and repeat the condition back ("Got it: I'll merge it if the tests pass"). Without a yes, the job is held for the arrival call.
- **Stay on the call.** `end_call` only for: a goodbye or "that's all"; a refused caller; or silence. Silence: `turn.turn_timeout` is at most 30 s (docs), so after 30 s the agent calls `skip_turn` (`wait_timeout_secs` 30). After the wait it checks in with "Anything else?" (about 60 s); the next silent turn (about 90 s) says "OK, talk later." and calls `end_call`. `silence_end_call_timeout` 90 is the backstop.
- **Arrival / exception calls:** the summary arrives in `{{summary}}`/`{{greeting}}`; `{{call_kind}}` says which call it is. If `{{pending_job_id}}` is set, only a clear yes calls `approve_action(approved=true)`; "maybe", "hold on" or silence never count.
- **Safety:** nothing irreversible without a spoken yes (pre-approval or `approve_action`).

## Model
`conversation_config.agent.prompt.llm = "claude-haiku-4-5"` (in the API's LLM enum on 2026-10-03; TODO(verify) it shows up in the agent UI, step 1.1). Fallback: `llm: "custom-llm"` with `prompt.custom_llm = {url, model_id, api_key: {secret_id}, api_type: "chat_completions"}` pointed at our server. That needs an OpenAI-compatible streaming `/v1/chat/completions` (SSE `data: {...}` ending in `data: [DONE]`).

## Twilio number (step 1.1)
- Buy a voice-capable US number in Twilio. No SMS or A2P registration needed.
- Import it: ElevenLabs → Phone Numbers → Twilio (label, number, Account SID, Auth Token), or `POST /v1/convai/phone-numbers {provider:"twilio", phone_number, label, sid, token, agent_id}`. The response's `phone_number_id` goes in `ELEVENLABS_PHONE_NUMBER_ID`.
- Assign the agent for inbound calls. ElevenLabs configures the Twilio webhooks.

## Tools (step 2.3)
- Tools are separate resources: `POST /v1/convai/tools` with each `tools[]` entry from the config, then put the returned ids in `conversation_config.agent.prompt.tool_ids` (inline `prompt.tools` is deprecated).
- Webhook tools: `tool_config.type = "webhook"`, `api_schema {url, method, request_headers, request_body_schema}`. `response_timeout_secs` is 5 to 300 (OpenAPI 2026-10-03): 5 for the background tools (< 500 ms), 15 for the inline ones (< 8 s). Body properties can be nested objects (`type: object`, `properties`, `required`), which is how `preapproval` is sent. Bind our own dynamic variables the same way as system ones (`get_status.drive_id` → `drive_id`).
- Auth header: `X-Shotgun-Secret` whose value is an ElevenLabs workspace secret (`{"secret_id": ...}`) holding `TOOLS_SHARED_SECRET`. Created by `POST /v1/convai/secrets {type: "new", name, value}`, updated by `PATCH /v1/convai/secrets/{id} {type: "update", ...}`, both handled by the script.
- The caller number goes into each body via `"dynamic_variable": "system__caller_id"`. Other system vars: `system__conversation_id`, `system__call_sid`, `system__called_number`. Each tool also binds `called` to `system__called_number`. Which of the two holds Daniel's number on **outbound** callbacks is unverified, so the server accepts the allowed number in either field (`app/security.py`).
- `end_call` is a built-in system tool: `prompt.built_in_tools.end_call = {type: "system", name: "end_call", params: {system_tool_type: "end_call"}}` (OpenAPI, 2026-10-03). The prompt must tell the agent when to call it.

## Caller allowlist
There is **no built-in phone allowlist** (`platform_settings.auth.allowlist` is for web hosts). Two layers:
1. The conversation initiation webhook (`POST {{BASE_URL}}/tools/init`, enabled with `platform_settings.overrides.enable_conversation_initiation_client_data_from_webhook`) gets `{caller_id, agent_id, called_number, call_sid, conversation_id}`. For an unknown caller, return dynamic variables with a refusal greeting so the agent says "Sorry, this line is private" and calls `end_call`. The response must define **every** dynamic variable the agent uses. TODO(verify) whether it can reject outright.
2. Every `/tools/*` handler (`app/security.py: check_caller`) returns 403 unless `ALLOWED_CALLER_NUMBER` equals the body's `caller` (`system__caller_id`) **or** `called` (`system__called_number`). Either one counts because it's unverified which field holds Daniel's number on an outbound callback. A stranger dialling in has their own number as caller and the Twilio number as callee, so they're still refused. Numbers are normalised to `+digits`.

## Outbound calls and summaries (steps 1.2, 4.1)
```
POST https://api.elevenlabs.io/v1/convai/twilio/outbound-call
xi-api-key: $ELEVENLABS_API_KEY
{"agent_id": "...", "agent_phone_number_id": "...", "to_number": "+1...",
 "conversation_initiation_client_data": {"dynamic_variables": {
    "greeting": "Ramen is $21.40 with tip, it lands at 7:12. Confirm?",
    "summary": "...", "pending_job_id": "42", "eta_minutes": "31"}}}
-> {"success": true, "conversation_id": "...", "callSid": "..."}
```
- Use dynamic variables, not `conversation_config_override` (overrides need per-field permission in `platform_settings.overrides`).
- Optional `telephony_call_config.ringing_timeout_secs` (default 60).
- Post-call webhook (optional): `post_call_transcription` / `call_initiation_failure` events, signed with HMAC in the `elevenlabs-signature` header. Match on `conversation_id`.

## Debugging checklist
- Agent silent or slow: check the LLM choice and that tools return in < 500 ms (server logs).
- Tool 401: secret header mismatch between the ElevenLabs workspace secret and Railway `TOOLS_SHARED_SECRET`.
- Callback reads `{{summary}}` literally: the dynamic variable is missing from the request.
- After a base URL change, run the `deploy` skill's "update tool URLs" step.
