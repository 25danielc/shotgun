#!/usr/bin/env python3
"""Block commits that include .env files or anything that looks like an API key.

Two entry points, one scanner (stdlib only, so it runs without the venv):
- git pre-commit hook (.githooks/pre-commit, enabled by `make setup`):
      python3 scripts/hooks/check_secrets.py --staged          -> exit 1 to block
- Claude Code PreToolUse hook on Bash (.claude/settings.json), reads the tool call on stdin:
      python3 scripts/hooks/check_secrets.py --claude-hook     -> exit 2 to block

False positive? Put `secret-scan: allow` on the offending line.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys

BLOCKED_FILE = re.compile(r"(^|/)\.env(\..+)?$|\.(pem|key)$")
ALLOWED_FILES = {".env.example"}

KEY_PATTERNS = [
    ("Anthropic key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("ElevenLabs-style sk_ key", re.compile(r"\bsk_[A-Za-z0-9]{32,}")),
    ("GitHub token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Twilio API key", re.compile(r"\bSK[0-9a-f]{32}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "password in a database URL",
        re.compile(r"postgres(?:ql)?://[^:\s/]+:(?!PASSWORD@|password@|<)[^@\s]{6,}@"),
    ),
    (
        "secret assigned to a variable",
        re.compile(
            r"(?i)\b[A-Z0-9_]*(API_KEY|SECRET|TOKEN|PASSWORD)\s*[=:]\s*['\"]?"
            r"(?!your|changeme|xxx|<)[A-Za-z0-9_\-\.]{16,}"
        ),
    ),
]
ALLOW_MARKER = "secret-scan: allow"


def git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=False).stdout


def scan(files: list[str], diff: str) -> list[str]:
    """Return human-readable problems for the given file list and unified diff."""
    problems = []
    for path in files:
        if BLOCKED_FILE.search(path) and path.rsplit("/", 1)[-1] not in ALLOWED_FILES:
            problems.append(f"{path}: secret file must not be committed")
    current = "?"
    for line in diff.splitlines():
        if line.startswith("+++ "):
            current = line[6:] if line.startswith("+++ b/") else line[4:]
            continue
        if not line.startswith("+") or ALLOW_MARKER in line:
            continue
        for label, pattern in KEY_PATTERNS:
            if pattern.search(line):
                problems.append(f"{current}: looks like a {label}")
                break
    return problems


def staged_problems(include_unstaged: bool = False) -> list[str]:
    files = git("diff", "--cached", "--name-only", "--diff-filter=ACMR").split()
    diff = git("diff", "--cached", "-U0", "--diff-filter=ACMR")
    if include_unstaged:  # `git commit -a` also commits modified tracked files
        files += git("diff", "--name-only", "--diff-filter=ACMR").split()
        diff += git("diff", "-U0", "--diff-filter=ACMR")
    return scan(files, diff)


def report(problems: list[str]) -> None:
    print("Blocked: possible secrets in this commit.", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    print(
        "Keys belong in .env (git-ignored) and Railway variables only. "
        f"For a false positive, add '{ALLOW_MARKER}' to the line.",
        file=sys.stderr,
    )


def main() -> int:
    if "--claude-hook" in sys.argv:
        try:
            command = json.load(sys.stdin).get("tool_input", {}).get("command", "")
        except (json.JSONDecodeError, AttributeError):
            return 0
        if re.search(r"\bgit\s+add\b.*(\s-f\b|--force).*\.env", command):
            report([".env: force-adding an env file"])
            return 2
        if not re.search(r"\bgit\b[^;&|]*\bcommit\b", command):
            return 0
        include_unstaged = bool(re.search(r"\bcommit\b[^;&|]*\s(-a\b|--all\b|-am\b)", command))
        problems = staged_problems(include_unstaged)
        if problems:
            report(problems)
            return 2
        return 0

    problems = staged_problems()
    if problems:
        report(problems)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
