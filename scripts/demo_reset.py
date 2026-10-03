"""Demo reset: put the demo repo and the database back to "before the drive" (steps 7.1, 7.2).

    make demo-reset                  # both, asks nothing; prints what it did
    make demo-reset ARGS=--dry-run   # show what would change, change nothing
    uv run python scripts/demo_reset.py --repo-only | --db-only

Every rehearsal merges the fix for the planted login bug, so the next run would have nothing to
fix. This restores it:

Demo repo (GITHUB_DEMO_REPO, through the `gh` CLI login like `make github-hook`, because the
fine-grained GITHUB_TOKEN can't delete branches or read Actions):
- every file changed since the baseline commit ("Tiny login app with a planted bug ...") goes
  back to its baseline version, or is deleted if it didn't exist then. `.github/` is left
  alone, so the Claude Code and Tests workflows stay.
- open PRs from claude/ branches are closed, claude/ branches deleted, and open issues that
  Shotgun filed ("Filed by Shotgun" in the body) closed.

Database (DATABASE_URL):
- open drives are closed and marked arrival_called, so no arrival call rings for them.
- open jobs are failed with summary "Reset before the demo" and marked announced, so nothing
  reports them.

Idempotent: a second run finds nothing to do.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db, jobs  # noqa: E402
from app.config import settings  # noqa: E402
from app.jobs import JobState  # noqa: E402

BASELINE_MESSAGE = "Tiny login app with a planted bug"
KEEP_PREFIX = ".github/"
BRANCH_PREFIX = "claude/"
FILED_BY = "Filed by Shotgun"
RESET_SUMMARY = "Reset before the demo"

Runner = Callable[..., str]


def run(*args: str, cwd: Path | None = None) -> str:
    """Run a command, return stdout; raise with stderr on failure."""
    done = subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)
    if done.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:3])} failed: {done.stderr.strip()[:300]}")
    return done.stdout


def gh_json(runner: Runner, *args: str):
    return json.loads(runner("gh", *args) or "null")


@dataclass
class RepoPlan:
    baseline: str
    restore: list[str] = field(default_factory=list)  # files to set back to the baseline
    delete: list[str] = field(default_factory=list)  # files added after the baseline
    prs: list[int] = field(default_factory=list)
    branches: list[str] = field(default_factory=list)
    issues: list[int] = field(default_factory=list)

    def empty(self) -> bool:
        return not (self.restore or self.delete or self.prs or self.branches or self.issues)


def find_baseline(runner: Runner, repo: str) -> str:
    commits = gh_json(runner, "api", f"repos/{repo}/commits?per_page=100")
    for commit in commits:
        if commit["commit"]["message"].startswith(BASELINE_MESSAGE):
            return commit["sha"]
    raise RuntimeError(f"no commit starting {BASELINE_MESSAGE!r} in {repo}")


def plan_repo(runner: Runner, repo: str) -> RepoPlan:
    plan = RepoPlan(baseline=find_baseline(runner, repo))
    compare = gh_json(runner, "api", f"repos/{repo}/compare/{plan.baseline}...main")
    for changed in compare.get("files", []):
        name = changed["filename"]
        if name.startswith(KEEP_PREFIX):
            continue
        if changed["status"] == "added":
            plan.delete.append(name)
        else:  # modified, removed or renamed: put the baseline version back
            plan.restore.append(changed.get("previous_filename", name))
            if changed["status"] == "renamed":
                plan.delete.append(name)
    pulls = gh_json(runner, "api", f"repos/{repo}/pulls?state=open&per_page=100")
    plan.prs = [p["number"] for p in pulls if p["head"]["ref"].startswith(BRANCH_PREFIX)]
    branches = gh_json(runner, "api", f"repos/{repo}/branches?per_page=100")
    plan.branches = [b["name"] for b in branches if b["name"].startswith(BRANCH_PREFIX)]
    issues = gh_json(runner, "api", f"repos/{repo}/issues?state=open&per_page=100")
    plan.issues = [
        i["number"] for i in issues if "pull_request" not in i and FILED_BY in (i.get("body") or "")
    ]
    return plan


def apply_repo(runner: Runner, repo: str, plan: RepoPlan) -> None:
    for number in plan.prs:
        runner("gh", "pr", "close", str(number), "-R", repo, "--comment", RESET_SUMMARY)
    for number in plan.issues:
        runner("gh", "issue", "close", str(number), "-R", repo, "--comment", RESET_SUMMARY)
    for branch in plan.branches:
        runner("gh", "api", "-X", "DELETE", f"repos/{repo}/git/refs/heads/{branch}")
    if not (plan.restore or plan.delete):
        return
    with tempfile.TemporaryDirectory(prefix="shotgun-demo-") as tmp:
        work = Path(tmp) / "repo"
        runner("gh", "repo", "clone", repo, str(work), "--", "--quiet")
        if plan.restore:
            runner("git", "checkout", plan.baseline, "--", *plan.restore, cwd=work)
        if plan.delete:
            runner("git", "rm", "-q", "--", *plan.delete, cwd=work)
        runner(
            "git",
            "commit",
            "-q",
            "-m",
            "Demo reset: restore the planted login bug\n\nShotgun scripts/demo_reset.py",
            cwd=work,
        )
        runner("git", "push", "-q", "origin", "HEAD:main", cwd=work)


async def reset_db(conn, now: datetime | None = None) -> tuple[int, int]:
    """Close open drives and fail open jobs quietly. Returns (drives closed, jobs failed)."""
    now = now or datetime.now(UTC)
    cur = await conn.execute(
        "update drives set ended_at = %s, arrival_called = true where ended_at is null "
        "returning id",
        (now,),
    )
    closed = len(await cur.fetchall())
    failed = 0
    for job in await jobs.list_jobs(conn):
        try:
            await jobs.transition(
                conn, job.id, JobState.FAILED, summary=RESET_SUMMARY, note="demo reset"
            )
        except jobs.IllegalTransition:
            continue  # finished in the meantime
        await jobs.set_announced(conn, job.id, JobState.FAILED)
        failed += 1
    return closed, failed


def describe(plan: RepoPlan) -> list[str]:
    if plan.empty():
        return ["demo repo: already reset"]
    lines = []
    if plan.restore or plan.delete:
        lines.append(
            f"demo repo: restore {plan.restore or '-'} and delete {plan.delete or '-'} "
            f"from baseline {plan.baseline[:7]}"
        )
    if plan.prs:
        lines.append(f"demo repo: close PRs {plan.prs}")
    if plan.branches:
        lines.append(f"demo repo: delete branches {plan.branches}")
    if plan.issues:
        lines.append(f"demo repo: close issues {plan.issues}")
    return lines


async def _db(dry_run: bool) -> str:
    pool = await db.open_pool()
    try:
        async with pool.connection() as conn:
            if dry_run:
                cur = await conn.execute("select count(*) from drives where ended_at is null")
                open_drives = (await cur.fetchone())[0]
                open_jobs = len(await jobs.list_jobs(conn))
                return f"database: would close {open_drives} drive(s), fail {open_jobs} job(s)"
            closed, failed = await reset_db(conn)
            return f"database: closed {closed} drive(s), failed {failed} job(s)"
    finally:
        await db.close_pool()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    which = parser.add_mutually_exclusive_group()
    which.add_argument("--repo-only", action="store_true")
    which.add_argument("--db-only", action="store_true")
    args = parser.parse_args()
    if not args.db_only:
        plan = plan_repo(run, settings.github_demo_repo)
        for line in describe(plan):
            print(("[dry-run] " if args.dry_run else "") + line)
        if not args.dry_run and not plan.empty():
            apply_repo(run, settings.github_demo_repo, plan)
            print("demo repo: done")
    if not args.repo_only:
        print(asyncio.run(_db(args.dry_run)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
