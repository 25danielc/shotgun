---
name: add-worker
description: Use when adding or building a Shotgun background worker (coder 3.1, email 3.2, food 3.4, research 3.5, or a new job type). Gives the pattern - job type, adapter module, state transitions, approval gate, callback summary and tests.
---

# Add a worker

Workers are adapters around a job row. They never talk to each other or to the voice agent; the Neon job table (`app/jobs.py`) is the only shared state. Requires step 2.1 Done.

## 1. Job type
- Add the type name to `JobType` in `app/jobs.py` (the DB check constraint is re-applied by `init_schema`, so existing tables pick it up). `plan` is reserved for raw requests.
- Planner: add a strict tool to `TOOLS` and map it in `TOOL_TYPES` (`app/orchestrator.py`). Use `anyOf` with null for optional fields, and every property goes in `required`.
- Voice status: add a spoken name to `TYPE_NAMES` (`app/voice_tools.py`).
- Define the `details` JSON the worker needs (e.g. email: `to`, `subject`, `body_hint`).

## 2. Adapter module `app/workers/<type>.py`
- `async def run(job) -> None`: claims a `queued` job (`SELECT ... FOR UPDATE SKIP LOCKED`), sets `running`, does the reversible part, writes `summary` + `result`.
- The local Mac food worker uses the same claim query in a polling loop (`python -m app.workers.food --poll`).
- Third-party calls: check current docs first; put endpoints and slugs in a docstring with the date checked; `TODO(verify)` anything unconfirmed.

## 3. State transitions
```
queued -> running -> needs_approval -> approved -> done
                 \-> done            (read-only jobs, e.g. research)
any non-terminal -> failed           (with a speakable summary of why)
```
Use `jobs.transition()` (pass `summary=` on the state the driver should hear about); never write `state` directly. Workers claim work with `jobs.claim_next(conn, [JobType.X])`. Planner-made jobs carry `details["label"]` (short, speakable) and `details["plan_id"]`, and `deadline` when the user set one.

## 4. Approval gate (irreversible actions)
Sending email, ordering or paying, merging a PR: **stop at `needs_approval`** with a summary that ends in a question ("Ramen is $21.40 with tip, lands at 7:12. Confirm?"). The irreversible call runs only in the `approved` handler, which is triggered by `approve_action`. A "no" sets `failed` with summary "Cancelled".

| Worker | Reversible part | Gated part |
|---|---|---|
| coder | issue → Action branch → our PR | merge |
| email | `GMAIL_CREATE_EMAIL_DRAFT` | `GMAIL_SEND_DRAFT` |
| food | cart with items and total | place order (at ETA minus prep time, step 5.2) |
| research | Claude web search | none |

## 5. Callback summary
Callbacks are automatic: the watcher rings once when a job reaches `done`, `needs_approval` or `failed`, so workers don't call telephony themselves. `summary` is read aloud by the callback (`app/callbacks.py`), falling back to the label: at most 2 short sentences, numbers rounded, no URLs or IDs. Spell out what will happen on "yes".

## 6. Tests (`tests/test_step_X_Y_<type>_worker.py`)
- Offline: mock the third-party client; assert the transitions (queued → running → needs_approval, approved → done, a "no" → failed) and that the gated call is **not** made before approval.
- Live (`@pytest.mark.live`): the PLAN.md pass check against the real service.
- Hero workers (3.1, 3.4) also need a recorded successful run as the demo fallback.
