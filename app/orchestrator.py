"""Orchestrator: turns a spoken multi-part request into jobs with deadlines.

Build step 2.4: 5 fixture utterances produce the expected job types and deadlines.
Deadline scheduling ("there when I get home") is step 5.2.

Design:
- Claude Sonnet 5.5 (settings.orchestrator_model = "claude-sonnet-5-5") via the official
  `anthropic` SDK, with tool use: one tool per job type (coder, email, food, research).
- Sonnet 5.5 rejects forced tool_choice ("any"/"tool") with a 400: use tool_choice auto,
  strict: true tool schemas, and a prompt instruction to call the tools.
- Input includes the ETA in minutes (app/eta.py) so deadlines are "arrival minus prep time".
- Runs in the background after dispatch_task returns; never on the voice webhook's hot path.
- Allowed fallback (docs/DECISIONS.md): skip planning, let the voice agent call
  dispatch_task(type, details) directly.
- Also the brain behind the Fetch.ai agent (agents/fetch_agent.py, step 6.1).
"""
