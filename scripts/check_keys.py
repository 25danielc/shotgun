"""Step 0.2 pass check: prove every key in .env works with the cheapest read-only call.

Usage: make check-keys   (prints one line per check: OK / FAIL <reason> / SKIP <reason>)
Exit code 0 only if nothing FAILs. Secret values are never printed: every reason string is
scrubbed of all env values before it is shown.

Endpoints were checked against the vendors' docs on 2026-10-03 (see docs/DECISIONS.md, API notes).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
TIMEOUT = 10.0
E164 = re.compile(r"^\+[1-9]\d{7,14}$")

# A known place for Maps checks (Michigan Union -> Ann Arbor Amtrak). Not secret.
ORIGIN = {"latitude": 42.2754, "longitude": -83.7417}
DESTINATION = {"latitude": 42.2877, "longitude": -83.7432}


class CheckFailed(Exception):
    pass


def _get(url: str, **kwargs) -> httpx.Response:
    response = httpx.get(url, timeout=TIMEOUT, **kwargs)
    _raise_for_status(response)
    return response


def _post(url: str, **kwargs) -> httpx.Response:
    response = httpx.post(url, timeout=TIMEOUT, **kwargs)
    _raise_for_status(response)
    return response


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code >= 400:
        raise CheckFailed(f"HTTP {response.status_code}: {response.text[:160]}")


# --- one function per check; each returns a short detail string or raises ----------------


def check_anthropic(env: dict) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=env["ANTHROPIC_API_KEY"], max_retries=0, timeout=TIMEOUT)
    page = client.models.list(limit=1)
    return f"models visible (e.g. {page.data[0].id})" if page.data else "key accepted"


def check_elevenlabs(env: dict) -> str:
    headers = {"xi-api-key": env["ELEVENLABS_API_KEY"]}
    base = "https://api.elevenlabs.io/v1/convai"
    agents = _get(f"{base}/agents", params={"page_size": 1}, headers=headers).json()
    detail = f"{len(agents.get('agents', []))}+ agent(s)"
    if env.get("ELEVENLABS_AGENT_ID"):
        _get(f"{base}/agents/{env['ELEVENLABS_AGENT_ID']}", headers=headers)
        detail += ", agent id found"
    if env.get("ELEVENLABS_PHONE_NUMBER_ID"):
        # TODO(verify): GET /v1/convai/phone-numbers/{id} path
        _get(f"{base}/phone-numbers/{env['ELEVENLABS_PHONE_NUMBER_ID']}", headers=headers)
        detail += ", phone number id found"
    return detail


def check_twilio(env: dict) -> str:
    sid, token = env["TWILIO_ACCOUNT_SID"], env["TWILIO_AUTH_TOKEN"]
    base = f"https://api.twilio.com/2010-04-01/Accounts/{sid}"
    account = _get(f"{base}.json", auth=(sid, token)).json()
    if account.get("status") != "active":
        raise CheckFailed(f"account status is {account.get('status')}")
    detail = f"account active ({account.get('type')})"
    number = env.get("TWILIO_PHONE_NUMBER")
    if number:
        found = _get(
            f"{base}/IncomingPhoneNumbers.json", params={"PhoneNumber": number}, auth=(sid, token)
        ).json()
        if not found.get("incoming_phone_numbers"):
            raise CheckFailed("TWILIO_PHONE_NUMBER is not on this account")
        detail += ", number owned"
    return detail


def check_neon(env: dict) -> str:
    import psycopg

    with psycopg.connect(env["DATABASE_URL"], connect_timeout=10, prepare_threshold=None) as conn:
        version = conn.execute("select version()").fetchone()[0]
    return version.split(" on ")[0]


def check_github(env: dict) -> str:
    headers = {
        "Authorization": f"Bearer {env['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
    }
    login = _get("https://api.github.com/user", headers=headers).json()["login"]
    detail = f"token for {login}"
    repo = env.get("GITHUB_DEMO_REPO")
    if repo:
        perms = _get(f"https://api.github.com/repos/{repo}", headers=headers).json()
        if not perms.get("permissions", {}).get("push"):
            raise CheckFailed(f"token cannot push to {repo}")
        detail += f", can write {repo}"
    return detail


def check_composio(env: dict) -> str:
    # TODO(verify): v3.1 tools listing accepts limit=1; docs show GET /api/v3.1/tools with x-api-key
    _get(
        "https://backend.composio.dev/api/v3.1/tools",
        params={"limit": 1},
        headers={"x-api-key": env["COMPOSIO_API_KEY"]},
    )
    return "key accepted"


def check_google_maps(env: dict) -> str:
    key = env["GOOGLE_MAPS_API_KEY"]
    # Places Text Search with an IDs-only field mask: the free "Essentials (IDs Only)" SKU.
    places = _post(
        "https://places.googleapis.com/v1/places:searchText",
        headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": "places.id"},
        json={"textQuery": "ramen in Ann Arbor", "pageSize": 1},
    ).json()
    # Routes with TRAFFIC_UNAWARE bills as Essentials (10k free/month); proves Routes is enabled.
    routes = _post(
        "https://routes.googleapis.com/directions/v2:computeRoutes",
        headers={"X-Goog-Api-Key": key, "X-Goog-FieldMask": "routes.duration"},
        json={
            "origin": {"location": {"latLng": ORIGIN}},
            "destination": {"location": {"latLng": DESTINATION}},
            "travelMode": "DRIVE",
            "routingPreference": "TRAFFIC_UNAWARE",
        },
    ).json()
    if not places.get("places"):
        raise CheckFailed("Places returned no results")
    if not routes.get("routes"):
        raise CheckFailed("Routes returned no route")
    return f"Places OK, Routes OK ({routes['routes'][0]['duration']})"


def check_railway(env: dict) -> str:
    if not shutil.which("railway"):
        raise CheckFailed("railway CLI not installed")
    result = subprocess.run(
        ["railway", "whoami"], capture_output=True, text=True, timeout=TIMEOUT, check=False
    )
    if result.returncode != 0:
        raise CheckFailed("not logged in (run `railway login`)")
    return "CLI logged in"


def check_secret(name: str) -> Callable[[dict], str]:
    def check(env: dict) -> str:
        if len(env[name]) < 16:
            raise CheckFailed("too short; use `openssl rand -hex 24`")
        return "set (local check only)"

    return check


def check_phone(name: str) -> Callable[[dict], str]:
    def check(env: dict) -> str:
        if not E164.match(env[name]):
            raise CheckFailed("not E.164, e.g. +17345551234")
        return "E.164 format"

    return check


# (label, env vars that must be set, check)
CHECKS: list[tuple[str, list[str], Callable[[dict], str]]] = [
    ("Anthropic", ["ANTHROPIC_API_KEY"], check_anthropic),
    ("ElevenLabs", ["ELEVENLABS_API_KEY"], check_elevenlabs),
    ("Twilio", ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"], check_twilio),
    ("Neon", ["DATABASE_URL"], check_neon),
    ("GitHub", ["GITHUB_TOKEN"], check_github),
    ("Composio", ["COMPOSIO_API_KEY"], check_composio),
    ("Google Maps", ["GOOGLE_MAPS_API_KEY"], check_google_maps),
    ("Railway", [], check_railway),
    ("EVENTS_SHARED_SECRET", ["EVENTS_SHARED_SECRET"], check_secret("EVENTS_SHARED_SECRET")),
    ("TOOLS_SHARED_SECRET", ["TOOLS_SHARED_SECRET"], check_secret("TOOLS_SHARED_SECRET")),
    ("GITHUB_WEBHOOK_SECRET", ["GITHUB_WEBHOOK_SECRET"], check_secret("GITHUB_WEBHOOK_SECRET")),
    ("ALLOWED_CALLER_NUMBER", ["ALLOWED_CALLER_NUMBER"], check_phone("ALLOWED_CALLER_NUMBER")),
    ("MY_PHONE_NUMBER", ["MY_PHONE_NUMBER"], check_phone("MY_PHONE_NUMBER")),
    ("TWILIO_PHONE_NUMBER", ["TWILIO_PHONE_NUMBER"], check_phone("TWILIO_PHONE_NUMBER")),
]


def redact(text: str, env: dict) -> str:
    for value in env.values():
        if value and len(value) >= 6:
            text = text.replace(value, "***")
    return text


def run(env: dict, checks=CHECKS) -> list[tuple[str, str, str]]:
    """Return (label, status, detail) for every check. Never raises."""
    results = []
    for label, required, check in checks:
        missing = [name for name in required if not env.get(name)]
        if missing:
            results.append((label, "FAIL", f"missing {', '.join(missing)} in .env"))
            continue
        try:
            results.append((label, "OK", redact(check(env), env)))
        except Exception as exc:  # noqa: BLE001 - report every failure, keep going
            reason = (
                f"{type(exc).__name__}: {exc}" if not isinstance(exc, CheckFailed) else str(exc)
            )
            results.append((label, "FAIL", redact(reason, env)[:240]))
    return results


def main() -> int:
    env_file = ROOT / ".env"
    if not env_file.exists():
        print("No .env found. Run `make setup`, then fill in .env.")
        return 1
    env = {k: v for k, v in dotenv_values(env_file).items() if v}
    env.update({k: v for k, v in os.environ.items() if k in env and v})
    results = run(env)
    width = max(len(label) for label, _, _ in results)
    for label, status, detail in results:
        print(f"{label:<{width}}  {status:<4}  {detail}")
    failed = sum(status == "FAIL" for _, status, _ in results)
    print(f"\n{len(results) - failed}/{len(results)} OK")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
