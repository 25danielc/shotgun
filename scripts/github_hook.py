"""Register (or update) the demo repo's webhook to POST {PUBLIC_BASE_URL}/github/hook.

Step 3.1 setup, and again whenever the base URL changes (deploy skill). Idempotent: an existing
hook pointing at any */github/hook URL is updated in place. Events: issue_comment (the Claude
Code Action's "Create a PR" link) and pull_request. Never prints the secret.

    uv run python scripts/github_hook.py            # or: make github-hook
    uv run python scripts/github_hook.py --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402

EVENTS = ["issue_comment", "pull_request"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    missing = [
        name
        for name in ("GITHUB_TOKEN", "GITHUB_DEMO_REPO", "GITHUB_WEBHOOK_SECRET", "PUBLIC_BASE_URL")
        if not getattr(settings, name.lower())
    ]
    if missing:
        sys.exit(f"set {', '.join(missing)} in .env")

    url = settings.public_base_url.rstrip("/") + "/github/hook"
    body = {
        "active": True,
        "events": EVENTS,
        "config": {
            "url": url,
            "content_type": "json",
            "secret": settings.github_webhook_secret,
            "insecure_ssl": "0",
        },
    }
    api = f"https://api.github.com/repos/{settings.github_demo_repo}/hooks"
    headers = {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    hooks = httpx.get(api, headers=headers, timeout=15)
    hooks.raise_for_status()
    existing = next(
        (h for h in hooks.json() if h.get("config", {}).get("url", "").endswith("/github/hook")),
        None,
    )
    action = f"update hook {existing['id']}" if existing else "create hook"
    print(f"{action}: {url} events={EVENTS}")
    if args.dry_run:
        return 0
    if existing:
        response = httpx.patch(f"{api}/{existing['id']}", json=body, headers=headers, timeout=15)
    else:
        response = httpx.post(api, json=body, headers=headers, timeout=15)
    if response.status_code >= 400:
        sys.exit(f"GitHub said HTTP {response.status_code}: {response.text[:300]}")
    print(f"ok: hook {response.json()['id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
