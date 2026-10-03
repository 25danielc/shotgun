# Shotgun task runner. `make help` lists targets.
.PHONY: help setup dev test test-live test-neon db-init lint fmt check-keys ring curl-tools deploy smoke

PORT ?= 8000

help:  ## List targets
	@grep -E '^[a-z-]+:.*##' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

setup:  ## Install Python 3.12 + deps, enable the secret-blocking git hook, create .env
	uv python install 3.12
	uv sync
	git config core.hooksPath .githooks
	@test -f .env || (cp .env.example .env && echo "Created .env from .env.example; fill it in.")

dev:  ## Run the API locally with reload on :$(PORT)
	uv run uvicorn app.main:app --reload --port $(PORT)

test:  ## Run offline tests (live tests skipped)
	uv run pytest -q

test-live:  ## Run tests that hit real APIs / ring the phone (needs .env)
	RUN_LIVE=1 uv run pytest -q -m live

test-neon:  ## Run the suite with Neon (DATABASE_URL) as the test DB; rolled back, no data left
	USE_NEON=1 uv run pytest -q

db-init:  ## Create the job tables in DATABASE_URL (idempotent)
	uv run python -m app.db init

lint:  ## Ruff lint + format check
	uv run ruff check .
	uv run ruff format --check .

fmt:  ## Auto-fix lint and format
	uv run ruff check --fix .
	uv run ruff format .

check-keys:  ## Step 0.2 pass check: one cheap read-only call per key
	uv run python scripts/check_keys.py

ring:  ## Step 1.2: ring MY_PHONE_NUMBER through ElevenLabs (MSG="..." to set the greeting)
	uv run python -m app.telephony $(MSG)

curl-tools:  ## Step 2.2: curl sample ElevenLabs payloads at BASE (default PUBLIC_BASE_URL)
	bash scripts/curl_tools.sh $(BASE)

deploy:  ## Deploy to Railway (see .claude/skills/deploy)
	railway up --detach

smoke:  ## GET /health on BASE_URL (default: PUBLIC_BASE_URL from .env)
	uv run python scripts/smoke.py
