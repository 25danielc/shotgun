# Shotgun

An AI agent saved as a phone contact. Plug the phone into the car and the car rings ("Shotgun"); you speak a multi-part request, the voice agent dispatches jobs and hangs up, workers run in the background, and the car rings back with results. Nothing irreversible happens without a spoken "yes". Solo build for MHacks 2026. **Deadline: Sun Oct 4, 12:00 PM EDT.**

- **[docs/PLAN.md](docs/PLAN.md)** is the source of truth for build steps, IDs, pass checks, priorities and status.
- **[docs/DECISIONS.md](docs/DECISIONS.md)** explains why: decisions, rejected options, cut list, sponsor rules, fallback gates, open questions, API notes.

## Architecture

```
iPhone Shortcut (CarPlay connects) ─POST /events─▶ FastAPI on Railway ─▶ ElevenLabs outbound call ─▶ car rings
Car call (ElevenLabs Agent + Twilio) ─/tools/*──▶ dispatch_task / get_status / approve_action (<500 ms)
ASI:One (Fetch.ai, stretch) ─chat protocol─▶ same dispatch path
dispatch ─▶ orchestrator (Sonnet 5.5) ─▶ Neon job table ◀─ workers: coder | email | food | research
job done / needs_approval ─▶ callback watcher ─▶ outbound call with summary ─▶ "Confirm?" ─▶ approve_action
```

| Module | Role | Step |
|---|---|---|
| `app/main.py` | App, `/health` | 1.4 |
| `app/events.py` | `/events` + call trigger | 1.5, 4.3 |
| `app/security.py` | Shared-secret + caller checks (fail closed) | 1.5, 2.2 |
| `app/telephony.py` | ElevenLabs outbound call | 1.2 |
| `app/voice_tools.py` | `/tools/*` webhooks | 2.2, 2.3, 4.2 |
| `app/jobs.py`, `app/db.py` | Job table + state machine; connection pool | 2.1 |
| `app/orchestrator.py` | Request → jobs + deadlines | 2.4, 5.2 |
| `app/callbacks.py` | Job → outbound call | 4.1 |
| `app/eta.py` | Routes API ETA | 5.1 |
| `app/workers/*.py` | coder 3.1, email 3.2, food 3.4, research 3.5 | |
| `agents/fetch_agent.py` | Agentverse / ASI:One | 6.1 |
| `config/elevenlabs_agent.json` | Voice prompt, voice, tool schemas | 1.1, 2.3 |

## Fixed stack (do not reopen these without asking Daniel)

- Trigger: iOS Shortcuts "When CarPlay connects/disconnects" → POST /events. No Siri anywhere.
- Voice + phone: ElevenLabs Agents on a Twilio voice number. Voice LLM: `claude-haiku-4-5` in ElevenLabs.
- Orchestrator: Claude Sonnet 5.5 (`claude-sonnet-5-5`) via the `anthropic` SDK, tool use.
- Server: Python 3.12 FastAPI on Railway (ngrok fallback). No self-hosted models.
- Job table: Neon Postgres via psycopg 3. Shared by all agents; no agent-to-agent protocol.
- Coder: GitHub issue with @claude → Claude Code GitHub Action on `shotgun-demo-app`.
- Email: Composio Gmail on a throwaway account. Food: official DoorDash CLI or a cart-only browser agent.
- Maps: Google Routes API only (ETA to `HOME_ADDRESS`). Research uses Claude web search, not Places. No other Google Cloud.
- Callbacks are phone calls, never SMS. Hero action is the coding PR unless switched at hour 10.

## How to work

1. **One build step per session or task.** Start by stating the step ID (e.g. "Step 2.1").
2. **Plan first:** read the step row in PLAN.md, check that its "Needs" steps are done, then write a short plan.
3. **Every step ships with the test that proves its pass check** (`tests/test_step_X_Y_*.py`). Offline where possible; anything that hits real APIs or rings a phone is marked `@pytest.mark.live`.
4. Run `make test` and `make lint`. When the pass check passes, **update the step's Status in docs/PLAN.md** to `Done (YYYY-MM-DD HH:MM)`.
5. **Commit per step:** `step X.Y: <summary>`. GitHub issues #1 to #26 mirror the steps (labels core/hero/stretch); add `(closes #N)` to the commit message.
6. New decisions or changed plans go in the DECISIONS.md decision log.

Skills in `.claude/skills/`: `build-step`, `verify-step`, `add-worker`, `voice-agent`, `deploy`, `demo-prep`.

## Hard rules

- **Never commit secrets.** Keys live only in `.env` (git-ignored) and Railway variables. The repo is **public**. A pre-commit hook and a Claude hook (`scripts/hooks/check_secrets.py`) block `.env` and key-shaped strings; don't bypass them.
- **Tool webhooks answer in under 500 ms and never block on workers.** Insert a row, return, do the work in the background.
- **Nothing irreversible (send, order, pay, merge) without `approve_action`.** Workers stop at `needs_approval`.
- **Caller allowlist and shared secrets stay on**, in dev too: `/events` checks `EVENTS_SHARED_SECRET`, `/tools/*` check `TOOLS_SHARED_SECRET` and the caller number.
- **Verify third-party API details against current docs** before coding them (ElevenLabs, Fetch.ai, Composio, Google, GitHub Action). Mark anything unconfirmed `TODO(verify)`. DECISIONS.md §11 caches what was checked on 2026-10-03.
- Respect the cut list (DECISIONS.md §5) and the stretch order: only after milestone 1 (1.6) works.

## Commands

| | |
|---|---|
| `make setup` | Install Python 3.12 + deps via uv, enable the git hook, create `.env` |
| `make dev` | Run locally on :8000 with reload (`ngrok http 8000` to expose) |
| `make test` | Offline tests (DB tests use embedded Postgres via pgserver). `make test-live T=<file>` runs one file's `@live` tests; without `T` it runs all of them and **rings the phone** |
| `make test-neon` / `make db-init` | Same suite with Neon as the DB (rolled back, but briefly locks `jobs`: not during a live demo) / create the job tables in Neon |
| `make lint` / `make fmt` | Ruff check + format check / auto-fix |
| `make check-keys` | Step 0.2 pass check: one cheap read-only call per key |
| `make ring` | Step 1.2: ring `MY_PHONE_NUMBER` via ElevenLabs. `scripts/apply_agent.py --stage greet\|full` pushes the agent config |
| `make callback-demo` | Step 4.1: flip a job to done in Neon and ring with its summary (`MSG="..."`) |
| `make coder-demo` / `make github-hook` | Step 3.1: insert a coder job and watch it reach a PR (10 min) / point the demo repo's webhook at `PUBLIC_BASE_URL` (via the `gh` login) |
| `make curl-tools` | Step 2.2: curl the sample ElevenLabs payloads at a server (`BASE=...`, default `PUBLIC_BASE_URL`); 200 in < 500 ms each |
| `make deploy` | `railway up --detach` (see the `deploy` skill) |
| `make smoke` | GET /health on `PUBLIC_BASE_URL` (or `BASE_URL=...`) |

## Env vars (see .env.example)

`ANTHROPIC_API_KEY` · `ELEVENLABS_API_KEY` `ELEVENLABS_AGENT_ID` `ELEVENLABS_PHONE_NUMBER_ID` · `TWILIO_ACCOUNT_SID` `TWILIO_AUTH_TOKEN` `TWILIO_PHONE_NUMBER` · `DATABASE_URL` · `GITHUB_TOKEN` `GITHUB_DEMO_REPO` `GITHUB_WEBHOOK_SECRET` · `COMPOSIO_API_KEY` `COMPOSIO_USER_ID` · `GOOGLE_MAPS_API_KEY` `HOME_ADDRESS` `TIMEZONE` · `EVENTS_SHARED_SECRET` `TOOLS_SHARED_SECRET` `ALLOWED_CALLER_NUMBER` · `MY_PHONE_NUMBER` · `PUBLIC_BASE_URL`

## Steps that need Daniel physically: stop and ask, never fake a pass

- **0.1** DoorDash waitlist · **0.2** account creation · **0.3** Notability sketches
- **1.1, 1.3, 1.6** tests in the Civic · **7.1, 7.2** rehearsal and filming
- **Phone must ring or he must speak:** 1.2, 1.5, 2.3, 4.1, 4.2. Claude may run the curl; Daniel confirms what happened.

## Time gates (hours from noon Sat)

Hour 4: no plug-in ring → tap-the-contact trigger. Hour 10: pick the hero. Hour 12: no end-to-end voice → callback → cut Fetch.ai. Never cut: plug-in ring, callback, one real completed action.
