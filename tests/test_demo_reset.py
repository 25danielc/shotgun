"""make demo-reset (scripts/demo_reset.py): restore the planted bug and clear stale drives/jobs.

Offline: a fake `gh`/`git` runner that answers like the GitHub API and records commands; the
database part on the test Postgres. The real run was checked by hand on 2026-10-03 19:25
(restored auth.py + tests/test_auth.py from d22264d, deleted the old claude/ branch, closed
issue #3; a second run said "already reset").
"""

import importlib.util
import json
import sys
from pathlib import Path

from app import drives, jobs
from app.jobs import JobState, JobType
from tests.helpers import run

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("demo_reset", ROOT / "scripts" / "demo_reset.py")
reset = importlib.util.module_from_spec(spec)
sys.modules["demo_reset"] = reset  # dataclasses look their module up while it loads
spec.loader.exec_module(reset)

REPO = "25danielc/shotgun-demo-app"
BASELINE = "d22264d" + "0" * 33


class FakeGitHub:
    def __init__(self, *, files=None, pulls=None, branches=None, issues=None):
        self.answers = {
            f"repos/{REPO}/commits?per_page=100": [
                {"sha": "ea698d1", "commit": {"message": "CI: run pytest on PRs"}},
                {"sha": BASELINE, "commit": {"message": "Tiny login app with a planted bug\n"}},
            ],
            f"repos/{REPO}/compare/{BASELINE}...main": {"files": files or []},
            f"repos/{REPO}/pulls?state=open&per_page=100": pulls or [],
            f"repos/{REPO}/branches?per_page=100": branches or [{"name": "main"}],
            f"repos/{REPO}/issues?state=open&per_page=100": issues or [],
        }
        self.commands = []

    def __call__(self, *args, cwd=None):
        self.commands.append(args)
        if args[:2] == ("gh", "api") and len(args) == 3:
            return json.dumps(self.answers[args[2]])
        return ""


DIRTY = dict(
    files=[
        {"filename": "auth.py", "status": "modified"},
        {"filename": "tests/test_auth.py", "status": "modified"},
        {"filename": "tests/test_login_case.py", "status": "added"},
        {"filename": ".github/workflows/tests.yml", "status": "added"},
    ],
    pulls=[
        {"number": 7, "head": {"ref": "claude/issue-6-20261004-0900"}},
        {"number": 8, "head": {"ref": "daniel/manual"}},
    ],
    branches=[{"name": "main"}, {"name": "claude/issue-6-20261004-0900"}],
    issues=[
        {"number": 6, "body": "...Filed by Shotgun (job 190)."},
        {"number": 9, "body": "a human issue"},
        {"number": 7, "body": "Filed by Shotgun", "pull_request": {}},
    ],
)


def test_plan_restores_the_baseline_and_keeps_the_workflows():
    plan = reset.plan_repo(FakeGitHub(**DIRTY), REPO)
    assert plan.baseline == BASELINE
    assert plan.restore == ["auth.py", "tests/test_auth.py"]
    assert plan.delete == ["tests/test_login_case.py"]  # .github/ is never touched
    assert plan.prs == [7]  # only claude/ branches
    assert plan.branches == ["claude/issue-6-20261004-0900"]
    assert plan.issues == [6]  # only Shotgun's, and not the PR


def test_apply_closes_deletes_restores_and_pushes():
    gh = FakeGitHub(**DIRTY)
    plan = reset.plan_repo(gh, REPO)
    gh.commands.clear()
    reset.apply_repo(gh, REPO, plan)
    names = [" ".join(c[:3]) for c in gh.commands]
    assert names[:3] == ["gh pr close", "gh issue close", "gh api -X"]
    assert gh.commands[2][-1] == f"repos/{REPO}/git/refs/heads/claude/issue-6-20261004-0900"
    assert ("git", "checkout", BASELINE, "--", "auth.py", "tests/test_auth.py") in gh.commands
    assert ("git", "rm", "-q", "--", "tests/test_login_case.py") in gh.commands
    assert gh.commands[-1] == ("git", "push", "-q", "origin", "HEAD:main")


def test_clean_repo_needs_nothing():
    gh = FakeGitHub()
    plan = reset.plan_repo(gh, REPO)
    assert plan.empty()
    assert reset.describe(plan) == ["demo repo: already reset"]
    gh.commands.clear()
    reset.apply_repo(gh, REPO, plan)
    assert gh.commands == []


async def test_db_reset_closes_drives_and_fails_open_jobs_quietly(db, rang):
    drive = await drives.open_drive(db)
    running = await jobs.create_job(db, JobType.CODER, drive_id=drive.id)
    await run(db, running.id, "running")
    held = await jobs.create_job(db, JobType.CODER, drive_id=drive.id)
    await run(db, held.id, "running", "needs_approval", summary="Merge it?")
    done = await jobs.create_job(db, JobType.RESEARCH, drive_id=drive.id)
    await run(db, done.id, "running", "done", summary="It's 54 degrees.")

    assert await reset.reset_db(db) == (1, 2)
    gone = await drives.get_drive(db, drive.id)
    assert gone.ended_at is not None and gone.arrival_called is True
    for job_id in (running.id, held.id):
        job = await jobs.get_job(db, job_id)
        assert (job.state, job.summary, job.announced_state) == (
            JobState.FAILED,
            "Reset before the demo",
            JobState.FAILED,
        )
    assert (await jobs.get_job(db, done.id)).state is JobState.DONE
    assert await reset.reset_db(db) == (0, 0)  # idempotent

    from app import calls

    assert await calls.tick(db) is None  # nothing rings about any of it
    assert rang == []
