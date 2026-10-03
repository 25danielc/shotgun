# Dashboard state contract

`static/dashboard.html` ("mission control", shown on a laptop at the judging table) only ever
reads one endpoint, `GET /dashboard/state`. This file defines exactly what that endpoint
returns. Fixtures: `tests/fixtures/dashboard_state.json` (mid-drive) and
`tests/fixtures/dashboard_state_empty.json` (plugged in, nothing dispatched).

The dashboard is read-only. It never approves, cancels or merges anything: approval stays
voice-only. The only writes are the three demo endpoints at the end, and they only work in
demo mode.

## Endpoint

```
GET /dashboard/state?token=<DASHBOARD_TOKEN>
200 application/json   body below
401                    token missing or wrong (constant-time compare)
503                    DASHBOARD_TOKEN not configured (never open)
```

- The page polls every 1.5 s, so the handler must stay cheap: a few indexed queries plus
  in-memory caches. **It never calls a third-party service.** Service health and agent
  heartbeats come from caches that are filled elsewhere (see below).
- Send `Cache-Control: no-store`.
- `GET /dashboard` serves `static/dashboard.html`. The page reads `token` from its own URL and
  forwards it.

## Conventions

- Timestamps are ISO 8601 UTC with `Z`, e.g. `"2026-10-03T22:34:52Z"`. `null` means unknown or
  not yet. The page renders them as local `HH:MM:SS`.
- The page computes clock skew from `server.now` and uses it for every countdown and
  elapsed timer. The server clock wins, so the laptop clock doesn't matter.
- IDs are the Postgres bigint IDs, sent as JSON numbers.
- Lists have a fixed order (stated per field), so the page can diff by ID.
- No secrets, phone numbers or email addresses anywhere in the payload. The repo and the demo
  are public. Recipient names are fine; addresses are not.

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
| `active_agent` | enum or null | `voice \| orchestrator \| coder \| research \| email \| null`: whichever agent did the most recent thing (newest event or heartbeat with work in hand). The AGENTS diagram highlights it. |

### server

| Field | Type | Notes |
|---|---|---|
| `now` | timestamp | Server clock when the payload was built. |
| `uptime_s` | int | Seconds since the process started. |
| `demo_mode` | bool | `DEMO_MODE=true`. The page shows the demo buttons only when this is true. |

### Service

```
{name, ok, latency_ms, checked_at, detail}
```

| Field | Type | Notes |
|---|---|---|
| `name` | enum | `api_server \| neon \| elevenlabs \| twilio \| anthropic \| github \| google_routes \| ntfy` |
| `ok` | bool or null | `null` = never checked yet. |
| `latency_ms` | int or null | Round trip of the last check. `null` if it failed before any response. |
| `checked_at` | timestamp or null | When the last check finished. The page dims a row when this is > 90 s old. |
| `detail` | string or null | Short reason when not ok, e.g. `"HTTP 403 API not enabled"`. Never a key or a URL with a key. |

A background task checks each service about every 30 s and caches the result in memory. One
cheap, read-only, no-side-effect probe per service:

| name | Probe (suggested) |
|---|---|
| `api_server` | The process itself: always `ok: true`, `latency_ms` = time to build the last state payload. |
| `neon` | `select 1` on the pool. |
| `elevenlabs` | `GET /v1/convai/agents/{ELEVENLABS_AGENT_ID}` (TODO(verify) against current docs). |
| `twilio` | `GET /2010-04-01/Accounts/{sid}.json` (TODO(verify)). |
| `anthropic` | `GET /v1/models` (no tokens spent). |
| `github` | `GET /repos/{GITHUB_DEMO_REPO}`. |
| `google_routes` | The cheapest Routes call that proves the key works, or the last real ETA call's result if one is under 30 s old. Avoid spending quota every 30 s. TODO(verify). |
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
- `on_call`: a call is ringing or active.
- `silent`: the drive is open, no call, and the departure call has already happened, or the
  call policy decided not to ring.
- `plugged_in`: the drive is open and the departure call hasn't happened yet (it's being
  placed, or there's no call at all yet).

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
| `transcript` | array or omitted | Optional. The last ≤ 6 turns, `{at, role: "agent" \| "driver", text}`, oldest first. Omit it if it isn't available cheaply. The page hides the section when it's missing. |

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
| `preapproved` | A pre-approval exists and still holds. | `pre-approved: <preapproval.condition>` |
| `pending` | No pre-approval: the job will need a spoken yes (not at `needs_approval` yet, or at `needs_approval` while a call is live). | `needs approval` |
| `held` | `needs_approval`, waiting for the arrival call. | `held for arrival` |
| `approved` | Spoken yes (at dispatch or `approve_action`). | `approved by voice` |
| `declined` | Spoken no. | `declined` |
| `exception` | The result broke its pre-approval. | `exception: <note>` |

`approval.note` is a string or null: a short reason, e.g. `"tests failed"` or `"total $31.40 > $25"`.

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
| `coder` | `issue_filed → action_running → tests → pr_opened → approval_check → merged` |
| `research` | `searching → summarizing` |
| `email` | `drafting → approval_check → sent` |
| `food` | `cart_built → approval_check → ordered` |
| `plan` | `planning` |

`approval_check` is `done` when a pre-approval held or a spoken yes arrived, `running` while
it's held or pending, and `failed` on exception or decline.

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

Sources:
- `arrival_call`: `drive.arrival_call_at` while `arrival_called` is false.
- `preapproved_action`: each open job with `approval.status = preapproved` whose irreversible
  step hasn't run yet.
- `held_action`: each job with `approval.status = held`. Description:
  `"held for arrival call: <action>"`. `at` = the arrival call time.
- `job_expiry`: open jobs with a deadline (`jobs.deadline`, or coder `created_at` +
  `PR_TIMEOUT_MINUTES`), plus queued jobs that nobody has claimed.
- `exception_call`: an exception call is queued but waiting on the 10-minute rate limit.

### ToolCall

```
{at, tool, query, latency_ms, ok}
```

| Field | Type | Notes |
|---|---|---|
| `at` | timestamp | When the request arrived. |
| `tool` | enum | `search_web \| draft_message \| set_destination \| dispatch_task \| get_status \| approve_action` |
| `query` | string or null | The main argument (search query, draft instruction, destination, task text). Truncate to 120 chars. |
| `latency_ms` | int | Time to the response. Budgets: inline 8000 ms, background 500 ms. The page turns over-budget calls red. |
| `ok` | bool | False on error or timeout. |

### Event

```
{id, at, type, message, job_id}
```

| Field | Type | Notes |
|---|---|---|
| `id` | int | Monotonic. The page appends events whose id is greater than the last one it saw. |
| `at` | timestamp | |
| `type` | enum | `plug_in \| unplug \| call_started \| call_ended \| tool_called \| job_dispatched \| job_state \| arrival_scheduled \| arrival_call \| exception_call \| push_sent`. `job_state` covers both state changes and step changes. |
| `message` | string | One line, written for a judge reading over a shoulder. |
| `job_id` | int or null | Optional; omit or null when it doesn't apply. |

### Agent

```
{name, status, last_heartbeat, current_job_id}
```

| Field | Type | Notes |
|---|---|---|
| `name` | enum | `voice \| orchestrator \| coder \| research \| email` (always in this order). |
| `status` | enum | `idle \| busy \| offline`. **offline** if `last_heartbeat` is older than 60 s. |
| `last_heartbeat` | timestamp or null | |
| `current_job_id` | int or null | Set while busy. The diagram labels busy nodes with it. |

Heartbeats:
- **Workers** (orchestrator, coder, research, email): each worker loop writes a heartbeat
  every ≤ 20 s, including when idle, e.g. into a `heartbeats(name, at, job_id)` table upserted
  per tick, or an in-process dict if every worker runs in the Railway process.
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
    {"name": "google_routes", "ok": false, "latency_ms": null, "checked_at": "2026-10-03T22:34:44Z", "detail": "timeout after 5000ms"}
  ],
  "drive": {"id": 17, "status": "on_call", "started_at": "2026-10-03T22:29:10Z", "ended_at": null,
            "destination": "North Campus", "eta": "2026-10-03T22:45:00Z",
            "arrival_call_at": "2026-10-03T22:42:00Z", "arrival_called": false},
  "call": {"status": "active", "kind": "departure", "started_at": "2026-10-03T22:29:12Z",
           "duration_s": 334, "last_tool": "get_status"},
  "jobs": [
    {"id": 42, "type": "coder", "title": "Fix the login redirect loop", "details": {"repo": "shotgun-demo-app"},
     "state": "running", "progress": 0.33,
     "steps": [
       {"name": "issue_filed", "state": "done", "started_at": "2026-10-03T22:30:51Z", "finished_at": "2026-10-03T22:30:58Z", "detail": "issue #12"},
       {"name": "action_running", "state": "done", "started_at": "2026-10-03T22:30:58Z", "finished_at": "2026-10-03T22:33:40Z", "detail": null},
       {"name": "tests", "state": "running", "started_at": "2026-10-03T22:33:40Z", "finished_at": null, "detail": "3 checks"},
       {"name": "pr_opened", "state": "pending", "started_at": null, "finished_at": null, "detail": null},
       {"name": "approval_check", "state": "pending", "started_at": null, "finished_at": null, "detail": null},
       {"name": "merged", "state": "pending", "started_at": null, "finished_at": null, "detail": null}
     ],
     "preapproval": {"condition": "merge it if the tests pass", "require_tests_pass": true, "max_usd": null},
     "approval": {"status": "preapproved", "note": null},
     "result_summary": null, "result_url": "https://github.com/OWNER/shotgun-demo-app/issues/12",
     "created_at": "2026-10-03T22:30:50Z", "updated_at": "2026-10-03T22:33:40Z"}
  ],
  "upcoming": [
    {"at": "2026-10-03T22:42:00Z", "kind": "arrival_call", "description": "arrival call: batched summary of 2 jobs", "job_id": null},
    {"at": null, "kind": "preapproved_action", "description": "merge the PR if tests pass (pre-approved)", "job_id": 42}
  ],
  "tool_calls": [
    {"at": "2026-10-03T22:29:41Z", "tool": "search_web", "query": "is the Bonisteel lot open on Saturday", "latency_ms": 3712, "ok": true}
  ],
  "events": [
    {"id": 301, "at": "2026-10-03T22:29:10Z", "type": "plug_in", "message": "CarPlay connected, drive #17 opened", "job_id": null}
  ],
  "agents": [
    {"name": "voice", "status": "busy", "last_heartbeat": "2026-10-03T22:34:50Z", "current_job_id": null},
    {"name": "email", "status": "offline", "last_heartbeat": "2026-10-03T22:31:40Z", "current_job_id": null}
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

## Backend requirements

What the features session must build so the page works against the real server. None of it
exists yet.

1. **Routes:** `GET /dashboard` (serves `static/dashboard.html`), `GET /dashboard/state`,
   and the three `POST /dashboard/demo/*` routes. Config: `DASHBOARD_TOKEN` (new secret,
   Railway and `.env` only) and `DEMO_MODE` (bool, default false).
2. **Service health cache:** a background task probes the 8 services every ~30 s
   (read-only probes, table above) and keeps `{name, ok, latency_ms, checked_at, detail}` in
   memory. `/dashboard/state` only reads the cache.
3. **Agent heartbeats:** every worker loop (orchestrator, coder, research, email) writes
   `{name, at, job_id}` every ≤ 20 s. Status is `offline` past 60 s. The voice agent's status
   comes from call state plus the ElevenLabs health check.
4. **An `events` log table** (or a view over `job_events` plus a new `drive_events` table) with
   a monotonic `id` and the 11 types. Write sites: `/events` (plug_in, unplug), telephony
   (call_started, call_ended, arrival_call, exception_call), `/tools/*` (tool_called),
   `dispatch_task` (job_dispatched), `jobs.transition` (job_state), the arrival scheduler
   (arrival_scheduled) and ntfy (push_sent).
5. **A `tool_calls` record:** `/tools/*` logs `{at, tool, query, latency_ms, ok}`. A ring buffer
   of the last 20 in memory is enough.
6. **Call tracking:** current call `{status, kind, started_at, answered_at, last_tool}`, set
   when a call is placed (telephony) and closed by the ElevenLabs post-call webhook or status
   callback. `transcript` is optional.
7. **New job states `exception` and `expired`.** They aren't in `JobState` / the check
   constraint yet. Today an expired coder job goes to `failed`; the dashboard wants `expired`
   when it timed out and `exception` when a result broke its pre-approval (D17).
8. **Job steps:** each worker records its step list (e.g. a `steps jsonb` column on `jobs`,
   updated with the state). The coder worker maps its flow onto
   `issue_filed → action_running → tests → pr_opened → approval_check → merged`. `tests` needs
   the PR's check-run status (a `check_suite` / `check_run` webhook, or a poll).
9. **Derived fields:** `drive.status`, `job.title`, `job.approval {status, note}` (held vs
   pending depends on whether a call is live), `job.progress`, `job.result_url`
   (`result.pr_url` or `result.issue_url`), `upcoming[]` (sources above), and `active_agent`.
10. **Payload hygiene:** no phone numbers, email addresses or keys. Cap `events` at 100,
    `tool_calls` at 20, transcript turns at 6. Keep `/dashboard/state` under ~100 ms; it's
    polled every 1.5 s.
