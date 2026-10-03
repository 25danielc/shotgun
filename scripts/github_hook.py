"""Register (or update) the demo repo's webhook to POST {PUBLIC_BASE_URL}/github/hook.

Step 3.1 setup, and again whenever the base URL changes (deploy skill). Idempotent: an existing
hook pointing at any */github/hook URL is updated in place. Events: issue_comment (the Claude
Code Action's "Create a PR" link) and pull_request. Never prints the secret.

    uv run python scripts/github_hook.py            # or: make github-hook
    uv run python scripts/github_hook.py --dry-run
    uv run python scripts/github_hook.py --gh       # use the gh CLI's login instead of
                                                   # GITHUB_TOKEN (that token may lack the
                                                   # Webhooks permission; the worker doesn't
                                                   # need it)
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402

EVENTS = ["issue_comment", "pull_request"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--gh", action="store_true", help="authenticate with the gh CLI")
    args = parser.parse_args()
    needed = ["GITHUB_DEMO_REPO", "GITHUB_WEBHOOK_SECRET", "PUBLIC_BASE_URL"]
    if not args.gh:
        needed.append("GITHUB_TOKEN")
    missing = [name for name in needed if not getattr(settings, name.lower())]
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
    path = f"repos/{settings.github_demo_repo}/hooks"
    if args.gh:
        return via_gh(path, body, url, args.dry_run)
    api = f"https://api.github.com/{path}"
    headers = {
        "Authorization": f"Bearer {settings.github_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    hooks = httpx.get(api, headers=headers, timeout=15)
    if hooks.status_code == 403:
        sys.exit("GITHUB_TOKEN lacks the Webhooks permission: add it, or rerun with --gh")
    hooks.raise_for_status()
    existing = find_hook(hooks.json())
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


def find_hook(hooks: list[dict]) -> dict | None:
    return next(
        (h for h in hooks if h.get("config", {}).get("url", "").endswith("/github/hook")), None
    )


def via_gh(path: str, body: dict, url: str, dry_run: bool) -> int:
    """Same create-or-update through `gh api`; the body (with the secret) goes on stdin."""
    listed = subprocess.run(["gh", "api", path], capture_output=True, text=True, check=False)
    if listed.returncode != 0:
        sys.exit("gh api failed: is `gh auth status` logged in with access to the repo?")
    existing = find_hook(json.loads(listed.stdout))
    print(f"{'update hook ' + str(existing['id']) if existing else 'create hook'} via gh: {url}")
    if dry_run:
        return 0
    method, target = ("PATCH", f"{path}/{existing['id']}") if existing else ("POST", path)
    result = subprocess.run(
        ["gh", "api", "-X", method, target, "--input", "-"],
        input=json.dumps(body),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        sys.exit("gh api could not save the hook (output hidden: it may echo the secret)")
    print(f"ok: hook {json.loads(result.stdout)['id']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
