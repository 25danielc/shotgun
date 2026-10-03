# Shotgun — Stack & Build Plan

Oct 3, 2026 · @Daniel

Shotgun is an AI agent saved as a phone contact: it rings your car when you plug in, takes tasks by voice, answers quick questions on the spot, hands longer jobs to worker agents, and rings once more with a batched summary just before you park (at most two calls per drive, D17). Built solo for MHacks 2026 in 24 hours on a 2026 Honda Civic with wired CarPlay.

## Product

The pitch: everyone put a chatbot in the car; Shotgun is an agent built for the car. It starts itself, uses your arrival time as the deadline, and works in the background because you can't look at a screen.

What it must do on demo day:

1. Plug in the phone; within about 10 seconds the car rings and shows "Shotgun".
2. You speak a multi-part request. Searches and drafts are answered on the same call; longer jobs are dispatched, with any "yes" asked up front (pre-approval). The agent stays on the line until you say goodbye.
3. Workers run in parallel in the background: coding PR (hero), research; email and food are stretch.
4. About 3 minutes before you arrive, the car rings once with a batched summary: done, waiting on you, failed. Exception calls only when a pre-approved action can't go ahead as agreed.
5. Nothing irreversible (send, order, pay, merge) happens without a spoken "yes", given at dispatch or on the arrival call.
6. Unplugging closes the drive and sends a push recap.

Demo script (target 60 seconds):

> Plug in → ring → "Hey, it's Shotgun. Where are you headed?" → "Home." → "22 minutes. Anything you want handled?" → "What's the score of the Michigan game?" → "One sec, checking… Michigan's up 21 to 14 in the third." → "And fix the login bug Sarah filed." → "I'll have Claude open a PR. Merge it if the tests pass?" → "Yes." → "Got it: merge if the tests pass. You'll hear back before you park." → "That's all." → "Bye." → *(3 min before arrival)* ring → "Shotgun here. The login fix is merged; the tests passed." (D17: coding PR only, no email line)

Out of scope: controlling the car, real-inbox Gmail, restaurant reservations, a native CarPlay app (needs Apple entitlement), Siri anywhere in the flow.

## Architecture

&#91;embedded content: Shotgun architecture · 3 entry points, 1 server, 4 workers\]

Two calls per drive (D17): the plug-in event goes through the call policy (4.3) for the departure call, and the arrival scheduler (4.1) rings once at ETA − 3 min with every job in the drive. Inline tools (search, draft, destination) answer during the call in under 8 s; background tools never wait on a worker.

## Stack

No self-hosted models: every model is an API. You host one small Python server; everything else is configured, not built.

| Layer | Choice | Sponsor prize | Notes |
| --- | --- | --- | --- |
| Trigger | iOS Shortcuts automation: "CarPlay connects / disconnects" → POST /events | — | Only Apple dependency; Run Immediately, no Siri |
| Phone + voice | ElevenLabs Agents + Twilio voice number | ElevenLabs (sponsor + MLH) | Inbound and outbound calls; tools call our webhooks |
| Voice-turn model | Claude Haiku 4.5, selected inside ElevenLabs | — | Confirm Claude is selectable; else custom-LLM endpoint |
| Server | Python FastAPI on Railway | — | /events, /tools/\*, /github/hook; ngrok as fallback |
| Orchestrator | Claude Sonnet 5.5 via Anthropic API, tool use | Fetch.ai ASI:One (wrapped as an Agentverse agent) | Plans request → jobs with deadlines |
| Job table | Neon Postgres | Neon | Shared state: `drives` + `jobs`. queued → running → (needs\_approval | exception) → approved → done / failed; pre-approved jobs skip the question |
| Coding worker | Claude Code GitHub Action on a demo repo | — | We open an issue mentioning @claude; it opens the PR |
| Email worker | Composio Gmail tools, throwaway Gmail account | — | Skips Google OAuth setup |
| Food worker | DoorDash CLI on a Mac (waitlist) or browser agent that stops at cart | — | Mac-only CLI runs as a local worker polling the job table |
| Maps | Google Routes API | — | ETA once at departure, for the arrival call (research uses Claude web search, D15) |
| Unplug recap | ntfy push (`NTFY_TOPIC`) | — | Only the unplug recap; calls stay the main channel (D6, D17) |
| Planning | Notability Pro | Notability | Sketches + 2 screenshots for Devpost |

## Build steps

Each step has one pass/fail check you can run on its own. Core rows total about 18 hours; everything totals about 25.5, so stretch rows go in this order only after milestone 1 works: 3.2 email. D17 (17:43) reopened 2.2, 2.3, 4.1, 4.2, promoted 4.3 and 5.1 to core, added 4.4 and 4.5, and cut 6.1. Build order: 2.2 → 2.3 → 4.2 → 4.1 → 4.3 → 4.4 → 4.5 → 5.1.

| ID | Step | Needs | Pass when | Hrs | Priority | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 0.1 | Join the DoorDash CLI waitlist | — | Confirmation email received | 0.1 | Core | Not started |
| 0.2 | Create accounts and keys: Anthropic, ElevenLabs, Twilio, Railway, Neon, Composio, GitHub, Google Maps | — | check\_keys.py prints OK for every key | 0.75 | Core | In progress: 12/14 OK (GitHub fixed). Composio + Google Maps not added yet. Check: `make check-keys` |
| 0.3 | Notability: architecture sketch and wireframes | — | 2+ screenshots saved | 0.25 | Stretch | Not started |
| 1.1 | ElevenLabs agent on a Twilio number; save the "Shotgun" contact | 0.2 | Calling it from the Civic: agent greets through the car speakers, contact name on screen | 0.5 | Core | Not started |
| 1.2 | Outbound call through the ElevenLabs API | 1.1 | One curl makes the phone ring within 5 s | 0.25 | Core | Done (2026-10-03 14:21): rang within 5 s (Daniel). Second call 14:20 hung up on its own after 20 s silence. 1.1's Civic check still pending |
| 1.3 | CarPlay-connect automation posting to webhook.site | — | 5 of 5 replugs logged with the phone locked; location present; latency noted | 0.5 | Core | Not started |
| 1.4 | FastAPI skeleton deployed on Railway | 0.2 | GET /health on the public URL returns 200 | 0.5 | Core | Done (2026-10-03 14:37): https://shotgun-production-5f30.up.railway.app/health → 200 |
| 1.5 | /events triggers an outbound call, shared-secret check | 1.2, 1.4 | curl /events rings the phone; wrong secret returns 401 | 0.5 | Core | Done (2026-10-03 15:06): the iPhone Shortcut posted to Railway → 202 → the call rang and the agent greeted Daniel (conv_9101…). Wrong secret → 401. Note: the Shortcut sent no location yet |
| 1.6 | Milestone 1: plug in → car rings | 1.3, 1.5 | 3 of 3 in the Civic, ring within 10 s | 0.5 | Core | In progress: 1 of 3 in the Civic (~15:47, plug-in rang, agent took a request). 2 more to go |
| 2.1 | Job table in Neon with state transitions | 0.2 | Tests: create job, legal transitions pass, illegal ones raise | 1 | Core | Done (2026-10-03 14:40): `make test-neon`, 173 passed on Neon (PostgreSQL 18.6); `make db-init` created the tables |
| 2.2 | Tools split (D17). Inline, < 8 s: `search_web` (Haiku + Claude web search), `draft_message`. Background, < 500 ms: `dispatch_task(type, details, preapproval?)`, `get_status(drive_id?)`, `approve_action`. Schema: `drives` table, `jobs.drive_id`, `jobs.preapproval` | 2.1 | `search_web` answers 5 sample queries in under 8 s each (live); `draft_message` returns draft text; background samples answer in under 500 ms and write a row with drive\_id and preapproval | 1.5 | Core | Done (2026-10-03 17:53): live `search_web` on 5 sample queries took 2.2–2.7 s each and `draft_message` 0.7 s (`make test-live T=tests/test_step_2_2_inline_tools.py`). Background samples under 500 ms, writing drive\_id and preapproval (offline, and the suite on Neon). The curl against Railway runs with the 2.3 deploy |
| 2.3 | Voice agent stays on the call (D17): prompt in `config/elevenlabs_prompt.md`, `end_call` only for goodbye, a refused caller or 60 s + 30 s of silence, inline tool timeout about 15 s, pushed with `scripts/apply_agent.py`; caller allowlist | 1.1, 2.2 | Config tests pass offline. **Daniel, in car:** one call with a search question then a coding task with pre-approval: zero drops, zero outbound calls until arrival; unknown number refused | 1 | Core | Blocked: config built and tested offline (prompt file, 5 tools, end\_call for 3 reasons, silence 30 s skip\_turn + 30 s wait + end). Waiting on: Daniel OKs the ElevenLabs push, then a desk call. The in-car check runs after 4.2 and 4.1, because until then the old per-job watcher still rings after the call |
| 2.4 | Orchestrator planner (Sonnet): request → job list with deadlines | 2.1 | 5 fixture utterances produce the expected job types and deadlines | 1.5 | Core | Done (2026-10-03 14:47): 5/5 fixture utterances against claude-sonnet-5-5 (`make test-live T=tests/test_step_2_4_orchestrator.py`) |
| 3.1 | Coder worker plus demo repo with a planted bug | 2.1 | Job inserted by hand → PR opens and webhook marks job done within 10 min | 2 | Hero (pick one) | Done (2026-10-03 16:43): `make coder-demo` job 85 → issue #1 → Action → PR #2 → needs_approval in 1.7 min |
| 3.2 | Email worker via Composio | 2.1 | Job → draft in Gmail, status needs\_approval; approve → sent | 1.5 | Stretch | Not started |
| 3.4 | Food worker: DoorDash CLI, or browser agent stopping at cart | 0.1, 2.1 | Job → cart with the right items and total; order placed only after approval | 2.5 | Hero (pick one) | Not started |
| 3.5 | Research worker via Claude web search (D15) | 2.1 | Job → 3 open restaurants near the destination with hours | 1 | Stretch | Done (2026-10-03 17:25): live lookup → 3 open ramen places near home with closing times in 3.6 s (basic web search since 17:45). Names only places whose hours it can verify, never guesses. `make test-live T=tests/test_step_3_5_research_worker.py` |
| 4.1 | Arrival and exception calls (replace the per-job callback watcher): one call at ETA − 3 min (or once every job in the drive is terminal or held) with a batched summary; no jobs, no call; exception calls at most one per 10 min | 2.2, 4.2 | Test with a mocked ElevenLabs client: a drive with 2 pre-approved jobs makes exactly 2 outbound calls in total; a drive with no jobs makes only the departure call | 1.5 | Core | Blocked: offline pass check passes (`tests/test_step_4_1_calls.py`: 2 pre-approved jobs → exactly departure + arrival; an exception rings once; 10-min gap). The per-job watcher is deleted. Waiting on: deploy, then **Daniel** hears a real arrival call |
| 4.2 | Approval: pre-approval at dispatch, held approvals and exceptions. Edge running → approved for pre-approved jobs; new `exception` state; coder checks `require_tests_pass` (demo repo `tests.yml`) before merging | 2.3 | Tests: a pre-approved job reaches done with no call; a job that breaks its pre-approval goes to `exception` and causes at most one exception call; a job with no pre-approval is held for arrival. **Daniel:** a spoken "no" cancels | 1.5 | Core | Blocked: built, offline tests pass (`tests/test_step_4_2_approvals.py`); the demo repo has a Tests workflow (passes on main) and its hook now sends `check_run`. Live run still to do: `make coder-demo PRE=1` once 4.1 is deployed and the planted bug is restored. **Daniel:** a spoken "no" cancels |
| 4.3 | Departure call policy: pending items, a known drive of 10+ min, or no call in 30 min; thresholds in config; `CALL_POLICY=always` for rehearsals | 1.5, 2.2 | Unit test per rule passes, plus the override | 0.5 | Core | Not started |
| 4.4 | Unplug recap: `carplay_disconnected` (alias `car_disconnected`) closes the drive, cancels the pending arrival call, sends an ntfy push (done / waiting on you) | 4.1 | Test: unplug cancels the arrival call and posts the recap to ntfy. **Daniel:** the push arrives on the phone | 0.5 | Core | Not started |
| 4.5 | Demo tooling: `make demo-call` (simulated plug-in), `make demo-arrive` (arrival call now), `make watch` (live drive and job states) | 4.1 | Each command works against a local server with mocked telephony | 0.75 | Core | Not started |
| 5.1 | ETA from the Routes API, once at departure: the Shortcut sends lat/lng; inline `set_destination(text)` → Routes (traffic-aware) → drive destination, eta, arrival\_call\_at = now + duration − 3 min | 1.3, 4.5 | Pass checks to confirm with Daniel (his message was cut off). Proposed: coordinates → minutes within 2 of Google Maps (live); `set_destination` answers in under 8 s; no location or no destination falls back to the "all terminal or held" trigger (test). **Daniel:** edit the plug-in Shortcut | 1 | Core | Not started |
| 5.2 | Deadline scheduling ("there when I get home") | 2.4, 5.1 | Fake-clock test: order fires at ETA minus prep time | 1 | Core | Not started |
| 6.1 | Fetch.ai: orchestrator as an Agentverse agent, discoverable in ASI:One | 2.4 | A message from ASI:One creates a job | 2.5 | Cut | Cut (D17, 17:43): time; the core car demo comes first |
| 7.1 | Full rehearsal in the Civic | Core rows | 3 clean runs back to back | 1 | Core | Not started |
| 7.2 | Record the demo video and a backup take | 7.1 | Filmed parked or with a second driver; backup saved | 1 | Core | Not started |
| 7.3 | Slides, Devpost, ASI submission agent | 7.2 | Devpost lists every sponsor tag; ASI submission confirmed | 1.5 | Core | Not started |

## Decision points and open questions

- [x] Hour 1: does Claude appear as a model choice in ElevenLabs Agents? If not, use its custom-LLM endpoint pointed at our server. *Yes: the agent runs `claude-haiku-4-5` (set via API 14:14, read back OK).*
- [x] Hour 1: read the Fetch.ai hackpack — is their own LLM or a specific chat protocol required? *Agent Chat Protocol required; ASI-1 LLM not required (DECISIONS §6a).*
- [ ] Hour 10: hero worker. DoorDash approved and an Apple Silicon Mac on hand → food (3.4). Otherwise → coding PR (3.1). *The D17 demo script is coding PR only.*
- [x] Check Devpost rules: how many sponsor prizes can one project enter? *As many as eligible; one main track. Winning several isn't stated (DECISIONS §6).*
- [x] Destination source for ETA: next calendar event, a fixed home/work address, or the agent asks. *Was fixed `HOME_ADDRESS` (D16); now the agent asks on the departure call (D17, step 5.1).*

## Risks and fallbacks

| Risk | Fallback |
| --- | --- |
| Plug-in automation fires late or not when locked | Tap the Shotgun contact in Phone favorites on the Civic screen |
| Outbound call takes more than 10 s to ring | Pitch it as "rings while you buckle up"; trim server work before the call |
| Coding agent fails live | Small demo repo, well-described planted bug, pre-recorded successful run |
| DoorDash CLI not approved | Browser agent that stops at a ready cart; checkout only after approval |
| Browser agent blocked by bot checks | Show the cart step on a recording; make the coding PR the live hero |
| Voice latency feels slow | Haiku for voice turns; a filler line ("one sec, checking") before inline tools; background tools never wait on workers |
| Call policy silences the plug-in ring during rehearsal | `CALL_POLICY=always` |
| No location or destination, so no ETA | Arrival call rings once every job is terminal or held |
| Running out of time | Cut stretch rows in reverse of the order in Build steps; milestone 1 + one hero + the arrival call is a complete demo |
| Safety question from judges | Voice only, spoken confirmation for every irreversible action, long items wait until parked; demo filmed parked or with a second driver |

## Sources

- [MacRumors: iOS 26.4 brings CarPlay support for ChatGPT, Claude and Gemini](https://macrumors.com/2026/02/18/ios-26-4-carplay-support)
- [Engadget: CarPlay-connect automation with Run Immediately](https://engadget.com/2244156/how-to-add-intro-apple-carplay-with-shortcuts/)
- [DoorDash CLI on GitHub (waitlist, macOS Apple Silicon)](https://github.com/doordash-oss/doordash-cli)
- [The New Stack: DoorDash CLI for agents](https://thenewstack.io/doordash-cli-agents-order/)
- [Composio: Gmail tools for Claude agents](https://composio.dev/toolkits/gmail/framework/claude-agents-sdk.md)
- [Vapi docs: outbound calls (pre-ElevenLabs reference)](https://docs.vapi.ai/sdk/mcp-server.md)
- [Fetch.ai MHacks 2026 hackpack](https://www.fetch.ai/events/hackathons/mhacks-2026/hackpack)
- MHacks 2026 Tracks & Prizes page (pasted by Dan)
