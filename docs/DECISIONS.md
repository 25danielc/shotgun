# Shotgun: Decisions, context and constraints

This file is the project's memory. The chat where these decisions were made is gone, so if something matters and isn't here or in [PLAN.md](PLAN.md), it's lost. **PLAN.md is the source of truth for build steps, IDs, pass checks and priorities.** This file explains *why*, and records what was rejected, what's cut, and what's still open.

Rule: don't reopen a decision below without asking Daniel. Add new decisions to the [Decision log](#decision-log) at the bottom.

---

## 1. Who and when

| | |
|---|---|
| Builder | Daniel, solo |
| Event | MHacks 2026, Ann Arbor. 24 hours starting about **12:00 PM Sat Oct 3, 2026** (America/Detroit, EDT) |
| **Submission deadline** | **Sun Oct 4, 2026, 12:00 PM EDT.** The Devpost rules and the mhacks.org/live timeline ("Submissions Close @ 12 PM", 11:30 AM to 12:00 PM). The Devpost header says 12:15 PM EDT; treat that as a buffer, not the target. Checked 2026-10-03 at https://mhacks-2026.devpost.com/rules and https://www.mhacks.org/live |
| Judging | 12:30 to 3:00 PM EDT Sunday, science-fair style at your table. Winners at 5:00 PM EDT |
| Main rubric | Innovation, Technical Complexity, Usability, Adherence to Theme |
| Test car | 2026 Honda Civic, **wired** CarPlay over USB, iPhone |
| Mac | The setup laptop is an **Apple Silicon (arm64)** Mac. Whether it's the one at the venue is still to confirm (only matters for the DoorDash CLI) |

## 2. The product

Shotgun is an AI agent saved as a phone contact. Plug the phone into the car and, within about 10 seconds, the car rings with "Shotgun" on the screen. You speak a multi-part request; the voice agent confirms it, dispatches jobs and hangs up. Worker agents do the jobs in the background. When a job finishes or needs approval, the car rings again with a short summary, and **nothing irreversible happens without a spoken "yes"**.

- Pitch lines: **"Everyone put a chatbot in the car. Shotgun is an agent built for the car."** / **"Your agent isn't an app. It's a contact."**
- What makes it car-specific:
  1. The car tells the agent when to start (plug-in).
  2. Arrival time is the deadline ("You're 31 minutes out, so I'll order in 6").
  3. No screen forces short answers, spoken confirmation and background work.
- **Main track: Actually Intelligent (AI).** Finance and food are examples, not the category. (The track description was announced at the opening ceremony and isn't online yet; check that the pitch fits it.)
- **Demo (about 60 s, filmed parked or with a second driver):** plug in → ring → "Morning. 31-minute drive home. Anything you want handled?" → request → "On it." → later ring → result + "Confirm?" → "Yes." The full script is in PLAN.md and `.claude/skills/demo-prep`.
- **Safety story for judges:** voice only; spoken confirmation for every irreversible action; long items wait until parked; the demo is filmed parked or with a second driver.

## 3. Architecture

```
 ENTRY POINTS                          SERVER (FastAPI on Railway)                 WORKERS (poll / claim jobs)
 ┌──────────────────────┐  POST /events ┌──────────────────────────┐
 │ iPhone Shortcut      │──────────────▶│ /events + call trigger   │──ring──▶ ElevenLabs outbound call ──▶ car
 │ "CarPlay connects"   │ secret+loc    │   (ETA via Routes API)   │
 └──────────────────────┘               └──────────────────────────┘
 ┌──────────────────────┐ webhook tools ┌──────────────────────────┐ insert ┌────────────────────┐
 │ Car ↔ phone call     │──────────────▶│ /tools/dispatch_task     │───────▶│ Neon job table     │◀──┬─ coder (GitHub issue → Claude Code Action → PR)
 │ ElevenLabs Agent +   │  < 500 ms     │ /tools/get_status        │        │ queued→running→    │   ├─ email (Composio Gmail)
 │ Twilio number        │               │ /tools/approve_action    │        │ needs_approval→    │   ├─ food (DoorDash CLI on Mac / cart agent)
 └──────────────────────┘               └──────────────────────────┘        │ approved→done|failed│   └─ research (Places API)
 ┌──────────────────────┐ chat protocol ┌──────────────────────────┐        └─────────┬──────────┘
 │ ASI:One / Agentverse │──────────────▶│ Orchestrator (Sonnet 5.5)│ plans jobs       │ done / needs_approval
 │ (Fetch.ai, stretch)  │               │ request → jobs+deadlines │                  ▼
 └──────────────────────┘               └──────────────────────────┘        callback watcher ──ring──▶ car (summary, "Confirm?")
```

(PLAN.md's embedded diagram, "3 entry points, 1 server, 4 workers", didn't survive export. This is the text version.)

- **Trigger.** An iOS Shortcuts personal automation, "When CarPlay **connects**", set to Run Immediately: Get Current Location → Get Contents of URL (POST to `/events` with a shared secret). A matching "disconnects" automation. **This is the only Apple dependency.** The phone is "just a sensor": `/events` accepts `{source, event, location}` from any source so other triggers can be added later. Setup: [SHORTCUT_SETUP.md](SHORTCUT_SETUP.md).
- **Call trigger (server).** Rings the car on plug-in, when a job is done, or when a "yes" is needed. Call rules (cooldown, drive ≥ 10 min, pending items) are stretch (4.3); **for the demo, always call.**
- **Voice agent.** ElevenLabs Agents on a Twilio voice number, inbound and outbound through ElevenLabs' API. Voice-turn LLM is Claude Haiku 4.5, or failing that ElevenLabs' custom-LLM option pointed at our server. **The voice agent never waits on work**: it calls tools that return instantly.
- **Voice tools** (webhooks to our server): `dispatch_task`, `get_status`, `approve_action`. Must answer in **under 500 ms**.
- **Orchestrator.** Claude Sonnet (PLAN.md: **Sonnet 5.5**, `claude-sonnet-5-5`) via the Anthropic API with tool use. Plans a request into jobs with deadlines from the ETA. *Allowed time-saving fallback:* cut the separate planner and let the voice agent call `dispatch_task(type, details)` directly.
- **Job table.** Neon Postgres. States `queued → running → needs_approval → approved → done`, or `failed`. All agents share it; **no agent-to-agent protocol is needed.** As built: types are the four workers plus `plan` (a raw spoken request the orchestrator splits), every transition is logged in `job_events`, and `announced_state` records what the driver has heard (callbacks).
- **Workers.**
  - *Coder:* create a GitHub issue mentioning `@claude` in the separate demo repo (`shotgun-demo-app`); the Claude Code GitHub Action does the fix; a GitHub webhook moves the job on. **See the PR caveat in §9.**
  - *Email:* Composio Gmail tools on a throwaway Gmail account (avoids Google OAuth setup).
  - *Food:* the official DoorDash CLI (waitlist-only) as a local worker on a Mac that polls the job table. Fallback: a browser agent that stops at a ready cart.
  - *Research:* Google Places API.
- **Maps.** Google Routes API gives a traffic-aware ETA from the location the Shortcut sends; the Places API finds restaurants. Shortcuts can't read the active Apple Maps route, so the **destination comes from the next calendar event, a fixed home/work address, or the agent asking** (open question §10).
- **Second front door.** The orchestrator is also registered as a Fetch.ai agent on Agentverse, discoverable in ASI:One. Chat requests feed the same `dispatch_task`.
- **Hosting.** Python FastAPI on Railway, with ngrok from the laptop as a fallback. **No self-hosted models.**
- **Security.** Only accept calls from Daniel's number (caller allowlist). Shared secret on `/events`. Keys only in `.env` and Railway variables. (Added in setup: a second shared secret on the `/tools/*` webhooks, `TOOLS_SHARED_SECRET`, since they're public URLs. As built in 2.2: the caller check passes when `ALLOWED_CALLER_NUMBER` is the call's caller **or** callee, so it works for inbound calls and outbound callbacks alike. Everything fails closed: 401 bad secret, 403 stranger, 503 unconfigured.)

## 4. Decisions and why

| # | Decision | Why | Rejected alternatives |
|---|---|---|---|
| D1 | **Phone call instead of a native CarPlay app** | Third-party CarPlay voice apps need an Apple entitlement (days to weeks). Calls work in any car with CarPlay, Android Auto or Bluetooth. | Native CarPlay app (cut). iOS 26.4's CarPlay support for ChatGPT/Claude/Gemini is someone else's chatbot, not our agent |
| D2 | **No Siri anywhere** | Daniel doesn't want a dependency he can't control in the core flow. **Manual fallback is tapping the "Shotgun" contact in Phone favorites**, not "Hey Siri". | "Hey Siri, call Shotgun", Siri Shortcuts by voice |
| D3 | **ElevenLabs over Vapi** | Same capabilities, plus it qualifies for two prizes (ElevenLabs sponsor + MLH). | Vapi (PLAN.md keeps a Vapi doc link only as an outbound-call reference) |
| D4 | **Neon over SQLite** | Costs nothing extra and qualifies for the Neon prize. Also lets the Mac food worker and Railway share one table. | SQLite |
| D5 | **Railway, not Google Cloud Run** | Cloud Run's default CPU throttling between requests stalls background workers and delayed jobs. **The only Google products used are the Maps APIs.** | Cloud Run, Gemini, other GCP |
| D6 | **Calls instead of SMS for callbacks** | US business texting via Twilio needs A2P 10DLC carrier registration that takes days. Voice numbers don't. | Twilio SMS, iMessage |
| D7 | **Delegate coding to the Claude Code GitHub Action** | Don't build a coding agent. The Action is a configured product. | Custom coding agent, Claude Agent SDK worker |
| D8 | **Composio for Gmail instead of Google OAuth** | Composio-managed auth skips making a Google OAuth app and its consent-screen setup. | Gmail API with own OAuth client, real inbox |
| D9 | **Hero action defaults to the coding PR** | It depends only on us. **Switch to food timed to arrival only if DoorDash approves the waitlist and an Apple Silicon Mac is available. Decide by about hour 10** (~10 PM Sat). | Food as default hero |
| D10 | **No unofficial DoorDash MCP servers** | Don't hand an account and a card to unknown code. | Community DoorDash MCPs/scrapers |
| D11 | **Voice agent never waits on workers** | Tools return instantly (< 500 ms); work runs in the background; callbacks deliver results. | Synchronous tools that block the call |
| D12 | **Shared job table, no agent-to-agent protocol** | Simplest way for the voice agent, orchestrator, workers and Fetch.ai agent to coordinate. | Message bus, A2A protocols |
| D13 | **Orchestrator can be cut** | If time runs short, the voice agent calls `dispatch_task(type, details)` directly. | — |
| D14 | **Voice-turn model: Claude Haiku 4.5 inside ElevenLabs** | Low latency. ElevenLabs' LLM list includes `claude-haiku-4-5` (checked 2026-10-03, still to confirm in the UI). Fallback: custom LLM pointed at our server. | GPT/Gemini in ElevenLabs |
| D15 | **Research = Claude web search for everything** (Daniel, 16:55) | One worker, no extra key or API to wire up: Sonnet 5.5 with Anthropic's server-side web search tool answers place lookups and general questions alike. | Google Places API (New) for places plus a second path for other questions |
| D16 | **ETA destination = fixed `HOME_ADDRESS`** (Daniel, 16:55); **"text X" = email** (Daniel says "email" in the demo) | Simplest, works without calendar access. The address lives only in `.env` and Railway, never in git. | Next calendar event; the agent asks |

## 5. Cut from scope (don't build)

Capital One Nessie money worker · the receipt page and the .tech domain · Photon / Relay / Spacetime · any hardware (OBD-II, ESP32 button) · a native CarPlay app · Gemini · the Figma prize · real restaurant reservations · real-inbox Gmail · controlling the car · Siri anywhere in the flow.

## 6. Sponsor prizes being targeted

Devpost rules: *"You may submit to only one main MHacks track, but you may enter as many eligible sponsor tracks or prizes as you would like."* So entering all four below is allowed. Whether one project can **win** more than one isn't stated (open question §10).

| Prize | How we qualify | Requirement to remember |
|---|---|---|
| **Best Project Built with ElevenLabs** (sponsor) + **[MLH] Best Use of ElevenLabs** | The voice layer is ElevenLabs Agents | **Tag ElevenLabs on Devpost.** Sponsor prize is 3 months of the Scale tier per member; MLH prize is earbuds |
| **Fetch.ai ASI:One Agent Challenge** ($1,250 / $750 / $500 + internship interview) | Orchestrator registered on Agentverse, discoverable via ASI:One | See §6a. **Also submit through the ASI "MHacks Submission Agent", not just Devpost** |
| **Best Use of Neon Backend** ($1,000 / $500 / $100 in AI Gateway credits) | Job table on Neon Postgres | No extra requirement posted |
| **Best Use of Notability** (1 yr Notability Pro + merch) | Architecture sketch + wireframes made in Notability Pro (step 0.3) | **Tag Notability on Devpost with a note on how it was used and at least 2 screenshots** |

### 6a. Fetch.ai hackpack (read 2026-10-03, https://innovationlab.fetch.ai/events/hackathons/mhacks-2026/hackpack)

- **Required:** at least one agent registered on Agentverse; **Agent Chat Protocol implemented**; discoverable and usable directly in ASI:One; meaningful tool execution or multi-agent orchestration; **the main workflow runs entirely inside an ASI:One chat** (no custom frontend); a **public GitHub repo** with run instructions.
- **Not required:** ASI:One's own LLM (ASI-1). Any framework is allowed, so Claude is fine.
- **README must include:** the agent name and address, plus the badges `![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)` and `![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)`.
- **Demo video: 3 to 5 minutes.** This differs from our 60-second demo; see §9.
- **Judging:** Functionality 25%, Use of Fetch.ai tech 20% (Agentverse, Chat Protocol, Payment Protocol), Innovation 20%, Real-world impact 20%, UX 15%. Bonus for multi-agent work, Payment Protocol, ASI1 Interactive Cards, reliability, real-time data.
- **Submission:** in ASI:One, message the **MHacks Submission Agent** ("Hi") → "Create team (I'm the lead)". Give the project name, your name, email, team size (1), problem solved and the public GitHub URL. Optional: table number, demo video URL, Agentverse profile URL, ASI:One shared-chat URL (bonus). You get a Team ID `mhacks-...`. Dashboard: https://asi1.ai/festival/mhacks2026/dashboard
- Promo codes MHACKS26 / MHACKSAV give a free month of ASI:One Pro / Agentverse Premium.

## 7. Stretch order and fallback gates

- **Stretch order (only after milestone 1 works):** Fetch.ai (6.1) → email worker (3.2) → research worker (3.5) → call rules (4.3). Cut in reverse.
- **Gate, hour 4 (~4 PM Sat):** if plug-in → ring isn't working, make **tapping the contact** the trigger and move on.
- **Gate, hour 10 (~10 PM Sat):** pick the hero (D9).
- **Gate, hour 12 (~midnight):** if voice dispatch → callback isn't working end to end, **cut Fetch.ai**.
- **Never cut:** plug-in ring, callback, one real completed action.
- PLAN.md's "Risks and fallbacks" table lists the other fallbacks.

## 8. Steps that need Daniel in person

Claude must stop and ask, never pretend to verify these:

- **0.1** Join the DoorDash CLI waitlist (form: https://forms.gle/gvCQZvu9C1EKA6aM6).
- **0.2** Account creation (Anthropic, ElevenLabs, Twilio, Railway, Neon, Composio, GitHub, Google Maps).
- **0.3** Notability sketches + screenshots.
- **1.1, 1.3, 1.6** Civic tests.
- **7.1, 7.2** Rehearsal and filming.
- **Phone in hand** (added in setup): 1.2, 1.5, 4.1, 4.2, 2.3 pass only when Daniel's phone rings or he speaks to the agent. Claude can run the curl; Daniel confirms the ring.

## 9. Conflicts and findings from setup (2026-10-03)

PLAN.md wins on build steps; these are flagged for Daniel.

1. **Demo script vs default hero.** PLAN.md's script callback is the ramen order (food hero), but D9 makes the coding PR the default. If the PR is the hero, the callback line becomes something like "Sarah's login bug: I opened PR 4 with a fix. Merge it?" → "Yes."
2. **"Text Alex" has no worker.** SMS is out (D6) and email is the only messaging worker. Say "email Alex" in the script, or drop that part. As built (2.4): the planner turns "text X" into an email job. **Resolved (D16):** OK'd; Daniel says "email" in the demo.
3. **Coder "done" vs the approval rule.** The context says the GitHub webhook marks the job done; PLAN.md lists *merge* as irreversible. Proposed flow: PR opened → `needs_approval` → spoken "yes" → merge → `done`. Step 3.1's pass check ("PR opens and webhook marks job done") stays as written; read "done" there as "the worker's part is done".
4. **The Claude Code Action doesn't open the PR by itself.** Per its docs (capabilities-and-limitations.md, checked 2026-10-03), from an issue it pushes a `claude/...` branch and posts a link to a **prefilled PR creation page**. Fix in step 3.1: either our server opens the PR with the GitHub API when the `claude/*` branch appears (webhook `create`/`push`), or the workflow allows `gh pr create` (TODO(verify) the `claude_args`/allowed-tools syntax). The first option is simpler and fully under our control. **As built (3.1):** our webhook reads the Action's "[Create a PR](…/compare/base...claude/branch?quick_pull=1…)" link from its `issue_comment` (format from `src/entrypoints/update-comment-link.ts`), opens the PR through the API and moves the job to `needs_approval`.
5. **ElevenLabs has no built-in caller allowlist.** *(As built: `/tools/init`; the webhook fires only on inbound calls, or on outbound calls that carry no initiation data, which ours always do.)* Use the *conversation initiation client data webhook* (it receives `caller_id`) to give unknown callers a refusal greeting and `end_call`, **and** check `system__caller_id` in every tool webhook. Whether that webhook can reject a call outright is unverified.
6. **Fetch.ai wants a 3 to 5 minute demo video**; our main demo is 60 s. Plan a longer cut, or a separate ASI:One walkthrough, for the Fetch.ai submission.
7. **The DoorDash CLI also ships for Linux x86_64**, not only macOS Apple Silicon (README, 2026-10-03). It might run on Railway with `DD_CLI_ACCESS_TOKEN`. D9 still says Mac; this note just weakens the Mac dependency. Decide at hour 10.
8. **Fetch.ai requires a public repo.** `25danielc/shotgun` is already public, so secret hygiene matters (the pre-commit hook blocks `.env` and key-shaped strings).
9. PLAN.md's build steps skip **3.3** (3.2 → 3.4). The IDs are unchanged on purpose.
10. **Step 2.3's "3 rows appear" relies on the planner.** The voice prompt calls `dispatch_task` once with the whole request, which stores one `plan` row. The 3 worker rows appear only once the orchestrator (2.4) splits it. PLAN.md lists 2.3's Needs as 1.1 and 2.2 only, so either build 2.4 before 2.3's live test (2.4 needs only 2.1), or switch the prompt to the D13 fallback (one `dispatch_task(type, request)` per part).
11. **"Long items wait until parked" has no build step.** It's in the safety story (§2) but no PLAN.md row implements it. Either add it to the 4.3 call policy, using the `carplay_disconnected` event, or drop it from the pitch.
12. **`/events` doesn't store the location yet.** The planner has no ETA until it does: `run_planner` passes none, and deadlines tied to arrival come back as null. Step 5.1 should save the latest trip (location, time) from `/events` and feed it to 5.2.

## 10. Open questions

| Question | Status | Decide by |
|---|---|---|
| Exact submission deadline | **Resolved:** Sun Oct 4, 12:00 PM EDT (Devpost header shows 12:15) | — |
| Is an Apple Silicon Mac available at the venue? | Setup Mac is arm64; confirm it's at the venue. Linux CLI build may remove the need (§9.7) | Hour 10 |
| Does ElevenLabs offer Claude for voice turns? | **Resolved:** the agent runs `claude-haiku-4-5` (pushed via API and read back, 14:14) | — |
| Fetch.ai hackpack: own LLM or chat protocol required? | **Resolved:** Chat Protocol required; ASI-1 LLM **not** required (§6a) | — |
| How many sponsor prizes can one project enter? | **Resolved:** as many as eligible. Whether it can *win* several isn't stated; ask an organizer | Before submitting |
| Where does the ETA destination come from? | **Resolved (D16):** fixed `HOME_ADDRESS` in `.env` / Railway | — |
| Can the ElevenLabs initiation webhook reject a caller outright? | Not described in the docs. Built instead: `/tools/init` returns `caller_allowed: "no"` and a refusal greeting, the prompt hangs up at once, and the tools return 403 | — |
| Does the iOS 26 CarPlay automation fire reliably with the phone locked? | Open; step 1.3 measures it (5 of 5 replugs locked) | Hour 4 gate |

## 11. API notes (checked against docs 2026-10-03)

Not decisions, just a cache so later sessions don't re-research. Still re-check anything that fails.

- **ElevenLabs outbound call:** `POST https://api.elevenlabs.io/v1/convai/twilio/outbound-call`, header `xi-api-key`. Body `{agent_id, agent_phone_number_id, to_number, conversation_initiation_client_data: {dynamic_variables: {summary: "..."}}}`. Response `{success, message, conversation_id, callSid}`.
- **ElevenLabs number import:** `POST /v1/convai/phone-numbers` `{provider:"twilio", phone_number, label, sid, token, agent_id?}` returns `{phone_number_id}`.
- **ElevenLabs tools:** separate resources, `POST /v1/convai/tools` with `tool_config.type = "webhook"`, referenced by `conversation_config.agent.prompt.tool_ids` (inline `prompt.tools` is deprecated). `response_timeout_secs` defaults to 20 (min 5). Body properties can bind `dynamic_variable: "system__caller_id"` and others. Header values can reference a workspace secret `{secret_id}`. `end_call` goes under `prompt.built_in_tools`.
- **ElevenLabs LLM field:** `conversation_config.agent.prompt.llm`. Values include `claude-haiku-4-5` and `claude-sonnet-5-5`. Custom LLM: `llm: "custom-llm"` + `prompt.custom_llm {url, model_id, api_key, api_type}`, OpenAI-compatible streaming chat completions.
- **ElevenLabs key check:** `GET /v1/convai/agents?page_size=1`.
- **ElevenLabs, checked in the OpenAPI spec (2026-10-03):** `POST /v1/convai/secrets {type:"new", name, value}` and `PATCH /v1/convai/secrets/{id} {type:"update", ...}`. `GET/PATCH/DELETE /v1/convai/tools/{id}` and `GET /v1/convai/tools?search=`. `GET/PATCH /v1/convai/phone-numbers/{id}` (PATCH `{agent_id}` assigns the agent); `GET /v1/convai/phone-numbers` returns `phone_number_id`. `conversation_config.agent.dynamic_variables.dynamic_variable_placeholders`. `built_in_tools.end_call = {type:"system", name:"end_call", params:{system_tool_type:"end_call"}}`. `platform_settings.workspace_overrides.conversation_initiation_client_data_webhook = {url, request_headers}`. `scripts/apply_agent.py` uses all of these.
- **Twilio key check:** `GET https://api.twilio.com/2010-04-01/Accounts/{SID}.json` with basic auth. A2P 10DLC applies only to messaging.
- **Google Routes:** `POST https://routes.googleapis.com/directions/v2:computeRoutes`, headers `X-Goog-Api-Key`, `X-Goog-FieldMask: routes.duration,routes.distanceMeters` (the field mask is required). Body `origin/destination.location.latLng`, `travelMode: DRIVE`, `routingPreference: TRAFFIC_AWARE` (Pro SKU, 5k free/month). `routes[0].duration` is a string like `"1234s"`.
- **Google Places (New):** `POST https://places.googleapis.com/v1/places:searchText` / `:searchNearby`. Field mask `places.displayName,places.formattedAddress,places.currentOpeningHours.openNow` (opening hours are the Enterprise SKU, 1k free/month). The IDs-only mask `places.id` is free.
- **Railway (as deployed):** project/service `shotgun`, URL https://shotgun-production-5f30.up.railway.app. Railpack 0.40.1 needs `railpack.json` → `deploy.startCommand` (our app is `app/main.py`, so auto-detection fails at build time). Railway sets `PORT=8080`. CLI 4.10 syntax: `railway variables --set`.
- **Railway:** Railpack builder (reads `.python-version`). `railway.json` sets `startCommand` and `healthcheckPath`. Variables: `railway variable set K=V` (`railway variables --set` is deprecated). App sleeping is opt-in, so containers stay up.
- **Claude Code Action:** `anthropics/claude-code-action@v1`, secret `ANTHROPIC_API_KEY`, set up with `/install-github-app`. Triggers on `issues: [opened, assigned]` + `issue_comment`, checking for `@claude`. Only users with write access can trigger it. Opens no PR (§9.4).
- **GitHub webhook:** `X-GitHub-Event: pull_request`, `action: opened`. Verify `X-Hub-Signature-256` (`sha256=` HMAC of the raw body) with `hmac.compare_digest`.
- **Neon + psycopg 3:** the pooled (`-pooler`) string with `sslmode=require` works. Pass `prepare_threshold=None` to be safe. No LISTEN/NOTIFY or advisory locks through the pooler.
- **Composio:** packages `composio` + `composio_anthropic` (0.25.x). `user_id` replaces "entity". Gmail slugs `GMAIL_CREATE_EMAIL_DRAFT` (returns `draft_id`), `GMAIL_SEND_DRAFT`, `GMAIL_SEND_EMAIL`. Composio-managed OAuth via `session.authorize("gmail")` → `redirect_url`. Key check: `GET https://backend.composio.dev/api/v3.1/tools` with `x-api-key`.
- **Fetch.ai uAgents:** `uagents` 0.26.0 / `uagents-core` 0.4.11 (docs pin 0.25.5). Chat protocol from `uagents_core.contrib.protocols.chat` (`ChatMessage`, `ChatAcknowledgement`, `TextContent`, `EndSessionContent`, `chat_protocol_spec`). `Agent(..., mailbox=True, publish_agent_details=True)` + `agent.include(proto, publish_manifest=True)`. Connect through the Local Agent Inspector → Mailbox. Set the profile name, handle and keywords so ASI:One can find it.
- **DoorDash CLI:** `dd-cli` from github.com/doordash-oss/doordash-cli. Waitlist-only, darwin-arm64 + linux-x86_64. `dd-cli login`, `dd-cli search --query ...`, `dd-cli order history`. Cart and checkout subcommands are TODO(verify) via `dd-cli --help`.
- **Anthropic (learned in the 2.4 live run):** the API key must be **workspace-scoped**. An org-level key gets 400 "not scoped to a workspace … anthropic-workspace-id header", and `make check-keys` catches it. SDK `anthropic` 1.11 runs on `httpx2`. `fallbacks="default"` needs beta `server-side-fallback-2026-07-01` via `client.beta.messages.create`. Nullable strict-tool fields use `anyOf` with `{"type": "null"}`: the docs list anyOf and null as supported, not type arrays.
- **Anthropic:** orchestrator `claude-sonnet-5-5`. Forced `tool_choice` (`any`/`tool`) returns a 400 on Sonnet 5.5, so use `auto` + `strict: true` tools + a prompt instruction. `thinking: {type: "disabled"}` is a 400; use low effort or `{type: "between_tools"}` for speed. Key check is `models.list(limit=1)`.

---

## Decision log

Newest last. Format: `YYYY-MM-DD HH:MM (hour N): decision. Why.`

- 2026-10-03 13:00 (hour 1): Repo set up from PLAN.md and the planning chat; all decisions above recorded. Python 3.12 + uv, FastAPI, psycopg 3. Added `TOOLS_SHARED_SECRET` for the `/tools/*` webhooks.
- 2026-10-03 13:30 (hour 1): Created private repo `25danielc/shotgun-demo-app` (planted bug: login fails on capitalised or space-padded email) and issues #1 to #26 on `25danielc/shotgun`, one per PLAN.md step. PLAN.md status stays authoritative.
- 2026-10-03 13:30 (hour 1): Step 2.1 transitions are the context's chain plus `running → done` for read-only jobs (research) and `any open state → failed` (a spoken "no" means failed with the summary "Cancelled"). `needs_approval → done` is deliberately impossible. Every transition is logged in `job_events` (the approval log for 4.2). DB tests run on embedded Postgres (`pgserver` dev dependency) offline, and on Neon with `make test-neon`.
- 2026-10-03 13:45 (hour 2): Step 2.2. `dispatch_task` stores a raw request as a queued job of a new `plan` type, which the orchestrator (2.4) claims and splits. With a worker `type` (D13 fallback) it creates that job directly; an unknown type falls back to `plan` so nothing is lost. `approve_action` acts only on jobs in `needs_approval` (new `expect=` guard), so a "no" can't cancel unrelated work. The caller check accepts `ALLOWED_CALLER_NUMBER` as either `system__caller_id` or `system__called_number` (outbound semantics unverified); tool bodies now also carry `called`. Auth runs before body parsing and the DB connection: 401 bad secret, 403 stranger, 503 when unconfigured.
- 2026-10-03 14:05 (hour 2): Step 2.4. The planner is one Sonnet 5.5 call with strict tools (one per worker type, plus `report_unsupported` so out-of-scope parts like car control are reported back, never invented). It runs at effort `low` with server-side refusal fallback (`fallbacks: "default"`). Deadlines come back as minutes from now, given the drive time; null means as soon as possible. "Text X" is planned as an email (D6, §9.2), pending Daniel's OK. Nullable fields use `anyOf` with null, the form the strict-schema docs list. A background loop in the app claims `plan` jobs every second. The live check found that the first Anthropic key isn't workspace-scoped (400 asking for `anthropic-workspace-id`): use a key created inside a workspace.
- 2026-10-03 14:25 (hour 2.5): Step 4.1. Jobs are announced once per state (`announced_state`) when they reach done, needs_approval or failed. A finished plan stays silent; a failed plan rings. Everything ready goes into one call, with at most one question per call (its id in `pending_job_id`) and no new question while one is unanswered. A spoken "no" is marked announced at once, so there's no "Cancelled" callback. A 60 s gap between callbacks guards against ringing into a live call. That is a collision guard, not the 4.3 cooldown policy (still stretch). A failed call is retried after the gap.
- 2026-10-03 13:20 (hour 1.5), recorded at the hour-2 audit: Daniel asked to build ahead while keys were being made. So 1.2, 1.5, 2.1, 2.2, 2.4 and 4.1 were built and tested offline before their Needs were Done (the build-step rule says stop). Each is marked `Blocked:` with what's left, and none is marked Done until its real check passes. 1.2 and 1.5 went into one commit (`steps 1.2 + 1.5`) because they share a code path; later steps are one commit each.
- 2026-10-03 14:45 (hour 3): Step 3.1. The coder loop claims a job and files an issue ending "@claude please fix this and open a pull request." The job stays `running` with the issue number. The `/github/hook` webhook (HMAC-checked, answers 202, works in the background) opens the PR from the Action's "Create a PR" link, or accepts a `pull_request` opened for a `claude/` branch, then sets `needs_approval` ("I opened a pull request: <label>. Merge it?"). A spoken yes leads to a squash merge and `done`; nothing merges before that. No PR within 15 min means `failed`. Demo repo setup still needed: `/install-github-app`, the `ANTHROPIC_API_KEY` repo secret, then `make github-hook` after deploy. Each merge fixes the planted bug, so reset the demo repo before every rehearsal (demo-prep checklist).
- 2026-10-03 14:14 (hour 2): With Daniel's OK, pushed `config/elevenlabs_agent.json` (stage greet) to his existing agent `shotgun`. Model changed from `claude-opus-5-5`, which he had set in the UI, to `claude-haiku-4-5` (D14). Prompt replaced with the repo's, first message `{{greeting}}`, `end_call` added, voice kept, still no tools and no call-start webhook. Read back and confirmed. His previous agent config was saved to the session scratchpad before the push. From now on the repo config is the source of truth: change the file, then re-run `scripts/apply_agent.py`.
- 2026-10-03 14:25 (hour 2.5): The 1.2 test call rang and played its message, but the agent never hung up; Daniel had to end it after 10 s. Transcript showed no `end_call`. Causes: the prompt only covered hanging up after a dispatch or an answered callback, and `silence_end_call_timeout` was `-1`. Fix: explicit hang-up rules in the prompt (goodbye, "that's all", one-way messages get "Anything else?" once) plus a 20 s silence timeout. Pushed and read back; regression test `test_agent_always_hangs_up`.
- 2026-10-03 14:21 (hour 2.5): Step 1.2 Done. Daniel confirmed the 14:15 call rang within 5 s and played the message. A second call at 14:20 ended itself ("Ending conversation after 20 seconds of silence", 27 s total). The spoken path ("bye" leading to `end_call`) is still to be seen, on the 1.1 Civic call. Fixed `make ring`, which didn't quote MSG, so apostrophes were lost.
- 2026-10-03 14:40 (hour 2.5): Built `/tools/init` (the server side of 2.3's caller allowlist) ahead of the car test. Docs read today: the webhook fires for inbound Twilio calls, and for outbound calls only when the request carries no `conversation_initiation_client_data`. Ours always carries it, so callbacks keep their greeting. New dynamic variable `caller_allowed` (outbound calls send "yes"). Unknown callers get "Sorry, this line is private. Goodbye." and the prompt's first rule makes the agent hang up. Generated the three shared secrets and set `ALLOWED_CALLER_NUMBER` = `MY_PHONE_NUMBER` in `.env` (backup in the session scratchpad).
- 2026-10-03 14:38 (hour 2.5): Step 1.4 Done. Railway project `shotgun` created with Daniel's OK ("build what you can" after `railway login`). 14 `.env` values pushed by name (`make railway-env`). The first build failed: Railpack found no start command, fixed with `railpack.json`. `/health` returns 200. `/events`, `/tools/init` and `/github/hook` give 401 on bad credentials. A live `/events` with the real secret returned 202 in 0.13 s and the server placed the call (step 1.5, awaiting Daniel). The app logs that the DB-backed loops wait for `DATABASE_URL` / `GITHUB_TOKEN`, as expected.
- 2026-10-03 14:47 (hour 3): Neon and GitHub keys in. 2.1 Done (whole suite on Neon). DATABASE_URL pushed to Railway; startup logs `database ready` with no loop warnings. 2.2 Done against Railway. To avoid a surprise callback, the dispatch sample used `type: research`: with the plan sample, the live planner would have made a coder job that fails on the GitHub 404, and the watcher would ring Daniel. Test rows deleted afterwards. 2.4 Done (5/5 live). Agent pushed at stage `full`: tools `dispatch_task`, `get_status` and `approve_action` plus the `/tools/init` webhook, read back.
- 2026-10-03 14:45 (hour 3): **Region mismatch.** Railway put the service in us-west2; Neon is us-east-2. Each DB round trip costs ~70 ms; dispatch_task spent ~250 ms server-side, and a real approve_action (~6 round trips) would exceed the 500 ms budget. `deploy.multiRegionConfig` in railway.json was ignored by `railway up` (the deployment manifest kept us-west2), and `railway scale` crashes on CLI 4.10 (GraphQL error), so the change was reverted. Daniel to switch the service region to US East (Virginia) in the Railway dashboard, then re-measure.
- 2026-10-03 14:44 (hour 3): GITHUB_TOKEN authenticates but gets 404 on `25danielc/shotgun-demo-app` (private): the fine-grained token wasn't given access to that repo.
- 2026-10-03 14:55 (hour 3): Commits showed on GitHub as **danmchen-sys**: the global `~/.gitconfig` email `danmchen@umich.edu` is verified on that account. This repo and `shotgun-demo-app` now set `user.email = 231554998+25danielc@users.noreply.github.com` locally (global config untouched). With Daniel's OK, all existing commits in both repos were rewritten to that email with `git filter-branch --env-filter`. Trees and dates were verified unchanged, and backup branches `backup/pre-author-rewrite` were kept. Daniel force-pushes (`--force-with-lease`).
- 2026-10-03 15:00 (hour 3): Daniel force-pushed both repos; GitHub now shows the commits as 25danielc. He moved the Railway service to US East (Virginia, `us-east4-eqdc4a`) in the dashboard. Server-side get_status went from ~250 ms (cross-country) to ~65 ms, so the latency risk is closed. GITHUB_TOKEN now reaches the demo repo but lacks the Webhooks permission, so the hook was registered through the `gh` login (`scripts/github_hook.py --gh`, hook 691695445). GitHub's ping returned 202, which proves the shared secret matches end to end. Still missing on the demo repo: the Claude Code workflow (`/install-github-app`) and the `ANTHROPIC_API_KEY` secret. Without them, a coder job files its issue, no PR ever comes, and after 15 min the driver gets a "didn't come back in time" callback.
- 2026-10-03 15:06 (hour 3): Step 1.5 Done via the real Shortcut, run by hand at the desk: `source=ios_shortcut`, 202, outbound call placed, agent greeted. The Shortcut sent `location` empty, so the Latitude/Longitude fields are missing or not mapped. That's fine for milestone 1, but needed for 1.3's "location present" and for 5.1.
- 2026-10-03 15:48 (hour 4): First Civic test. Plug-in rang (1.6: 1 of 3) and a live 2-part request became plan 82 → email 83 + research 84. Then nothing happened, because the email (3.2) and research (3.5) workers are still stubs and the watcher only rings on done / needs_approval / failed. So **any job type without a worker sits queued silently**. Next fix: a guard that fails unserved jobs with a spoken "I can't do that yet". Separately, coder job 79 (15:24) failed with a GitHub 404: Railway still had the old GITHUB_TOKEN. Synced with `make railway-env`, then redeployed at 16:36.
- 2026-10-03 16:43 (hour 4.7): Demo repo finished from the CLI with Daniel's OK (`/install-github-app` hadn't completed). Set the `ANTHROPIC_API_KEY` repo secret via `gh secret set`, from `.env` and never printed. Committed `.github/workflows/claude.yml` (Anthropic's `examples/claude.yml` for claude-code-action@v1) to main. The `make coder-demo` hero loop then ran fully live:
  - job 85 → issue #1 → Action (1m29s) → PR #2 opened by our webhook → needs_approval at 1.7 min
  - callback "I opened a pull request: Fix the login bug. Merge it?" → Daniel: "Yes." → approve_action → squash-merged 5 s later
  - a second callback "Merged: Fix the login bug." 60 s later

  3.1 and 4.1 Done; 4.2 "yes" path proven. **The demo repo's main is now fixed**, so every rehearsal needs the planted bug restored first (a reset script is a TODO for 7.1). Polish option: skip the "Merged" callback when the driver approved on the call less than 2 min earlier.
- 2026-10-03 16:55 (hour 5): Daniel's decisions:
  - **"Text X" → email** is OK, and he'll say "email" in the demo (D16).
  - **The ETA destination is a fixed home address**, now `HOME_ADDRESS` in `.env` and Railway. It isn't in git, because the repo is public (D16).
  - **Research uses Claude web search for everything**, so no Places API (D15). The Maps key now needs only the Routes API, and `check_keys` checks only Routes.

  Shipped the **unclaimed guard** (`callbacks.expire_unclaimed`): a job still queued after 2 min fails with "Sorry, I can't handle this one yet: <label>.", which the watcher then rings about. Old stuck jobs 80, 81, 83 and 84 were failed silently first (marked announced), so the deploy didn't ring about them.
- 2026-10-03 17:25 (hour 5.5): **Step 3.5 research built before 6.1 Fetch.ai** (Daniel reordered the stretch order).
  - `app/workers/research.py` uses Sonnet 5.5 + `web_search_20260209` (max_uses 5, effort low) and runs as an in-app loop (up to 3 lookups in parallel, no pool connection held while searching).
  - Live: 10–25 s per lookup. With dynamic filtering the final text has **no citations**, so sources come from the search-result URLs.
  - It names only places with hours it can verify: 2 of 3 in the live check. That's deliberate (never guess hours).
  - The planner's research tool now covers quick facts too (scores, weather, news).
  - **Bug fixed:** the planner formatted "current time" in the server zone, which is UTC on Railway, so clock-time deadlines would have been 4 h off. New `TIMEZONE` setting (default America/Detroit).
  - **Risk to watch:** a callback can ring while the driver is still on the dispatch call, because the watcher only spaces out *its own* calls. Research finishes about 30 s after dispatch.
- 2026-10-03 17:45 (hour 6): **Research latency 13.6 s → 3.6 s.**
  - Benchmark on one lookup: Sonnet 5.5 + `web_search_20260209` took 13.6 s and found 1 place (4 code-execution rounds from dynamic filtering). Sonnet 5.5 + `web_search_20250305` took 3.5 s and found 3 places with hours. Haiku 4.5 + 20250305 took 3.7 s and was wordier; Haiku can't use 20260209 at all.
  - Switched to 20250305 with max_uses 3.
  - **Busy-line guard:** the callback watcher first asks ElevenLabs (`GET /v1/convai/conversations?agent_id=…`, about 300 ms) whether a conversation is `initiated` or `in-progress`, and waits if so. It fails open, and ignores "live" calls older than 15 min. Needed because a research answer can now be ready before the driver hangs up.
