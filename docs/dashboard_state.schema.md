# Dashboard state contract

`static/dashboard.html` ("mission control", shown on a laptop at the judging table) only ever
reads one endpoint, `GET /dashboard/state`. This file defines exactly what that endpoint
returns. Fixtures: `tests/fixtures/dashboard_state.json` (mid-drive) and
`tests/fixtures/dashboard_state_empty.json` (plugged in, nothing dispatched) are real backend
output: `tests/test_dashboard_fixtures.py` builds both drives in the database and fails if
`build_state()` no longer returns them byte for byte (`REGEN_DASHBOARD_FIXTURES=1` rewrites
them). `tests/fixtures/dashboard_state_edge.json` is deliberately malformed (nulls, missing
fields, long text, every state) to check the page never throws.

The dashboard is read-only. It never approves, cancels or merges anything: approval stays
voice-only. The only writes are the three demo endpoints at the end, and they only work in
demo mode.

## Endpoint

```
GET /dashboard/state   header X-Dashboard-Token: <DASHBOARD_TOKEN>   (?token= also accepted)
200 application/json   body below
401                    token missing or wrong (constant-time compare)
503                    DASHBOARD_TOKEN not configured (never open)
```

- The page polls every 1.5 s, so the handler must stay cheap: a few indexed queries plus
  in-memory caches. **It never calls a third-party service.** Service health and agent
  heartbeats come from caches that are filled elsewhere (see below).
- Send `Cache-Control: no-store`.
- `GET /dashboard` serves `static/dashboard.html`. Open it as `/dashboard#token=...`: a URL
  fragment never reaches the server, so the token stays out of the access logs (`?token=` still
  works but is logged). The page keeps it in `sessionStorage`, removes it from the address bar
  and sends it as the `X-Dashboard-Token` header. `<meta name="referrer" content="no-referrer">`.

## Conventions

- Timestamps are ISO 8601 UTC with `Z`, e.g. `"2026-10-03T22:34:52Z"`. `null` means unknown or
  not yet. The page renders them as local `HH:MM:SS`.
- The page computes clock skew from `server.now` and uses it for every countdown and
  elapsed timer. The server clock wins, so the laptop clock doesn't matter.
- IDs are the Postgres bigint IDs, sent as JSON numbers.
- Lists have a fixed order (stated per field), so the page can diff by ID.
- No secrets, phone numbers, email addresses, `HOME_ADDRESS` or coordinates anywhere in the
  payload. The repo and the demo are public. Recipient names are fine; addresses are not.
  Every free-text field is scrubbed (`[email]`, `[phone]`, `[home]`); the drive's plug-in
  lat/lng is never read into the payload; keys and `NTFY_TOPIC` are never read at all.

## Top level

| Field | Type | Notes |
|---|---|---|
| `schema_version` | int | `1`. Bump on breaking changes. |
| `server` | object | Always present. |
| `services` | array of Service | Always 8 entries, in the order listed below. |
| `drive` | Drive or null | The open drive, else the most recent one if it ended < 30 min ago, else null. |
| `call` | Call or null | The live call, else the last call if it ended < 2 min ago, else null. |
| `jobs` | array of Job | Every job of `drive` (all of them, never paged), ordered by `id` ascending. `[]` when there's no drive. |
| `upcoming` | array of Upcoming | What the agent will do next. Sorted by `at` ascending, `null` last. |
| `tool_calls` | array of ToolCall | The 20 most recent `/tools/*` calls, newest first. |
| `events` | array of Event | The 100 most recent events, **oldest first**. |
| `agents` | array of Agent | Always 5 entries: voice, orchestrator, coder, research, email. |
| `active_agent` | enum or null | `voice \| orchestrator \| coder \| research \| email \| null`: whichever agent did the most recent thing: the live call's last tool call (voice), or the newest state change or PR of a job a worker is on. The AGENTS diagram highlights it. |

### server

| Field | Type | Notes |
|---|---|---|
| `now` | timestamp | Server clock when the payload was built. |
| `uptime_s` | int | Seconds since the process started. |
| `demo_mode` | bool | `DEMO_MODE=true`. The page shows the demo buttons only when this is true. |
| `build` | string | Fingerprint of the served `static/dashboard.html`. The page reloads itself when it changes, so a tab opened before a deploy picks up the new page. |

### Service

```
{name, ok, latency_ms, checked_at, detail}
```

| Field | Type | Notes |
|---|---|---|
| `name` | enum | `api_server \| neon \| elevenlabs \| twilio \| anthropic \| github \| google_routes \| ntfy` |
| `ok` | bool or null | `null` = never checked yet, or deliberately not probed (see `detail`). |
| `latency_ms` | int or null | Round trip of the last check. `null` if it failed before any response. |
| `checked_at` | timestamp or null | When the last check finished. The page dims a row when this is > 90 s old. |
| `detail` | string or null | Short reason when not ok, e.g. `"HTTP 403 API not enabled"`. Never a key or a URL with a key. |

A background task checks each service about every 30 s and caches the result in memory. One
cheap, read-only, no-side-effect probe per service:

| name | Probe (suggested) |
|---|---|
| `api_server` | The process itself: always `ok: true`, `latency_ms` = time to build the last state payload. |
| `neon` | `select 1` on the pool. |
| `elevenlabs` | `GET /v1/convai/agents?page_size=1` (a key check, DECISIONS §11). |
| `twilio` | `GET /2010-04-01/Accounts/{sid}.json` (TODO(verify)). |
| `anthropic` | `GET /v1/models` (no tokens spent). |
| `github` | `GET /repos/{GITHUB_DEMO_REPO}`. |
| `google_routes` | Not probed: every Routes call is billed. `ok: null`, detail `"not probed: billed per request"` (`ok: false` if the key is missing). |
| `ntfy` | `GET https://ntfy.sh/v1/health` (TODO(verify)). Never publish to `NTFY_TOPIC` to check. |

### Drive

```
{id, status, started_at, ended_at, destination, eta, arrival_call_at, arrival_called}
```

| Field | Type | Notes |
|---|---|---|
| `id` | int | `drives.id` |
| `status` | enum | `plugged_in \| on_call \| silent \| parked`, derived as described below. |
| `started_at` | timestamp | Plug-in. |
| `ended_at` | timestamp or null | Unplug. |
| `destination` | string or null | What the driver named (`set_destination`). |
| `eta` | timestamp or null | From Google Routes, once at departure. |
| `arrival_call_at` | timestamp or null | `eta − 3 min`, or earlier if every job went terminal or held. |
| `arrival_called` | bool | True once the arrival call was placed or cancelled. |

How `status` is derived:
- `parked`: `ended_at` is set.
- `on_call`: a call is ringing or active, or we placed one (a `calls` row) under 15 s ago and
  the live-call monitor hasn't seen it yet.
- `silent`: the drive is open, no call, and the departure call has already happened, or the
  call policy decided not to ring.
- `plugged_in`: the drive is open and the departure call hasn't happened yet (it's being
  placed, or there's no call at all yet): no `calls` row and under 60 s old.

### Call

```
{status, kind, started_at, duration_s, last_tool, transcript}
```

| Field | Type | Notes |
|---|---|---|
| `status` | enum | `ringing \| active \| ended` |
| `kind` | enum | `departure \| arrival \| exception \| inbound` (inbound = driver tapped the contact). |
| `started_at` | timestamp | When it started ringing. |
| `duration_s` | int | Seconds since it was answered, as of `server.now` (0 while ringing). The page keeps ticking it locally between polls. |
| `last_tool` | string or null | Name of the most recent `/tools/*` call during this call, e.g. `"dispatch_task"`. |
| `transcript` | array or omitted | Optional, not built. The last ≤ 6 turns, `{at, role: "agent" \| "driver", text}`, oldest first. Without it the page lists the call's `tool_calls` (those at or after `started_at`) in that space. |

### Job

```
{id, type, title, details, state, progress, steps, preapproval, approval,
 result_summary, result_url, created_at, updated_at}
```

| Field | Type | Notes |
|---|---|---|
| `id` | int | `jobs.id` |
| `type` | enum | `plan \| coder \| email \| food \| research` |
| `title` | string | Short human label, ≤ 60 chars (coder: `label_of(job)`; others: from `request`/`details`). |
| `details` | object | `jobs.details` passed through (already safe; strip addresses). |
| `state` | enum | `queued \| running \| approved \| needs_approval \| exception \| done \| failed \| expired` |
| `progress` | number or null | 0 to 1. `(done + skipped steps) / total steps`. `null` when the job has no steps. The page computes this itself from `steps` if it's null. |
| `steps` | array of Step | Per-type pipeline below. `[]` if the type has none. |
| `preapproval` | object or null | As stored at dispatch: `{condition: string, require_tests_pass: bool \| null, max_usd: number \| null}`. |
| `approval` | object | `{status, note}`, see below. Always present. |
| `result_summary` | string or null | `jobs.summary`: the sentence the arrival call will say. |
| `result_url` | string or null | PR URL if there is one, else issue URL, else null. |
| `created_at` / `updated_at` | timestamp | |

`approval.status` is one of:

| status | Meaning | Page renders |
|---|---|---|
| `not_needed` | Read-only job (research, plan). | nothing |
| `preapproved` | A pre-approval exists and still holds (also after it moved the job `running → approved` with no call). | `pre-approved: <preapproval.condition>` |
| `pending` | No pre-approval: the job will need a spoken yes (not at `needs_approval` yet, or at `needs_approval` while a call is live). | `needs approval` |
| `held` | `needs_approval`, waiting for the arrival call. | `held for arrival` |
| `approved` | Spoken yes through `approve_action` (from `needs_approval` or `exception`). | `approved by voice` |
| `declined` | Spoken no: `approve_action` moved it from `needs_approval` or `exception` to `failed`. | `declined` |
| `exception` | The result broke its pre-approval. | `exception: <note>` |

`approval.note` is a string or null: the reason from the `exception` job_event, e.g.
`"the tests failed"` or `"it comes to 31.40 dollars, over your 25 dollar limit"`.

### Step

```
{name, state, started_at, finished_at, detail}
```

| Field | Type | Notes |
|---|---|---|
| `name` | string | From the per-type list below, in that order. |
| `state` | enum | `pending \| running \| done \| failed \| skipped` |
| `started_at` / `finished_at` | timestamp or null | The page shows elapsed `MM:SS` (running steps tick live). |
| `detail` | string or null | e.g. `"issue #12"`, `"3 checks"`, `"PR #13"`. |

Steps per job type. Always send the full list, with not-yet-reached steps as `pending`:

| type | steps |
|---|---|
| `coder` | `issue_filed → action_running → pr_opened → tests → approval_check → merged` (tests run on the PR; `skipped` without `require_tests_pass`) |
| `research` | `searching → summarizing` |
| `email` | `drafting → approval_check → sent` |
| `food` | `cart_built → approval_check → ordered` |
| `plan` | `planning` |

`approval_check` is `done` when a pre-approval held or a spoken yes arrived (detail
`pre-approved` / `spoken yes`), `running` while it's held or in `exception` (both wait for a
yes or no), and `failed` on a spoken no. The coder `tests` step follows `result.tests`
(app/workers/coder.py): `pending` → running ("waiting for checks"), `success` → done, any other
check_run conclusion or `timed_out` → failed (detail "failed", "never reported", ...). When a
job fails, the step that was in progress is marked failed with `jobs.error` as its detail,
unless a step already failed (a spoken no is not a failed merge).

### Upcoming

```
{at, kind, description, job_id}
```

| Field | Type | Notes |
|---|---|---|
| `at` | timestamp or null | `null` = waits on an event rather than a time (e.g. "when tests pass"). |
| `kind` | enum | `arrival_call \| preapproved_action \| held_action \| job_expiry \| exception_call` |
| `description` | string | One line, e.g. `"merge the PR if tests pass (pre-approved)"`. |
| `job_id` | int or null | |

Sources (the rules of app/calls.py and app/workers/coder.py, imported, not copied):
- `arrival_call`: while `arrival_called` is false and the drive has any job
  (`calls.arrival_due`). `at` = `drive.arrival_call_at`; with no ETA, `server.now` once every
  job is terminal or held, else `null`. The count is the jobs `calls.spoken()` reads out.
- `preapproved_action`: each `running` non-read-only job with a pre-approval (`at: null`,
  "merge the PR if the tests pass (pre-approved)"), and each `approved` job (`at: now`,
  "merge the PR: approved, going through").
- `held_action`: each `needs_approval` job. `"held for arrival call: <label>"` at the arrival
  call time; after the arrival call, `"waiting on you (next plug-in call): <label>"`, `at: null`
  (nothing rings after the arrival call; 4.3's departure call asks).
- `job_expiry`: a `queued` job whose worker isn't running (or isn't claimed after 10 s) fails at
  `updated_at + UNCLAIMED_MINUTES`; a `running` coder job with no PR fails at
  `updated_at + PR_TIMEOUT_MINUTES`; one waiting for its tests goes to `exception` at
  `pr_opened_at + TESTS_TIMEOUT_MINUTES`. `jobs.deadline` isn't enforced by any code yet
  (step 5.2), so it isn't shown.
- `exception_call`: each unannounced `exception` job in a drive whose arrival call hasn't
  rung (`calls.exception_job`), at `max(now, last exception call + 10 min)`, one gap apart.

### ToolCall

```
{at, tool, query, latency_ms, ok}
```

| Field | Type | Notes |
|---|---|---|
| `at` | timestamp | When the request arrived. |
| `tool` | enum | `search_web \| draft_message \| set_destination \| dispatch_task \| get_status \| approve_action` |
| `query` | string or null | The main argument, ≤ 120 chars: `search_web` query, `draft_message` "to: intent", `set_destination` text, `dispatch_task` "type: details", `approve_action` "job #N: yes/no", `get_status` "drive #N". |
| `latency_ms` | int | Time to the response. Budgets: inline 8000 ms, background 500 ms. The page turns over-budget calls red. |
| `ok` | bool | False on error or timeout. |

### Event

```
{id, at, type, message, job_id}
```

| Field | Type | Notes |
|---|---|---|
| `id` | int | Monotonic, derived from the time (`epoch_ms × 16 + source rank`). The page appends ids it hasn't seen. |
| `at` | timestamp | |
| `type` | enum | `plug_in \| unplug \| call_started \| call_ended \| tool_called \| job_dispatched \| job_state \| arrival_scheduled \| arrival_call \| exception_call \| push_sent`. `job_state` covers both state changes and step changes. |
| `message` | string | One line (≤ 200 chars), written for a judge reading over a shoulder. |
| `job_id` | int or null | Optional; omit or null when it doesn't apply. |

### Agent

```
{name, status, last_heartbeat, current_job_id}
```

| Field | Type | Notes |
|---|---|---|
| `name` | enum | `voice \| orchestrator \| coder \| research \| email` (always in this order). |
| `status` | enum | `idle \| busy \| offline \| not_built`. **offline** if `last_heartbeat` is older than 60 s. **not_built**: the worker doesn't exist yet (email, 3.2 stretch); the page shows it dim, not as an error. |
| `last_heartbeat` | timestamp or null | |
| `current_job_id` | int or null | Set while busy. The diagram labels busy nodes with it. |

Heartbeats:
- **Workers** (orchestrator, coder, research, email): every worker loop runs in the Railway
  process, so the heartbeat is its asyncio task (named `planner`, `coder`, `research` in
  app/main.py) being alive: `last_heartbeat` = `server.now`, else `offline` with null.
- **voice**: `busy` while a call is ringing or active. Otherwise `idle` if the last
  ElevenLabs health check passed, `offline` if it failed. `last_heartbeat` = the most recent
  `/tools/*` call or post-call webhook.

`research` is in the list because the research worker (step 3.5) exists. The original prompt
listed only voice, orchestrator, coder and email.

## Example

Shortened. See `tests/fixtures/dashboard_state.json` for a full payload.

```json
{
  "schema_version": 1,
  "server": {"now": "2026-10-03T22:34:52Z", "uptime_s": 11520, "demo_mode": true},
  "services": [
    {"name": "neon", "ok": true, "latency_ms": 42, "checked_at": "2026-10-03T22:34:40Z", "detail": null},
    {"name": "google_routes", "ok": null, "latency_ms": null, "checked_at": "2026-10-03T22:34:44Z", "detail": "not probed: billed per request"}
  ],
  "drive": {"id": 17, "status": "on_call", "started_at": "2026-10-03T22:29:10Z", "ended_at": null,
            "destination": "North Campus", "eta": "2026-10-03T22:45:00Z",
            "arrival_call_at": "2026-10-03T22:42:00Z", "arrival_called": false},
  "call": {"status": "active", "kind": "departure", "started_at": "2026-10-03T22:29:12Z",
           "duration_s": 334, "last_tool": "get_status"},
  "jobs": [
    {"id": 42, "type": "coder", "title": "Fix the login redirect loop", "details": {"repo": "shotgun-demo-app"},
     "state": "running", "progress": 0.5,
     "steps": [
       {"name": "issue_filed", "state": "done", "started_at": "2026-10-03T22:30:51Z", "finished_at": "2026-10-03T22:30:58Z", "detail": "issue #12"},
       {"name": "action_running", "state": "done", "started_at": "2026-10-03T22:30:58Z", "finished_at": "2026-10-03T22:33:40Z", "detail": null},
       {"name": "pr_opened", "state": "done", "started_at": null, "finished_at": "2026-10-03T22:33:40Z", "detail": "PR #13"},
       {"name": "tests", "state": "running", "started_at": "2026-10-03T22:33:40Z", "finished_at": null, "detail": "waiting for checks"},
       {"name": "approval_check", "state": "pending", "started_at": null, "finished_at": null, "detail": null},
       {"name": "merged", "state": "pending", "started_at": null, "finished_at": null, "detail": null}
     ],
     "preapproval": {"condition": "merge it if the tests pass", "require_tests_pass": true, "max_usd": null},
     "approval": {"status": "preapproved", "note": null},
     "result_summary": null, "result_url": "https://github.com/OWNER/shotgun-demo-app/pull/13",
     "created_at": "2026-10-03T22:30:50Z", "updated_at": "2026-10-03T22:33:40Z"}
  ],
  "upcoming": [
    {"at": "2026-10-03T22:42:00Z", "kind": "arrival_call", "description": "arrival call: batched summary of 2 jobs", "job_id": null},
    {"at": null, "kind": "preapproved_action", "description": "merge the PR if the tests pass (pre-approved)", "job_id": 42}
  ],
  "tool_calls": [
    {"at": "2026-10-03T22:29:41Z", "tool": "search_web", "query": "is the Bonisteel lot open on Saturday", "latency_ms": 3712, "ok": true}
  ],
  "events": [
    {"id": 301, "at": "2026-10-03T22:29:10Z", "type": "plug_in", "message": "CarPlay connected: drive #17 opened", "job_id": null}
  ],
  "agents": [
    {"name": "voice", "status": "busy", "last_heartbeat": "2026-10-03T22:34:50Z", "current_job_id": null},
    {"name": "email", "status": "not_built", "last_heartbeat": null, "current_job_id": null}
  ],
  "active_agent": "coder"
}
```

## Demo endpoints

These are for the judging table: they make a drive happen without a car. Each one takes the
same token and **only works when `DEMO_MODE=true`**. Otherwise it returns 404, so the routes
don't even appear to exist in production.

```
POST /dashboard/demo/plug-in?token=<DASHBOARD_TOKEN>   -> 202 {"accepted": true}
POST /dashboard/demo/arrive?token=<DASHBOARD_TOKEN>    -> 202 {"accepted": true}
POST /dashboard/demo/unplug?token=<DASHBOARD_TOKEN>    -> 202 {"accepted": true}
401 bad token · 404 DEMO_MODE off · 409 nothing to do (e.g. arrive with no open drive)
```

| Endpoint | Does exactly what this real path does |
|---|---|
| `plug-in` | `/events` `carplay_connected` with `source: "dashboard_demo"`: opens a drive, and the departure call rings per call policy (4.3). |
| `arrive` | Sets the open drive's `arrival_call_at = now` so the arrival scheduler places the batched arrival call on its next tick. Same code path, no shortcut. |
| `unplug` | `/events` `carplay_disconnected`: closes the drive, cancels a pending arrival call, sends the ntfy recap. |

Every endpoint returns immediately and does its work in the background, like `/events`. Every
one also writes an event (`plug_in` / `arrival_call` / `unplug`) whose message ends in
`(demo)`, so the log shows what was simulated. None of them approves anything.

## Implementation status

Built in `app/dashboard.py`. Tests: `tests/test_dashboard_state.py` (contract shape, auth,
demo routes), `tests/test_dashboard_derived.py` (every job state, upcoming rules, privacy,
robustness, query count, health probes, live-call monitor), `tests/test_dashboard_recorder.py`
(the middleware: body and stream pass-through, no raise, < 1 ms overhead, a background tool
< 500 ms through the full app, refused callers and `/tools/init`) and
`tests/test_dashboard_fixtures.py` (the fixtures are backend output). Everything comes from
real rows or live checks; nothing is invented.

| Field | Source |
|---|---|
| `drive` | Latest `drives` row by id (open < 3 h, or parked < 30 min). |
| `drive.status` | `parked` if ended; `on_call` if a call is live or one was placed < 15 s ago; `plugged_in` if no call yet and < 60 s old; else `silent`. |
| `call` | `LIVE_CALL`: the agent's live ElevenLabs conversation, polled every 5 s while a drive is open (`GET /v1/convai/conversations`, as `telephony.call_in_progress`), and set immediately by `/tools/init` for the allowed caller only. A `calls` row the monitor hasn't seen yet shows as `ringing`. `kind` comes from the matching `calls` row (no match = `inbound`). No `transcript`. |
| `jobs` | `jobs` for the drive, plus `job_events` for timing. A `done` plan job is hidden (its parts speak for it, `calls.spoken`); a failed one is shown. `steps` are derived from the state history and `result` (coder: issue/PR numbers, `tests`, `pr_opened_at`). A row that breaks the derivation is still listed, without steps. |
| `upcoming` | Arrival call, pre-approved and approved actions, held jobs, exception calls (10-min gap), and the real timeouts (`UNCLAIMED_MINUTES`, coder `PR_TIMEOUT_MINUTES`, `TESTS_TIMEOUT_MINUTES`). |
| `tool_calls` | `ToolCallRecorder` ASGI middleware on `/tools/*`. Refused callers (401, 403, a stranger's `/tools/init`) are not recorded. Latency stops at the last response byte. In memory (last 20), so it resets on deploy. |
| `events` | `drives` (plug_in by source, unplug), `calls` (call_started, arrival_call, exception_call), `job_events` (job_dispatched, job_state), coder `result` (issue filed, PR opened) and in-memory tool, call and demo events. |
| `services` | `run_monitor()` every 30 s, read-only probes as above. `api_server.latency_ms` is the time the last payload took to build. |
| `agents` | voice: live call → busy, ElevenLabs probe failed → offline. Workers: the asyncio task (`planner`, `coder`, `research`) is alive → idle or busy (a `running` or `approved` job of its type); no task → offline. `email` has no worker yet, so it is always `not_built`. |
| `active_agent` | The newest of: the live call's last tool call (voice), and each working job's last state change or PR (its worker). |

The state read is five indexed queries on one pooled connection (2 s pool timeout): the latest
drive (primary key), the last exception call (`calls_at_idx`), the drive's calls, its jobs
(`jobs_drive_idx`) and their events (`job_events_job_idx`). A database error is logged at most
once a minute and the payload still answers, with `drive: null`.

Not built yet:
- **No `expired` state.** Timed-out jobs are `failed` in the jobs table.
- **No `push_sent` event.** The unplug recap isn't logged.
- **`arrival_scheduled` appears only for the demo arrive.** `set_destination` shows as its tool call.
- **In-memory records reset on restart.** Tool calls, call status and health live in process memory, so a redeploy clears them.
- **`DASHBOARD_TOKEN` and `DEMO_MODE` are Railway variables.** They must be set there for the deployed page.
