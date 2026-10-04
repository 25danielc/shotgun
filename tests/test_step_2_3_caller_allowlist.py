"""Step 2.3 (server side): caller allowlist via the conversation initiation webhook.

Pass check (live, Daniel): "unknown number refused". Offline: /tools/init answers instantly with
every dynamic variable the agent defines, greets Daniel and refuses anyone else.
"""

import json
import time
from pathlib import Path

import httpx
import pytest

from app.config import settings
from app.main import app

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "elevenlabs" / "init.json").read_text())
CONFIG = json.loads((Path(__file__).parents[1] / "config" / "elevenlabs_agent.json").read_text())
AGENT = CONFIG["agent"]["conversation_config"]["agent"]
SECRET = "t" * 32


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setattr(settings, "tools_shared_secret", SECRET)
    monkeypatch.setattr(settings, "allowed_caller_number", "+15555550100")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def init(client, body, secret=SECRET):
    start = time.perf_counter()
    response = await client.post("/tools/init", json=body, headers={"X-Shotgun-Secret": secret})
    return response, time.perf_counter() - start


async def test_daniel_gets_the_normal_greeting(client):
    response, elapsed = await init(client, FIXTURE)
    assert response.status_code == 200
    assert elapsed < 0.5
    data = response.json()
    assert data["type"] == "conversation_initiation_client_data"
    assert data["dynamic_variables"]["caller_allowed"] == "yes"
    assert data["dynamic_variables"]["greeting"] == "Shotgun here. What's up?"


async def test_stranger_is_refused(client):
    response, _ = await init(client, FIXTURE | {"caller_id": "+12025550123"})
    variables = response.json()["dynamic_variables"]
    assert variables["caller_allowed"] == "no"
    assert variables["greeting"] == "Sorry, this line is private. Goodbye."


async def test_every_defined_variable_is_returned(client):
    """ElevenLabs requires the webhook to return all dynamic variables the agent defines."""
    defined = set(AGENT["dynamic_variables"]["dynamic_variable_placeholders"])
    for caller in ("+15555550100", "+12025550123", None):
        response, _ = await init(client, FIXTURE | {"caller_id": caller})
        assert set(response.json()["dynamic_variables"]) == defined


async def test_formatting_differences_still_match(client):
    response, _ = await init(client, FIXTURE | {"caller_id": "+1 (555) 555-0100"})
    assert response.json()["dynamic_variables"]["caller_allowed"] == "yes"


async def test_bad_secret_is_401(client):
    response, _ = await init(client, FIXTURE, secret="wrong")
    assert response.status_code == 401


async def test_unconfigured_allowlist_fails_closed(client, monkeypatch):
    monkeypatch.setattr(settings, "allowed_caller_number", "")
    response, _ = await init(client, FIXTURE)
    assert response.status_code == 503


def test_prompt_hangs_up_on_strangers_first():
    prompt = (Path(__file__).parents[1] / "config" / "elevenlabs_prompt.md").read_text()
    assert prompt.startswith('# Private line\nIf {{caller_allowed}} is "no"')
    assert 'say only "Sorry, this line is private. Goodbye." and call end_call' in prompt
