# Shotgun — Stack & Build Plan

Oct 3, 2026 · @Daniel

Shotgun is an AI agent saved as a phone contact: it rings your car when you plug in, takes tasks by voice, hands them to worker agents, and calls back with results before you park. Built solo for MHacks 2026 in 24 hours on a 2026 Honda Civic with wired CarPlay.

## Product

The pitch: everyone put a chatbot in the car; Shotgun is an agent built for the car. It starts itself, uses your arrival time as the deadline, and works in the background because you can't look at a screen.

What it must do on demo day:

1. Plug in the phone; within about 10 seconds the car rings and shows "Shotgun".
2. You speak a multi-part request; the voice agent confirms it, dispatches jobs and hangs up.
3. Workers run in parallel: coding PR, email, food, research.
4. When a job finishes or needs approval, the car rings again with a short summary.
5. Nothing irreversible (send, order, pay, merge) happens without a spoken "yes".

Demo script (target 60 seconds):

> Plug in → ring → "Morning. 31-minute drive home. Anything you want handled?" → "Order my usual ramen so it's there when I get home, text Alex to come over, and fix the login bug Sarah filed." → "On it." → later ring → "Ramen is $21.40 with tip, ordering in 6 minutes so it lands at 7:12. Confirm?" → "Yes."

Out of scope: controlling the car, real-inbox Gmail, restaurant reservations, a native CarPlay app (needs Apple entitlement), Siri anywhere in the flow.

## Architecture

&#91;embedded content: Shotgun architecture · 3 entry points, 1 server, 4 workers\]

Plug-in events and finished jobs both go through the call trigger, so the car only rings when there is something worth hearing; the voice agent never waits on a worker.

## Stack

No self-hosted models: every model is an API. You host one small Python server; everything else is configured, not built.

| Layer | Choice | Sponsor prize | Notes |
| --- | --- | --- | --- |
| Trigger | iOS Shortcuts automation: "CarPlay connects / disconnects" → POST /events | — | Only Apple dependency; Run Immediately, no Siri |
| Phone + voice | ElevenLabs Agents + Twilio voice number | ElevenLabs (sponsor + MLH) | Inbound and outbound calls; tools call our webhooks |
| Voice-turn model | Claude Haiku 4.5, selected inside ElevenLabs | — | Confirm Claude is selectable; else custom-LLM endpoint |
| Server | Python FastAPI on Railway | — | /events, /tools/\*, /github/hook; ngrok as fallback |
| Orchestrator | Claude Sonnet 5.5 via Anthropic API, tool use | Fetch.ai ASI:One (wrapped as an Agentverse agent) | Plans request → jobs with deadlines |
| Job table | Neon Postgres | Neon | Shared state: queued → running → needs\_approval → approved → done / failed |
| Coding worker | Claude Code GitHub Action on a demo repo | — | We open an issue mentioning @claude; it opens the PR |
| Email worker | Composio Gmail tools, throwaway Gmail account | — | Skips Google OAuth setup |
| Food worker | DoorDash CLI on a Mac (waitlist) or browser agent that stops at cart | — | Mac-only CLI runs as a local worker polling the job table |
| Maps | Google Routes API + Places API | — | ETA deadlines and restaurant lookup |
| Planning | Notability Pro | Notability | Sketches + 2 screenshots for Devpost |

## Build steps

Each step has one pass/fail check you can run on its own. Core rows total about 18 hours; everything totals about 25.5, so stretch rows go in this order only after milestone 1 works: 6.1 Fetch.ai, 3.2 email, 3.5 research, 4.3 call policy.

| ID | Step | Needs | Pass when | Hrs | Priority | Status |
| --- | --- | --- | --- | --- | --- | --- |
| 0.1 | Join the DoorDash CLI waitlist | — | Confirmation email received | 0.1 | Core | Not started |
| 0.2 | Create accounts and keys: Anthropic, ElevenLabs, Twilio, Railway, Neon, Composio, GitHub, Google Maps | — | check\_keys.py prints OK for every key | 0.75 | Core | In progress: Anthropic, ElevenLabs, Twilio OK; still missing Neon, GitHub, Composio, Google Maps, Railway login, the 3 secrets, ALLOWED_CALLER_NUMBER. Check: `make check-keys` |
| 0.3 | Notability: architecture sketch and wireframes | — | 2+ screenshots saved | 0.25 | Stretch | Not started |
| 1.1 | ElevenLabs agent on a Twilio number; save the "Shotgun" contact | 0.2 | Calling it from the Civic: agent greets through the car speakers, contact name on screen | 0.5 | Core | Not started |
| 1.2 | Outbound call through the ElevenLabs API | 1.1 | One curl makes the phone ring within 5 s | 0.25 | Core | Done (2026-10-03 14:21): rang within 5 s (Daniel). Second call 14:20 hung up on its own after 20 s silence. 1.1's Civic check still pending |
| 1.3 | CarPlay-connect automation posting to webhook.site | — | 5 of 5 replugs logged with the phone locked; location present; latency noted | 0.5 | Core | Not started |
| 1.4 | FastAPI skeleton deployed on Railway | 0.2 | GET /health on the public URL returns 200 | 0.5 | Core | Not started |
| 1.5 | /events triggers an outbound call, shared-secret check | 1.2, 1.4 | curl /events rings the phone; wrong secret returns 401 | 0.5 | Core | Blocked: /events + offline tests done (401/202 checked locally); needs 1.2 + 1.4, then curl the Railway URL |
| 1.6 | Milestone 1: plug in → car rings | 1.3, 1.5 | 3 of 3 in the Civic, ring within 10 s | 0.5 | Core | Not started |
| 2.1 | Job table in Neon with state transitions | 0.2 | Tests: create job, legal transitions pass, illegal ones raise | 1 | Core | Blocked: code + tests pass offline; run `make test-neon` once DATABASE_URL is set |
| 2.2 | Tool webhooks: dispatch\_task, get\_status, approve\_action | 2.1 | Sample ElevenLabs payloads via curl answer in under 500 ms and write rows | 1 | Core | Blocked: done offline; local curl samples 200 in ≤ 3 ms and write rows. Needs Neon (2.1) + deploy, then `make curl-tools` |
| 2.3 | Voice prompt and tools wired in ElevenLabs; caller allowlist | 1.1, 2.2 | Live 3-part request: agent confirms, says "on it", 3 rows appear; unknown number refused | 1 | Core | Not started |
| 2.4 | Orchestrator planner (Sonnet): request → job list with deadlines | 2.1 | 5 fixture utterances produce the expected job types and deadlines | 1.5 | Core | Blocked: done offline (11 tests). Live check: `make test-live T=tests/test_step_2_4_orchestrator.py` (5 utterances) once the Anthropic key is workspace-scoped |
| 3.1 | Coder worker plus demo repo with a planted bug | 2.1 | Job inserted by hand → PR opens and webhook marks job done within 10 min | 2 | Hero (pick one) | Blocked: done offline (21 tests). Needs Neon + deploy, `/install-github-app` + `ANTHROPIC_API_KEY` secret on the demo repo, `make github-hook`, then `make coder-demo` (PR within 10 min) |
| 3.2 | Email worker via Composio | 2.1 | Job → draft in Gmail, status needs\_approval; approve → sent | 1.5 | Stretch | Not started |
| 3.4 | Food worker: DoorDash CLI, or browser agent stopping at cart | 0.1, 2.1 | Job → cart with the right items and total; order placed only after approval | 2.5 | Hero (pick one) | Not started |
| 3.5 | Research worker via Places API | 2.1 | Job → 3 open restaurants near the destination with hours | 1 | Stretch | Not started |
| 4.1 | Callback watcher places an outbound call with a summary | 1.2, 2.1 | Job flipped to done by hand → phone rings and reads the summary | 1 | Core | Blocked: done offline (15 tests). Needs 1.2 + Neon, then `make callback-demo` and Daniel confirms the ring |
| 4.2 | Spoken approval loop | 2.3, 4.1 | "Yes" runs the action, "no" cancels; both logged | 1 | Core | Not started |
| 4.3 | Call policy: cooldown, drive of 10+ min or pending items | 1.5 | Unit test per rule passes | 0.5 | Stretch | Not started |
| 5.1 | ETA from the Routes API | 1.3 | Coordinates → minutes within 2 of Google Maps | 0.75 | Core | Not started |
| 5.2 | Deadline scheduling ("there when I get home") | 2.4, 5.1 | Fake-clock test: order fires at ETA minus prep time | 1 | Core | Not started |
| 6.1 | Fetch.ai: orchestrator as an Agentverse agent, discoverable in ASI:One | 2.4 | A message from ASI:One creates a job | 2.5 | Stretch | Not started |
| 7.1 | Full rehearsal in the Civic | Core rows | 3 clean runs back to back | 1 | Core | Not started |
| 7.2 | Record the demo video and a backup take | 7.1 | Filmed parked or with a second driver; backup saved | 1 | Core | Not started |
| 7.3 | Slides, Devpost, ASI submission agent | 7.2 | Devpost lists every sponsor tag; ASI submission confirmed | 1.5 | Core | Not started |

## Decision points and open questions

- [x] Hour 1: does Claude appear as a model choice in ElevenLabs Agents? If not, use its custom-LLM endpoint pointed at our server. *Yes: the agent runs `claude-haiku-4-5` (set via API 14:14, read back OK).*
- [x] Hour 1: read the Fetch.ai hackpack — is their own LLM or a specific chat protocol required? *Agent Chat Protocol required; ASI-1 LLM not required (DECISIONS §6a).*
- [ ] Hour 10: hero worker. DoorDash approved and an Apple Silicon Mac on hand → food (3.4). Otherwise → coding PR (3.1).
- [x] Check Devpost rules: how many sponsor prizes can one project enter? *As many as eligible; one main track. Winning several isn't stated (DECISIONS §6).*
- [ ] Destination source for ETA: next calendar event, a fixed home/work address, or the agent asks.

## Risks and fallbacks

| Risk | Fallback |
| --- | --- |
| Plug-in automation fires late or not when locked | Tap the Shotgun contact in Phone favorites on the Civic screen |
| Outbound call takes more than 10 s to ring | Pitch it as "rings while you buckle up"; trim server work before the call |
| Coding agent fails live | Small demo repo, well-described planted bug, pre-recorded successful run |
| DoorDash CLI not approved | Browser agent that stops at a ready cart; checkout only after approval |
| Browser agent blocked by bot checks | Show the cart step on a recording; make the coding PR the live hero |
| Voice latency feels slow | Haiku for voice turns; the voice agent never waits on workers |
| Running out of time | Cut stretch rows in reverse of the order in Build steps; milestone 1 + one hero + callback is a complete demo |
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
