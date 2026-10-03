---
name: build-step
description: Use when implementing a Shotgun build step from docs/PLAN.md (e.g. "do step 2.1", "next step", "build the job table"). Takes one step from reading its row to a passing check, a status update and a commit. Stops and asks Daniel for steps that need the phone, the car or an account.
---

# Build one PLAN.md step

One step per session or task. PLAN.md is the source of truth for IDs, dependencies, pass checks and priority.

## 1. Name the step
Say "Step X.Y: <title>" first. If the user didn't give an ID, pick the first `Not started` Core row whose "Needs" are all done, and confirm it. Stretch rows (6.1, 3.2, 3.5, 4.3, in that order) only after 1.6 is Done.

## 2. Read and check dependencies
- Read the step's row in `docs/PLAN.md`: Needs, Pass when, Hrs, Priority.
- Every step in "Needs" must have Status `Done`. If not, stop and say which one is missing. Exception: if Daniel says to build ahead, build and test offline, then set Status to `Blocked: <what's left>`. Never Done until the real check passes.
- Skim `docs/DECISIONS.md` sections that touch this step (decisions, cut list §5, conflicts §9, API notes §11) and the stub module's docstring (see the CLAUDE.md module table).
- Check the clock against the time gates (CLAUDE.md). If a gate has passed, raise it before starting.

## 3. Does it need Daniel?
- **Daniel only:** 0.1, 0.2 (accounts), 0.3, 1.1, 1.3, 1.6, 7.1, 7.2. Write down exactly what he must do, help with any code or config, then **stop and ask him to report the result**. Never mark these Done yourself.
- **Phone must ring / he must speak:** 1.2, 1.5, 2.3, 4.1, 4.2. Build and test everything offline, run the live command, then ask him "Did the phone ring / what did the agent say?" before marking Done.

## 4. Plan
Write a short plan: files to touch, the data shapes, the test that proves the pass check, and any third-party API detail you must check. **Look up current docs for any third-party endpoint, parameter or model name** (DECISIONS.md §11 has notes from 2026-10-03). Mark anything you can't confirm `TODO(verify)`.

## 5. Implement
- Keep to the stub's module boundary; match the surrounding style.
- Config only through `app/config.py` settings; new env vars go in `.env.example`, `app/config.py`, the CLAUDE.md env list and, if they're keys, `scripts/check_keys.py`.
- Hard rules: tool webhooks < 500 ms and never block on workers; nothing irreversible without `approve_action`; secrets checks stay on; no secrets in code.

## 6. Write the pass-check test
`tests/test_step_X_Y_<slug>.py`. The test should encode the PLAN.md "Pass when" as literally as possible.
- Offline by default: fake HTTP with `httpx.MockTransport`, a fake clock for deadlines, fake Claude/telephony clients via monkeypatch. DB tests take the `db` fixture (tests/conftest.py): a connection inside a transaction that's always rolled back, on embedded Postgres by default or on Neon with `make test-neon`.
- Sample third-party payloads live in `tests/fixtures/` (e.g. `elevenlabs/`, `planner_utterances.json`).
- Anything that calls real APIs or rings a phone: `@pytest.mark.live`. Run one step's with `make test-live T=tests/test_step_X_Y_*.py`; a bare `make test-live` rings the phone.
- Latency rules get a test: time the handler and assert < 0.5 s.

## 7. Run it
`make test && make lint`, plus `make test-live T=<the step's test file>` when the step's check is live. Use the `verify-step` skill to report pass or fail with evidence.

## 8. Record and commit
- Set the step's Status in `docs/PLAN.md` to `Done (YYYY-MM-DD HH:MM)`, or `Blocked: <reason>`.
- New decisions or deviations go in the DECISIONS.md decision log (dated).
- Commit only the step's files: `git commit -m "step X.Y: <summary> (closes #N)"`. Find N with `gh issue list --search "X.Y:" --state open`. Don't push unless asked. The secret hook must pass; never bypass it.
