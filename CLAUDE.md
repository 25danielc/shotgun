# Shotgun

An AI agent saved as a phone contact. Plug the phone into the car and the car rings ("Shotgun"); you speak a multi-part request, the voice agent answers searches and drafts on the spot, dispatches longer jobs (asking for any "yes" up front) and stays on the line until you say goodbye. Workers run in the background, and about 3 minutes before you arrive the car rings once with a batched summary. **At most two calls per drive** (departure, arrival) plus rare exception calls (D17). Nothing irreversible happens without a spoken "yes". Solo build for MHacks 2026. **Deadline: Sun Oct 4, 12:00 PM EDT.**

- **[docs/PLAN.md](docs/PLAN.md)** is the source of truth for build steps, IDs, pass checks, priorities and status.
- **[docs/DECISIONS.md](docs/DECISIONS.md)** explains why: decisions, rejected options, cut list, sponsor rules, fallback gates, open questions, API notes.

## Architecture

```
iPhone Shortcut (CarPlay connects) ─POST /events─▶ FastAPI on Railway ─▶ ElevenLabs outbound call ─▶ car rings
   (call policy 4.3 decides whether the departure call rings; plug-in opens a drive)
Car call (ElevenLabs Agent + Twilio) ─/tools/*──▶ inline (<8 s): search_web / draft_message / set_destination
                                                  background (<500 ms): dispatch_task / get_status / approve_action
dispatch(type, preapproval?) ─▶ Neon drives + jobs ◀─ workers: coder | research (email, food stretch)
arrival scheduler (ETA − 3 min, or all jobs terminal/held) ─▶ one call, batched summary ─▶ approve_action
exception job (pre-approval broken, action pending) ─▶ exception call (≤ 1 per 10 min)
CarPlay disconnects ─▶ close drive, cancel arrival call, ntfy recap
```

Each module's docstring names its role and build step.

## Fixed stack (do not reopen these without asking Daniel)

- Trigger: iOS Shortcuts "When CarPlay connects/disconnects" → POST /events. No Siri anywhere.
- Voice + phone: ElevenLabs Agents on a Twilio voice number. Voice LLM: `claude-haiku-4-5` in ElevenLabs.
- Orchestrator: Claude Sonnet 5.5 (`claude-sonnet-5-5`) via the `anthropic` SDK, tool use.
- Server: Python 3.12 FastAPI on Railway (ngrok fallback). No self-hosted models.
- Job table: Neon Postgres via psycopg 3. Shared by all agents; no agent-to-agent protocol.
- Coder: GitHub issue with @claude → Claude Code GitHub Action on `shotgun-demo-app`.
- Email: Composio Gmail on a throwaway account. Food: official DoorDash CLI or a cart-only browser agent.
- Maps: Google Routes API only (ETA once at departure, to the destination the driver names, D17). Research and `search_web` use Claude web search, not Places or another search API. No other Google Cloud.
- Calls are the channel, never SMS. The only push is the unplug recap via ntfy (`NTFY_TOPIC`). Hero action is the coding PR (the D17 demo script is coder-only).

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
- **Background tools (`dispatch_task`, `get_status`, `approve_action`) answer in under 500 ms and never block on workers.** Insert a row, return, do the work in the background. **Inline tools (`search_web`, `draft_message`, `set_destination`) are capped at 8 s**, and the voice agent says a filler line first.
- **Nothing irreversible (send, order, pay, merge) without a spoken yes**: a pre-approval given at dispatch, or `approve_action`. Workers without a pre-approval stop at `needs_approval`; a result that breaks its pre-approval goes to `exception`.
- **The agent never ends a call to "continue later".** `end_call` only for: a goodbye / "that's all", a refused caller, or 60 s silence → "Anything else?" → 30 s more silence.
- **Caller allowlist and shared secrets stay on**, in dev too: `/events` checks `EVENTS_SHARED_SECRET`, `/tools/*` check `TOOLS_SHARED_SECRET` and the caller number.
- **Verify third-party API details against current docs** before coding them (ElevenLabs, Fetch.ai, Composio, Google, GitHub Action). Mark anything unconfirmed `TODO(verify)`. DECISIONS.md §11 caches what was checked on 2026-10-03.
- Respect the cut list (DECISIONS.md §5) and the stretch order: only after milestone 1 (1.6) works.

## Commands

`make help` lists every target. Two traps it doesn't show:
- `make test-live` without `T=<file>` runs every `@live` test and **rings the phone**.
- `make test-neon` briefly locks `jobs` in Neon: never during a live demo.

## Steps that need Daniel physically: stop and ask, never fake a pass

- **0.1** DoorDash waitlist · **0.2** account creation · **0.3** Notability sketches
- **1.1, 1.3, 1.6** tests in the Civic · **7.1, 7.2** rehearsal and filming
- **Phone must ring or he must speak:** 1.2, 1.5, 2.3, 4.1, 4.2, 4.4 (push arrives). Claude may run the curl; Daniel confirms what happened.
- **Shortcut edits:** 5.1 (plug-in automation sends lat/lng).
- **Every ElevenLabs push** (`scripts/apply_agent.py`): ask first; it changes the live agent.

## Time gates (hours from noon Sat)

Hour 4: no plug-in ring → tap-the-contact trigger. Hour 10: pick the hero. Hour 12 (Fetch.ai) is moot: 6.1 was cut at hour 5.7 (D17). Never cut: plug-in ring, the arrival call, one real completed action.
