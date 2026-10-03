"""Copy every non-empty value from .env into the Railway service's variables.

Step 1.4 and after any .env change. Prints names only, never values. Uses the installed CLI's
syntax (railway 4.10: `railway variables --set K=V --skip-deploys`); `make deploy` afterwards
picks the values up.

    uv run python scripts/railway_env.py [--service shotgun] [--dry-run]    # or: make railway-env
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--service", default="shotgun")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    values = {k: v for k, v in dotenv_values(ROOT / ".env").items() if v}
    if not values:
        sys.exit("no values in .env")
    print(f"setting {len(values)} variables on service {args.service}: {', '.join(sorted(values))}")
    if args.dry_run:
        return 0
    command = ["railway", "variables", "--service", args.service, "--skip-deploys"]
    for key, value in values.items():
        command += ["--set", f"{key}={value}"]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        # The CLI may echo what it was given, so never print its raw output.
        sys.exit(f"railway variables failed (exit {result.returncode}); run `railway whoami`")
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
