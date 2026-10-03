"""The secret-blocking hook (scripts/hooks/check_secrets.py) blocks .env and key-shaped strings.

Fake keys are assembled at runtime so this file itself never trips the scanner.
"""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "hooks" / "check_secrets.py"
FAKE_ANTHROPIC = "sk-" + "ant-" + "a1B2c3D4" * 5
FAKE_GITHUB = "gh" + "p_" + "Z9y8X7w6" * 5


@pytest.fixture
def repo(tmp_path):
    def git(*args):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "test")
    git("config", "core.hooksPath", "/dev/null")
    (tmp_path / "README.md").write_text("hello\n")
    git("add", "README.md")
    git("commit", "-qm", "init")
    return SimpleNamespace(path=tmp_path, git=git)


def run_hook(repo, *args, stdin=""):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=repo.path,
        input=stdin,
        capture_output=True,
        text=True,
    )


def claude_payload(command):
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})


def test_clean_commit_passes(repo):
    (repo.path / "app.py").write_text("print('hi')\n")
    repo.git("add", "app.py")
    assert run_hook(repo, "--staged").returncode == 0
    assert run_hook(repo, "--claude-hook", stdin=claude_payload("git commit -m x")).returncode == 0


def test_env_file_blocked(repo):
    (repo.path / ".env").write_text("FOO=bar\n")
    repo.git("add", "-f", ".env")
    result = run_hook(repo, "--staged")
    assert result.returncode == 1
    assert ".env" in result.stderr


def test_env_example_allowed(repo):
    (repo.path / ".env.example").write_text("ANTHROPIC_API_KEY=\n")
    repo.git("add", ".env.example")
    assert run_hook(repo, "--staged").returncode == 0


@pytest.mark.parametrize("secret", [FAKE_ANTHROPIC, FAKE_GITHUB])
def test_key_in_code_blocked(repo, secret):
    (repo.path / "config.py").write_text(f'KEY = "{secret}"\n')
    repo.git("add", "config.py")
    assert run_hook(repo, "--staged").returncode == 1
    result = run_hook(repo, "--claude-hook", stdin=claude_payload("git commit -m 'oops'"))
    assert result.returncode == 2
    assert "config.py" in result.stderr


def test_commit_all_scans_unstaged_tracked_changes(repo):
    (repo.path / "README.md").write_text(f"token {FAKE_GITHUB}\n")
    assert run_hook(repo, "--claude-hook", stdin=claude_payload("git commit -am x")).returncode == 2


def test_force_add_env_blocked(repo):
    payload = claude_payload("git add -f .env")
    assert run_hook(repo, "--claude-hook", stdin=payload).returncode == 2


def test_non_git_commands_ignored(repo):
    (repo.path / ".env").write_text("FOO=bar\n")
    repo.git("add", "-f", ".env")
    assert run_hook(repo, "--claude-hook", stdin=claude_payload("ls -la")).returncode == 0


def test_allow_marker(repo):
    (repo.path / "doc.md").write_text(f"example {FAKE_ANTHROPIC}  secret-scan: allow\n")
    repo.git("add", "doc.md")
    assert run_hook(repo, "--staged").returncode == 0
