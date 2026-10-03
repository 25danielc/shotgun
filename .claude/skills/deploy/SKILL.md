---
name: deploy
description: Use when deploying Shotgun to Railway, after changing env vars, or when the public URL changes (step 1.4 and every later deploy). Deploys, runs the smoke check against the public URL, and updates ElevenLabs tool and webhook URLs if the base URL changed. Falls back to ngrok.
---

# Deploy to Railway

Railway docs checked 2026-10-03: Railpack builder (reads `.python-version` = 3.12, `pyproject.toml` + `uv.lock`), config in `railway.json` (start command `uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}`, healthcheck `/health`). Containers stay running (app sleeping is opt-in), so background loops work.

## First time (step 1.4, ask Daniel before creating the project)
1. `railway login` (Daniel; browser) **in this terminal** (`! railway login`): logging in on the website doesn't log the CLI in. Check with `railway whoami`.
2. `railway init` (new project "shotgun") or `railway link` to an existing one.
3. Set variables from `.env` without echoing them: `make railway-env` (scripts/railway_env.py, prints names only). The installed CLI is **4.10**: the syntax is `railway variables --service shotgun --set K=V --skip-deploys`. Newer CLIs prefer `railway variable set`. Never paste values into chat or commit them.
4. `make deploy` (`railway up --detach`), then `railway logs` until the healthcheck passes.
5. `railway domain` creates the public URL. Put it in `.env` as `PUBLIC_BASE_URL` and in Railway variables.
6. TODO(verify): Railpack puts the venv's `uvicorn` on PATH for the start command. If the deploy fails with "uvicorn: not found", switch `startCommand` to `python -m uvicorn ...` and re-check the Railpack Python docs.

## Every deploy
1. `make test && make lint` must pass first.
2. `make deploy`, then `railway logs` for build errors.
3. **Smoke check:** `make smoke` (GET `$PUBLIC_BASE_URL/health` must return 200). Once `/events` exists, also check that a wrong secret gives 401:
   `curl -s -o /dev/null -w "%{http_code}" -X POST "$PUBLIC_BASE_URL/events" -H "X-Shotgun-Secret: wrong" -d '{}'` should print `401`.
4. With keys set, also run `make curl-tools` (step 2.2 check against the public URL) and look for `database ready`, and for no "not running" warnings about planner/callbacks/coder, in `railway logs`.
5. Report the URL, the commit deployed, and the smoke results.

## If the base URL changed
Everything that points at the server must be updated:
- ElevenLabs: update `PUBLIC_BASE_URL` in `.env`, then `uv run python scripts/apply_agent.py --stage full`. It PATCHes every tool URL (`PATCH /v1/convai/tools/{tool_id}`) and the conversation initiation webhook (`{{BASE_URL}}/tools/init`).
- The iPhone Shortcut's URL (Daniel, on the phone).
- The demo repo's GitHub webhook (`/github/hook`): `make github-hook` creates or updates it (events `issue_comment`, `pull_request`; secret `GITHUB_WEBHOOK_SECRET`).
- `.env` and Railway `PUBLIC_BASE_URL`.

## Fallback: ngrok from the laptop
`make dev` in one terminal, `ngrok http 8000` in another, then treat the ngrok URL as a base URL change (above). Free ngrok URLs change on restart, so use a reserved domain if one is available.
