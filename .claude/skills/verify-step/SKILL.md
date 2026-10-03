---
name: verify-step
description: Use when checking whether a Shotgun PLAN.md step passes (e.g. "verify 1.5", "does 2.2 pass?", before marking a step Done). Runs the step's pass check and reports PASS or FAIL with evidence. Never marks a manual (phone, car, account) step verified without Daniel confirming.
---

# Verify a step's pass check

1. **Find the check.** Read the step's "Pass when" in `docs/PLAN.md`. State it word for word.
2. **Classify it:**
   - *Automated:* a test in `tests/test_step_X_Y_*.py` proves it. Run `uv run pytest -q tests/test_step_X_Y_*.py` (add `RUN_LIVE=1` if it's marked live and Daniel has said live calls are OK).
   - *Needs Daniel:* 0.1, 0.2 (partly: `make check-keys` is the evidence), 0.3, 1.1, 1.3, 1.6, 7.1, 7.2, plus the phone-rings checks 1.2, 1.5, 2.3, 4.1, 4.2. Run whatever you can (curl, script, logs), then **ask Daniel what happened**. His answer is the evidence.
3. **Gather evidence:** test output, the exact command, timings (latency checks need numbers), response codes, row counts, PR URLs, Daniel's words with the time. Never paste secret values.
4. **Report** in this shape:
   ```
   Step X.Y: PASS | FAIL | NEEDS DANIEL
   Check: <Pass when, verbatim>
   Evidence: <commands + key output lines / Daniel's confirmation>
   Gaps: <anything not covered, TODO(verify) items>
   ```
5. **Only on PASS:** update the Status in `docs/PLAN.md` to `Done (YYYY-MM-DD HH:MM)`. On FAIL, leave the status alone and suggest the smallest next fix.

Partial passes (e.g. 4 of 5 replugs in 1.3, 2 of 3 rings in 1.6) are FAIL; say how close it got and whether a time gate applies (hour 4: switch to the tap-the-contact trigger).
