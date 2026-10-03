# Shotgun

![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:hackathon](https://img.shields.io/badge/hackathon-5F43F1)

**Everyone put a chatbot in the car. Shotgun is an agent built for the car.**

Your agent isn't an app. It's a contact. Plug your phone into the car and within about 10 seconds the car rings: "Morning. 31-minute drive home. Anything you want handled?" Say what you need. Shotgun confirms, hands the jobs to background workers and hangs up. When something is done or needs your OK, the car rings again, and nothing irreversible happens without a spoken "yes".

Built solo at MHacks 2026 (Actually Intelligent track).

## How it works

- **Trigger:** an iOS Shortcuts automation "When CarPlay connects" posts the phone's location to our server ([setup](docs/SHORTCUT_SETUP.md)).
- **Voice:** an ElevenLabs agent on a Twilio number, with Claude Haiku 4.5 for voice turns. It calls instant webhook tools and never waits on work.
- **Brain:** a Claude Sonnet 5.5 orchestrator turns the request into jobs, with deadlines set by your arrival time (Google Routes ETA).
- **Workers:** coding (GitHub issue → Claude Code Action → PR), email (Composio Gmail), food (DoorDash CLI) and research (Google Places), sharing a Neon Postgres job table.
- **Callbacks:** finished jobs ring the car with a short summary and ask "Confirm?"
- **Also on ASI:One:** the orchestrator is a Fetch.ai agent on Agentverse. Agent name and address: *TBD (step 6.1)*.

## Run it

```bash
make setup        # uv + Python 3.12 + deps, git secret hook, creates .env
# fill in .env (see .env.example)
make check-keys   # verifies every key with a read-only call
make dev          # http://localhost:8000/health
make test lint
```

Deploy: `make deploy` (Railway). Plan and decisions: [docs/PLAN.md](docs/PLAN.md), [docs/DECISIONS.md](docs/DECISIONS.md).
