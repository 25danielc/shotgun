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

Shotgun is an AI agent saved as a phone contact. Plug the phone into the car and, within about 10 seconds, the car rings with "Shotgun" on the screen. You speak a multi-part request; the voice agent answers quick questions on the spot, dispatches longer jobs (asking for any "yes" up front) and stays on the line until you say goodbye. Worker agents do the jobs in the background. About 3 minutes before you arrive, the car rings once with a batched summary. **Nothing irreversible happens without a spoken "yes"**, given up front or on that call (D17: at most two calls per drive).

- Pitch lines: **"Everyone put a chatbot in the car. Shotgun is an agent built for the car."** / **"Your agent isn't an app. It's a contact."**
- What makes it car-specific:
  1. The car tells the agent when to start (plug-in).
  2. Arrival time is the deadline ("You're 31 minutes out, so I'll order in 6").
  3. No screen forces short answers, spoken confirmation and background work.
- **Main track: Actually Intelligent (AI).** Finance and food are examples, not the category. (The track description was announced at the opening ceremony and isn't online yet; check that the pitch fits it.)
- **Demo (about 60 s, filmed parked or with a second driver):** plug in → ring → "Where are you headed?" → request → pre-approval ("merge it if the tests pass?" → "Yes") → goodbye → arrival ring → batched summary. Coding PR only (D17). The full script is in PLAN.md and `.claude/skills/demo-prep`.
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
- **Call trigger (server).** *(Superseded by D17.)* At most a departure call on plug-in (call policy 4.3, now core, with a `CALL_POLICY=always` demo override), an arrival call at ETA − 3 min with a batched summary, and rare exception calls.
- **Voice agent.** ElevenLabs Agents on a Twilio voice number, inbound and outbound through ElevenLabs' API. Voice-turn LLM is Claude Haiku 4.5, or failing that ElevenLabs' custom-LLM option pointed at our server. **The voice agent never waits on work**: it calls tools that return instantly.
- **Voice tools** (webhooks to our server). *(D17)* Background tools `dispatch_task`, `get_status`, `approve_action` answer in **under 500 ms**. Inline tools `search_web`, `draft_message`, `set_destination` do the work during the call in under 8 s.
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
| D17 | **"Two calls per drive" passenger** (Daniel, 17:43). At most a DEPARTURE call (on plug-in, only if the call policy says so) and an ARRIVAL call (ETA − 3 min, batched summary), plus rare EXCEPTION calls. The agent stays on the call until the driver says goodbye; searches and drafts finish inline; a "yes" can be given up front as a pre-approval. Supersedes the per-job callback watcher (4.1), the "always hang up" rule (log 14:25), D11's "never waits on work" (now: never waits on *workers*) and D16's fixed destination (the agent asks). Details in the decision log, 17:43 | A ring per finished job is a phone that keeps interrupting a driver; hanging up to "call back later" turned a 5 s answer into two calls. Batching to arrival matches when the driver can act | Callback per job (the 4.1 design); hang up and call back for every search; a third-party search API; SMS or push as the main channel; removing `end_call` entirely |

## 5. Cut from scope (don't build)

Fetch.ai / Agentverse (6.1, cut at hour 5.7, D17) · Capital One Nessie money worker · the receipt page and the .tech domain · Photon / Relay / Spacetime · any hardware (OBD-II, ESP32 button) · a native CarPlay app · Gemini · the Figma prize · real restaurant reservations · real-inbox Gmail · controlling the car · Siri anywhere in the flow.

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

- **Stretch order (only after milestone 1 works):** email worker (3.2). Fetch.ai (6.1) is cut and 4.3 is core (D17); research (3.5) is done.
- **Gate, hour 4 (~4 PM Sat):** if plug-in → ring isn't working, make **tapping the contact** the trigger and move on.
- **Gate, hour 10 (~10 PM Sat):** pick the hero (D9).
- **Gate, hour 12 (~midnight):** if voice dispatch → callback isn't working end to end, **cut Fetch.ai**.
- **Never cut:** plug-in ring, the arrival call (was "callback"), one real completed action.
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
- **Google Routes destination as text (5.1, checked 2026-10-03, Waypoint reference):** `destination: {address: "<free text or plus code>"}` is a valid Waypoint, so no Geocoding API is needed and Maps stays Routes-only. Live: Michigan Union → Ann Arbor Amtrak, TRAFFIC_AWARE, 384 s, 1657 m, answered in 0.3 s.
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
- **Inline tools (2.2, live 2026-10-03 17:53):** Haiku 4.5 + `web_search_20250305` (max_uses 2) answered 5 spoken questions in 2.2–2.7 s through the endpoint; a Haiku draft took 0.7 s. Haiku takes no `output_config.effort`, so the request omits it and `thinking`. `client.with_options(timeout=8, max_retries=0)` plus `asyncio.wait_for` enforce the 8 s budget. A process-wide `AsyncAnthropic` is reused for a warm connection; its pool belongs to one event loop, so tests reset it per test.
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
- 2026-10-03 17:43 (hour 5.7): **D17, "two calls per drive".** Daniel's design change, approved with these details:
  - **Calls.** At most two outbound calls per drive: DEPARTURE (on plug-in, only if the 4.3 policy allows) and ARRIVAL (at ETA − 3 min, one batched summary of every job in the drive: done, held for approval, failed). EXCEPTION calls are the only others: for `exception` jobs with an irreversible action pending, at most one per 10 min; anything else waits for arrival. No ETA → arrival rings once every job in the drive is terminal or held. A drive with no jobs gets no arrival call. The per-job callback watcher is deleted; its unclaimed-job guard and busy-line check are kept and moved.
  - **The agent never ends a call to "continue later".** `end_call` is kept, allowed for exactly three reasons: (1) the driver says goodbye or "that's all"; (2) a refused caller (the allowlist needs it, which is why `end_call` isn't removed); (3) silence: after 60 s of silence the agent asks "Anything else?", and after 30 s more with no reply it hangs up. This reverses the 14:25 "always hang up" fix.
  - **Inline vs background tools.** Inline tools do the work during the call, target < 8 s, ElevenLabs timeout about 15 s: `search_web` (Haiku 4.5 + Anthropic `web_search_20250305`, so D15 holds and no new search vendor), `draft_message`, and later `set_destination` (5.1). Background tools keep the < 500 ms rule: `dispatch_task(type, details, preapproval?)`, `get_status(drive_id?)`, `approve_action`. The CLAUDE.md hard rule now says so.
  - **Pre-approval.** `preapproval = {condition, require_tests_pass?, max_usd?}`, asked for at dispatch with the condition repeated back. Only the structured fields are checked by code; `condition` is stored and read back, never judged by a model (Daniel, a). Pre-approved jobs go queued → running → approved → done with no call. A result that breaks its pre-approval goes to `exception`. A job without one that reaches an irreversible step goes to `needs_approval` and is held for the arrival call.
  - **Typed dispatch is the voice path.** The agent dispatches one typed job per part, each with its own pre-approval (D13's fallback becomes the main path). `type: "plan"` stays for multi-part requests the agent can't split and for other front doors; the Sonnet orchestrator stays in the stack.
  - **Drives.** New `drives` table (id, started_at, ended_at, start lat/lng, eta, destination, arrival_call_at, arrival_called); `jobs.drive_id` references it. A plug-in opens a drive and closes any still open (the disconnect Shortcut can miss). A dispatch with no open drive (the tap-the-contact fallback) opens one. Unplug (`carplay_disconnected`, `car_disconnected` accepted as an alias) closes the drive, cancels the pending arrival call and sends an ntfy push with the recap (new env `NTFY_TOPIC`, kept out of git: anyone who knows an ntfy.sh topic name can read it).
  - **Call policy 4.3 promoted to core.** Departure call only with pending items, a known drive of 10+ min, or no call in the last 30 min. Thresholds are config values. `CALL_POLICY=always` overrides it for rehearsals, because the plug-in ring is never-cut and would go silent on a quick replug (Daniel, e).
  - **Coder pre-approval "merge if tests pass".** The demo repo gets a `tests.yml` workflow (Daniel, f) and the coder checks it before merging.
  - **Config stays in `config/`** (`config/elevenlabs_agent.json` + `scripts/apply_agent.py`, which already pushes via the API); the prompt moves to `config/elevenlabs_prompt.md` (Daniel, c).
  - **Build order:** 2.2 → 2.3 → 4.2 → 4.1 → 4.3 → 4.4 → 4.5 → 5.1. 4.2 comes before 4.1 because the arrival scheduler needs the `exception` state and pre-approvals (Daniel, d).
  - **Fetch.ai (6.1) is cut.** Time: the redesign is about 8 h at hour 5.7, which lands past the hour-12 gate, and the core car demo comes first.
  - **Email (3.2) stays stretch.** The demo script is the coding PR only: no "reply to Sarah" line, and the in-car pass check has no draft-then-send. `draft_message` still returns text to read back.
  - **5.1 ETA is core, built after 4.5.** The plug-in Shortcut sends lat/lng (Daniel edits it). The departure call asks "Where are you headed?" unless the destination is already known; inline `set_destination(text)` sends the address to Google Routes (traffic-aware) from the plug-in location and stores destination, eta and `arrival_call_at = now + duration − 3 min`. The ETA is computed once, at departure; there is no in-drive location. No destination or no location → the "all terminal or held" trigger. Calendar and trip-history destinations are out of scope (later stretch). Supersedes D16's fixed `HOME_ADDRESS`.
  - Rejected: a ring per finished job; hanging up after every dispatch; Brave/Tavily-style search APIs (new key, breaks D15); SMS or push as the main channel (D6; ntfy is only the unplug recap); disabling `end_call` (strangers couldn't be hung up on); an LLM judging free-text pre-approval conditions (slow and unpredictable).
- 2026-10-03 17:53 (hour 5.9): **Step 2.2 (D17).**
  - New `drives` table (`app/drives.py`); `jobs.drive_id` and `jobs.preapproval`. Plan jobs pass both on to the jobs they create.
  - `dispatch_task` takes `details` (the old `request` name is still accepted, so the agent live today keeps working until the 2.3 push), plus an optional `label` and `preapproval`, and opens a drive if none is open. Replies now carry `drive_id`.
  - Inline `search_web` / `draft_message` live in `app/inline.py`. A slow or failed answer is a spoken fallback ("Want me to look it up in the background?"), never an error.
  - **The curl check now dispatches a research-only sample.** The multi-part sample would make the live planner file a real coder issue.
  - One live draft turned "come over tonight, I'm bringing ramen" into "I'm bringing ramen over tonight. Want me to come by?": wrong direction. That's acceptable, because the agent reads every draft back before anything happens. Watch it in 2.3.
- 2026-10-03 17:58 (hour 6): **Step 2.3 config (D17), not pushed yet.**
  - The prompt moved to `config/elevenlabs_prompt.md`. It adds inline-first rules, dispatch with a yes up front, and `end_call` for exactly three reasons.
  - **ElevenLabs caps `turn_timeout` at 30 s** (docs, conversation-flow), so the 60 s silence rule is 30 s `turn_timeout` → `skip_turn` with `wait_timeout_secs` 30 → check-in "Anything else?" → next silent turn → `end_call`. `silence_end_call_timeout` is 90 as a backstop. TODO(verify) on a live call.
  - Inline tools: timeout 15 s and `pre_tool_speech: "force"`. `preapproval` is a nested object (supported per the OpenAPI spec).
  - New call variables `drive_id` and `call_kind`. Greetings no longer ask "Anything you want handled?".
  - **The in-car pass check has to wait for 4.2 + 4.1:** the old per-job watcher still rings after the call until then.
- 2026-10-03 18:04 (hour 6): **Step 4.2 (D17).**
  - New state `exception` (→ approved or failed only) and the edge running → approved, which `transition()` allows only when the job carries a preapproval.
  - `app/approvals.py` `settle()` decides at the irreversible step: approved (inside the pre-approval), exception (broken), or needs_approval (no pre-approval).
  - A structured condition the worker can't check counts as broken: "merge if the tests pass" with no result → exception.
  - Coder: "merge if the tests pass" waits for the demo repo's new `tests.yml` (job `tests`).
  - **The result arrives by webhook (`check_run` added to hook 691695445)**, because the fine-grained GITHUB_TOKEN gets 403 on both check runs and Actions runs. The webhook needs no new permission.
  - No result in 10 min → exception. `approve_action` accepts needs_approval or exception.
- 2026-10-03 18:08 (hour 6): **Step 4.1 (D17).** `app/callbacks.py` deleted and `app/calls.py` added. Each tick, in order:
  - The unclaimed guard (moved over unchanged).
  - The busy-line check.
  - At most one call: the arrival call for a due drive, or else one exception call.

  Details:
  - New `calls` table (in `app/drives.py`) logs every outbound call, so the 10-minute exception gap and 4.3's "not called in 30 min" survive restarts.
  - Plug-in now opens a drive and logs its departure call.
  - The arrival call's question is one the driver hasn't been asked yet (exception before hold); one already asked is read out but not asked again first.
  - Nothing rings after the arrival call. Results that come later go in the unplug recap (4.4).
  - With no ETA, the arrival call can ring soon after the driver hangs up, as soon as everything is settled (e.g. a quick research job). Step 5.1's ETA moves it to ETA − 3 min.
- 2026-10-03 18:10 (hour 6): **Step 4.3 Done.**
  - `app/policy.py`: ring on plug-in if something is waiting on the driver (needs_approval or exception, any drive), or a known drive of 10+ min, or no call in 30 min. `CALL_POLICY=always` overrides. Thresholds are settings (`DEPARTURE_MIN_DRIVE_MINUTES`, `DEPARTURE_QUIET_MINUTES`).
  - A silent plug-in still opens a drive. The decision is made in the request (a few queries), so `/events` returns an accurate `calling`. If the DB or the policy fails, it rings anyway (the never-cut ring).
  - The departure greeting leads with what's waiting and asks its question. Otherwise "Hey, it's Shotgun, riding along."
  - **For rehearsals and filming, set `CALL_POLICY=always` in Railway.**
- 2026-10-03 18:12 (hour 6.5): **Step 4.4 (D17).**
  - Unplug (`carplay_disconnected` or the alias `car_disconnected`) closes the open drive. That cancels its arrival call: `app/calls.py` only rings for open drives.
  - Then one ntfy push (`app/recap.py`) with lines grouped as Done / Waiting on you / Still running / Didn't work. A drive with no jobs pushes nothing.
  - Items still waiting come back on the next departure call (4.3's pending rule).
  - `NTFY_TOPIC` is a secret (ntfy.sh topics are public by name). It was generated into `.env` as `shotgun-<24 hex>`. `make check-keys` checks it locally only, because a request would publish the name.
- 2026-10-03 18:13 (hour 6.5): **Step 4.5 Done.** `scripts/demo.py`:
  - `make demo-call` goes through the real `/events`, so the call policy applies.
  - `make demo-arrive` sets the open drive's `arrival_call_at` to now in Neon; the deployed loop does the ringing. No new public endpoint.
  - `make watch` is read-only.
  - `make callback-demo` was removed with the watcher in 4.1.
- 2026-10-03 18:16 (hour 6.6): **Step 5.1 built (D17).**
  - The departure greeting asks "Where are you headed?". The agent sends the answer to the new inline tool `set_destination`.
  - The server sends that text to Google Routes as a `Waypoint.address`, from the drive's plug-in location (no Geocoding API). It stores destination, eta and `arrival_call_at = eta − 3 min` (or now, for drives under 3 min).
  - "home" / "my place" maps to `HOME_ADDRESS` and is spoken as "home", so D16's address survives as a shortcut.
  - No location or no route → the destination is stored and the arrival call falls back to "all settled".
  - The 4.3 long-drive rule still can't fire at plug-in, because the destination comes later on the call.
  - `make watch` shows the plug-in location, to check the Shortcut fix.
  - **Pass checks:** Daniel's message was cut off at "Pass checks:", so these are the ones proposed in PLAN.md, pending his list.
- 2026-10-03 18:30 (hour 6.7): **Daniel's test call: "can you find me 3 nearby ramen places" got "I can't search, but I could have someone research it".**
  - Transcript `conv_3101m41y…`. Cause: the live agent is still the pre-D17 config (3 tools, no `search_web`), because the 2.3 push is waiting on his OK.
  - Fixed before the push anyway:
    - The prompt now says lookups are searched at once, with no asking first and no explaining.
    - The agent speaks as itself and never mentions tasks, workers or "someone else", or says it can't search.
    - A slow search is handed off silently ("Still digging, I'll tell you before you park").
    - The server's fallback reply no longer offers a background lookup.
  - `search_web` now gets the drive's plug-in location and destination (dynamic variable `drive_id`), so "nearby" means near the driver.
  - The search prompt now requires places that match exactly, no repeats, and never reading out the home address.
  - Live, the same query from Ann Arbor: Haiku 4.5 took 2.9–3.5 s but still counted a poke place as ramen in some runs; Sonnet 5.5 took 4.7–5.4 s and gave 3 real ramen shops. Kept Haiku as Daniel specified; `INLINE_MODEL=claude-sonnet-5-5` switches with no code change.
- 2026-10-03 18:38 (hour 6.6): **Deployed 4.1–5.1 and pushed the D17 agent** (Daniel: "go").
  - Railway variables synced (22, names only), including `NTFY_TOPIC` and `CALL_POLICY=always` for testing. Deploy `3009e528` SUCCESS; `/health` 200, `database ready`, no loop warnings.
  - `apply_agent.py --stage full`: created `search_web`, `draft_message` and `set_destination`, updated the other 3 tools, the agent and the number. Read back OK.
  - **Location:** Daniel's Shortcut already sends lat/lng (Railway logged `location True` at 18:26). The server deployed at 17:58 just didn't store it yet, so SHORTCUT_SETUP.md was corrected.
  - Not run: the curl check of `dispatch_task` against Railway. A dispatched job now earns a real arrival call.
- 2026-10-03 18:49 (hour 6.9): **Daniel's 18:40 test call** (`conv_6901m41y…`, 157 s). It stayed on the line through a search, a coding task with pre-approval and a draft, and ended on "that's enough". Problems found and fixed:
  - **"53 minutes to 333 East Jefferson" (should be ~10).** The plug-in location stored fine (42.2967, -83.7211, North Campus); the Shortcut works. The address had no city, and Routes resolved it to 333 E Jefferson in Detroit (76 km, 3154 s). With ", Ann Arbor, MI" it's 4 km, 559 s.
    - Fix (`app/eta.py`): a street address with no city is also tried in HOME_ADDRESS's city and state, both requests in parallel, and the closer route wins. Bare place names are sent as said.
    - Live: 10 min.
  - **Stiff and robotic** (it read "six oh eight East Liberty, open until eleven PM"). Fixes:
    - The search prompt now answers like a friend in the passenger seat: names plus a rough where, never street numbers, hours only if asked or closing within the hour.
    - `without_house_numbers()` removes any house number the model still produces.
    - The agent prompt treats tool replies as notes to say in its own words, never reads addresses, coordinates or exact times, and varies its fillers. Tool replies are shorter ("About 10 minutes. I'll ring you just before you get there.").
  - **Location handling.** Haiku given raw coordinates asked the driver for their location and once placed Ann Arbor in Detroit. Search is now localized with `user_location` (city from HOME_ADDRESS). The home address is no longer in the search context, where it pulled answers toward home. "Where am I?" gets the part of town only; precise reverse geocoding would need the Geocoding API, outside the fixed stack ("Routes only").
  - **It said "Sent." for an email it had only dispatched**, and there's no email worker (3.2 stretch), so that job will fail at the unclaimed guard and be reported on the arrival call. The prompt now says to claim "done" only when it is.
  - **Tests now pin `CALL_POLICY` and `HOME_ADDRESS`** so a local `.env` can't change results (it did, once `CALL_POLICY=always` was set).
  - **Live coder job 182** filed demo-repo issue #3, but the demo repo's main is already fixed, so expect no PR and a "didn't come back in time" failure after 15 min.
- 2026-10-03 18:54 (hour 6.9): Pushed the conversational prompt with Daniel's OK; read back OK. The re-assign step `PATCH /phone-numbers/…` returned HTTP 500 (ElevenLabs internal error), but the number was already assigned to the agent and still is (checked). Harmless, since the step is idempotent. If it repeats, `apply_agent.py` could skip the PATCH when the assignment already matches.
- 2026-10-03 19:20 (hour 7.5): Daniel OK'd the next-steps plan. Changes:
  - **Cut 0.1, 3.4 and 5.2:** the demo is coder-only, so nothing needs food or deadline scheduling.
  - **Merged the dashboard branch** from another session (worktree `../shotgun-dashboard`, 3 commits). A review agent is hardening it.
  - **`make demo-reset`** (`scripts/demo_reset.py`), through the `gh` login. It puts every non-`.github/` file back to the planted-bug baseline (`d22264d`), closes claude/ PRs, deletes claude/ branches and closes issues Shotgun filed. In the database it closes open drives (marked `arrival_called`) and quietly fails open jobs. Idempotent. Ran it on the repo: restored `auth.py` + `tests/test_auth.py`, and CI on the reset commit is green.
  - **Live pre-approved hero run (4.2), job 184:** issue #4 → Claude's PR #5 at 1.2 min → Tests passed → `approved` with no question at 1.5 min → squash-merged at 1.7 min. So the demo repo needs `make demo-reset` again before a rehearsal.
  - **The arrival call wording is now natural.** `app/phrasing.py` has Haiku say the facts like a friend in about 1 s, ending with the pending question. It checks the result (not empty, ≤ 420 chars, ends in "?" when there's a question) and falls back to the plain wording, so a call never waits or fails on it. Live takes: "Hey! Good news, the login bug fix is merged and all the tests passed. I couldn't get that email to Alex sent though. Oh, and the signup typo fix is ready as a pull request, want me to go ahead and merge it?" One early take promised "I'll do that next", so the prompt now forbids promises.
  - **Worker summaries are friendlier:** "I merged the fix for the login bug, and the tests passed." / "The fix for X is ready as a pull request. Want me to merge it?" / "I can't do that one yet: ...". Exception calls open "Hey, quick one."
  - **Honest about email and food:** the prompt says plainly that it can't send messages or order food yet. It never dispatches them and never says "sent".
  - **Tests can't reach Anthropic any more:** `conftest.py` blanks the API key unless RUN_LIVE=1. The new wording code had quietly called real Haiku from the suite (3 s → 10 s).
  - `.claude/worktrees/` is ignored by git and ruff.
- 2026-10-03 19:34 (hour 7.6): **First live arrival call (4.1).** Drive 1's call rang at 23:30:16 UTC, exactly `arrival_call_at` (ETA − 3 min), covering jobs 182 + 183. It used the deploy made 9 minutes earlier, so the new natural wording.
  - It reached Daniel's voicemail, and the agent talked to the greeting for 112 s ("I think there's been a mix-up…"), asked "Where are you headed?" (a departure question), and checked in with "You still there?".
  - Fixes (in config, not pushed yet):
    - ElevenLabs' built-in `voicemail_detection` with a `voicemail_message` that reads `{{summary}}` and how to answer, then hangs up.
    - The destination question is limited to departure calls.
    - The check-in is exactly "Anything else?".
- 2026-10-03 19:44 (hour 8): **Dashboard review merged** (review agent, 33 new tests).
  - Correctness against the D17 schema: refused callers no longer show as calls; "declined" vs "pre-approved" is right; coder steps read `result.tests`.
  - The upcoming list reuses `calls.arrival_due`; the fixtures are now real backend output.
  - The ToolCallRecorder middleware is proven harmless: bodies intact, < 1 ms overhead, tools still < 500 ms, never raises.
  - Payload privacy is tested: no keys, topic, phone numbers, emails, home address or coordinates. The state handler makes zero third-party calls.
  - The page survives bad data, rendering each window on its own.

  Follow-ups done here:
  - The email worker reads dim "not built" instead of a red "offline" that judges would read as a failure.
  - **The token moved into a URL fragment:** open `/dashboard#token=…`; the page sends `X-Dashboard-Token`. Fragments never reach the server, so the token stays out of Railway's access logs.
  - **Background research now searches near the drive's destination** (D17), not HOME_ADDRESS, and is localized the same way as `search_web`.
  - `calls(drive_id)` index.
- 2026-10-03 19:59 (hour 8): Pushed the agent with Daniel's OK. Read back: 6 tools, `voicemail_detection` with `{{summary}}` in its message, honest about email and food, destination asked on departure only, check-in exactly "Anything else?". The number is still on the agent; this time the phone-number PATCH succeeded.
- 2026-10-03 20:06 (hour 8.1): **"No ETA" on Daniel's 20:00 call** (`conv_3501m423…`, drive 2). The drive was opened by the dashboard's SIMULATE PLUG-IN (`source = dashboard_demo`), which sends no location, so `set_destination` correctly found none. His Shortcut had stored one on drive 1 at 18:40. "Nearby" search worked only because search is localized by city.
  - **Fix:** a drive opened without a location takes the last known location: the newest drive with a real plug-in location in the last 12 h (`drives.LAST_KNOWN_HOURS`). It's marked `location_source = last_known` (vs `plug_in`), and a copied location is never copied forward again. This covers the simulate button and tapping the contact. A real plug-in location always wins.
  - Also seen on that call, not fixed:
    - The agent made up an explanation for why search worked without a location.
    - It dispatched the coder job without asking for the pre-approval first, so the job was held and asked on the arrival call, where a "yes" merged it. That's allowed, but not the intended demo flow.
- 2026-10-03 20:11 (hour 8.2): Pushed the agent: it always asks "Want me to merge it if the tests pass?" before dispatching a code fix, and never invents explanations ("I'm not sure why"). `LAST_KNOWN_HOURS` 12 → 48 (Daniel) so Saturday's location covers Sunday's judging.
  - Live evidence from drive 2 (the 20:00 simulated plug-in): exactly two calls, departure and arrival. Job 185 was held, asked on the arrival call, and Daniel's "yep" merged PR #7. Marked 4.1 Done and 4.2 Done apart from a live "no".
  - `fix_name` adds "the" ("the fix for login bug" → "the fix for the login bug").
- 2026-10-03 20:36 (hour 8.5): **Step 3.2, the email worker, built** (Daniel: yes).
  - **The Composio "invalid key" was a paste error.** The `.env` value had the variable name pasted in front of the key, so the name plus an equals sign was sent as part of it. The real key works (HTTP 200); fixed in `.env` and Railway, and check-keys passes 15/15.
  - **Gmail connected** through `POST /api/v3/connected_accounts/link` on the existing Composio-managed auth config `shotgun` (`initiate()` is retired for managed OAuth since May 2026). Status ACTIVE.
  - Worker (`app/workers/email.py`, REST `POST /api/v3/tools/execute/{slug}`):
    - Claims a job and resolves `to` through `EMAIL_CONTACTS` (first name is enough) or a spoken address. An unknown name fails with "I don't have an email address for Bob."; it never guesses.
    - Drafts with `GMAIL_CREATE_EMAIL_DRAFT`, then `approvals.settle`: approved for a pre-approval ("send this exact message"), otherwise needs_approval and held for the arrival call.
    - An approved job is sent with `GMAIL_SEND_DRAFT`.
    - Shapes were checked live with a draft to the account itself, then deleted.
  - `dispatch_task` gained `to`. The prompt now reads the draft back, asks "Want me to send that?", and on a yes dispatches with the exact text plus the pre-approval. It says "I'll send it", never "Sent".
  - The dashboard shows email as a real worker.
- 2026-10-03 20:59 (hour 9): **Daniel's Car Run 1** (drive 5, real Shortcut with location; 181 s departure call, 28 s arrival).
  - Worked: a real email to Daniel (pre-approved), the coder pre-approved and merged, exactly two calls, natural arrival summary, and several searches.
  - The planted bug was restored automatically after the merge, for Run 2.
  - **Miss: "The Landmark" got no ETA.** Routes has no route for the bare name. The first fix (retry with ", Ann Arbor, MI") routed to the wrong place 8 km away, while the agent's own search found the right one (student housing on South University).
  - **Final fix:** a place name (no street number, no city) is looked up first with Haiku and web search near the driver (`inline.find_address`, 5 s cap, a street-address line only, else NONE), then routed. Street addresses and "home" skip the lookup; a failed lookup falls back.
  - Live: The Landmark → 1300 S University, 11 min; the Michigan Union → 530 S State, 11 min; Costco → 771 Airport Blvd, 16 min. 2.6–2.8 s total, inside the 8 s budget.
- 2026-10-03 21:03 (hour 9.1): Daniel's feedback on Run 1:
  - **The "last call" wasn't the 3-minute call.** With no ETA (The Landmark failed to route), the arrival call fell back to "all settled" and rang about 3.5 min into the drive. Now that place names are looked up it rings at ETA − 3. The arrival wording opens with the minutes left ("Hey, you're about three minutes out") only when there's an ETA; the fallback opens "Hey, quick update." and never claims arrival. Live: "Hey, you're about three minutes out. Good news, I merged that login bug fix and all the tests passed."
  - **The prompt changes (need a push):** say the place and the minutes right after set_destination, unasked, with no description; never describe places unless asked.
- 2026-10-03 21:17 (hour 9.3): **The live agent was overwritten at 21:13:42 from outside the repo.** Daniel noticed the prompt "looked short".
  - What changed: the live prompt was the old 320-word pre-D17 one ("On it. I'll call you back" + end_call), 0 webhook tools were attached, and `silence_end_call_timeout` was back to 20. Settings that came after (voicemail, skip_turn, the new dynamic variables) survived.
  - Cause: no other session was active then, so most likely a stale ElevenLabs dashboard tab, opened around 14:14 and saved at 21:13.
  - Restored from the repo and verified: the live prompt is byte-equal to the repo, 6 tools, turn 30/90.
  - **New `make agent-check`** (read-only drift check: prompt, llm, tool count, built-ins, turn, first message) and `make agent-push`. Run agent-check before every rehearsal and never save the agent in the ElevenLabs UI.
  - **The prompt was rewritten at Daniel's request**, more conversational and less repetitive: 1,235 → 952 words, every rule kept, each rule now stated once, and the tests updated to the new phrasing. It includes the ETA-unasked and no-description rules from 21:04.
- 2026-10-03 21:32 (hour 9.5): Daniel, two asks.
  - **Code access (option a):** the agent says it's connected to exactly one GitHub repo ("your shotgun demo app"), and the driver must name the repo for a fix ("…in my demo workflow repo").
    - `dispatch_task` takes `repo`. The server refuses a code fix with no repo ("Which repo is that in?…") or a different one ("I'm not connected to X…").
    - "demo" or the repo's own name matches; "the shotgun repo" doesn't, since that's this project. The token appears to reach more of Daniel's repos (`/user/repos` lists 12 of his plus Z-Laboratory), but only the demo repo has the Action, Tests, secret and hook.
  - **Arrival calls end on their own:** they say what finished, end with "If you don't have anything else, I'm going to hang up.", and hang up after about 10 s of silence.
    - ElevenLabs can't change `turn_timeout` per call (`TurnConfigOverride` only has `soft_timeout_config`), so a second agent, "Shotgun (arrival)", handles arrival and exception calls: same prompt, tools and built-ins, with turn 10 / silence-end 30. The main agent keeps 30/90 for the departure silence rule.
    - `apply_agent.py` creates it (`ELEVENLABS_CALLBACK_AGENT_ID`), `agent-check` covers it, `calls.py` places these calls with it, and the busy-line check and dashboard read both agents' conversations.
    - Verified 21:40: an outbound call with the callback agent on the main number works.
  - First live arrival at ETA − 3 with the new opener (21:28:39): "Hey, you're about three minutes out. Good news—I already sent that email to Erica for you."
- 2026-10-03 21:36 (hour 9.7): **Every call opens "Shotgun here"** (Daniel: character in the first five seconds, without overdoing it).
  - Openers: departure "Shotgun here. Where are we headed?"; inbound "Shotgun here. What's up?"; arrival "Shotgun here. You're about three minutes out…" (or "Shotgun here, quick update."); exception "Shotgun here, quick one."; voicemail "Shotgun here with your update."
  - The arrival wording strips a leading "Hey/Hi" before adding the prefix. The prompt gives the character one line: easygoing, a little dry, no catchphrases or puns about riding shotgun.
  - **The voice, as checked:** "Kai – Clean, Modern, Global" (young American male, "confident… crisp and neutral with an edge"), `eleven_v4_turbo`, stability 0.5, speed 1.0. It was picked in the ElevenLabs UI and isn't pinned in the repo (`tts.voice_id` is still TODO), so a UI save can change it.
  - Test fix: `conftest.py` resets `inline._shared` per test. A reused client from an earlier test's event loop made live phrasing silently fall back.
- 2026-10-03 21:41 (hour 9.7): Pushed with Daniel's OK ("pin and push").
  - The main agent got "Shotgun here", the character line, the repo rules and the pinned voice (Kai, `eleven_v4_turbo`, the live settings). The new callback agent "Shotgun (arrival)" is `agent_0601m428…`, and `ELEVENLABS_CALLBACK_AGENT_ID` is in `.env` and Railway. Deployed; `make agent-check` passes for both agents.
  - Test arrival call through the callback agent (conv_1101m429…, 11 s): "Shotgun here. This is a test of the arrival call. If you don't have anything else, I'm going to hang up." → "Hang up." → "Talk later." → end_call. The silent 10 s path is still to be seen live.
  - **`.env` glitch:** the file ended without a newline after `EMAIL_CONTACTS`, so the appended line got glued onto it and dotenv failed to parse it. Fixed (backup in the scratchpad), and Railway re-synced and checked without printing the values. Appends to `.env` must start on a new line.

