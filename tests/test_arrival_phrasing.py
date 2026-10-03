"""The arrival call sounds like a friend, not a screen reader (Daniel 18:43). app/phrasing.py.

Offline: a fake Claude client; every bad answer falls back to the plain wording, and the call is
placed either way. Live (`make test-live T=tests/test_arrival_phrasing.py`): real Haiku on a
realistic drive, printed for a listen-through, checked against the same rules.
"""

import asyncio
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app import calls, drives, jobs, phrasing
from app.config import settings
from app.jobs import JobType
from tests.helpers import TESTS_YES, run

FACTS = [
    phrasing.Fact("done", "I merged the fix for the login bug, and the tests passed."),
    phrasing.Fact("didn't work", "I can't do that one yet: email Alex I'm running late."),
]
QUESTION = "The fix for the signup typo is ready as a pull request. Want me to merge it?"
NICE = (
    "Hey, almost there! The login fix is merged and the tests passed. I couldn't email Alex, "
    "that's not hooked up yet. The signup typo fix is ready, want me to merge it?"
)


class FakeClaude:
    def __init__(self, text="", *, error=None, delay=0.0):
        self.text, self.error, self.delay = text, error, delay
        self.requests = []
        self.messages = self

    def with_options(self, **options):
        return self

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return SimpleNamespace(
            stop_reason="end_turn", content=[SimpleNamespace(type="text", text=self.text)]
        )


async def test_natural_wording_is_used_when_it_passes_the_checks():
    fake = FakeClaude(NICE)
    assert await phrasing.arrival_greeting(FACTS, QUESTION, client=fake) == NICE
    [request] = fake.requests
    assert request["model"] == "claude-haiku-4-5"
    assert "output_config" not in request
    assert request["messages"][0]["content"] == (
        "Facts:\n"
        "- done: I merged the fix for the login bug, and the tests passed.\n"
        "- didn't work: I can't do that one yet: email Alex I'm running late.\n"
        f"Question to ask: {QUESTION}"
    )


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("", "empty"),
        ("Hey! " + "The login fix is merged. " * 30 + "Merge it?", "too long"),
        ("Hey, the login fix is merged and the signup fix is ready.", "no question at the end"),
    ],
)
async def test_bad_wording_falls_back_to_plain(text, why):
    plain = await phrasing.arrival_greeting(FACTS, QUESTION, client=FakeClaude(text))
    assert plain == phrasing.plain_greeting(FACTS, QUESTION), why


async def test_slow_or_failing_claude_falls_back(monkeypatch):
    monkeypatch.setattr(phrasing, "PHRASING_SECONDS", 0.05)
    slow = FakeClaude(NICE, delay=1)
    assert await phrasing.arrival_greeting(FACTS, None, client=slow) == phrasing.plain_greeting(
        FACTS, None
    )
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    down = FakeClaude(error=anthropic.APIConnectionError(request=request))
    assert await phrasing.arrival_greeting(FACTS, None, client=down) == phrasing.plain_greeting(
        FACTS, None
    )


async def test_no_key_and_no_client_is_plain_without_a_request():
    assert settings.anthropic_api_key == ""  # tests/conftest.py keeps Claude offline
    assert await phrasing.arrival_greeting(FACTS, None) == (
        "Hey, almost there. I merged the fix for the login bug, and the tests passed. "
        "I can't do that one yet: email Alex I'm running late."
    )


async def test_the_arrival_call_speaks_the_natural_wording(db, rang, monkeypatch):
    fake = FakeClaude(NICE)
    real = phrasing.arrival_greeting

    async def greeting(facts, question):
        return await real(facts, question, client=fake)

    monkeypatch.setattr(phrasing, "arrival_greeting", greeting)
    drive = await drives.open_drive(db)
    merged = await jobs.create_job(
        db, JobType.CODER, {"label": "Fix the login bug"}, drive_id=drive.id, preapproval=TESTS_YES
    )
    await run(
        db,
        merged.id,
        "running",
        "approved",
        "done",
        summary="I merged the fix for the login bug, and the tests passed.",
    )
    held = await jobs.create_job(
        db, JobType.CODER, {"label": "Fix the signup typo"}, drive_id=drive.id
    )
    await run(db, held.id, "running", "needs_approval", summary=QUESTION)

    assert await calls.tick(db) == "arrival"
    [call] = rang
    assert call["greeting"] == NICE
    assert (
        call["summary"] == f"I merged the fix for the login bug, and the tests passed. {QUESTION}"
    )
    assert call["pending_job_id"] == str(held.id)
    sent = fake.requests[0]["messages"][0]["content"]
    assert "- done: I merged the fix for the login bug" in sent
    assert sent.endswith(f"Question to ask: {QUESTION}")


@pytest.mark.live
async def test_live_arrival_wording_sounds_natural():
    text = await phrasing.arrival_greeting(FACTS, QUESTION)
    print(f"\n{text}\n")
    assert text != phrasing.plain_greeting(FACTS, QUESTION), "fell back to plain"
    assert text.endswith("?")
    assert not any(ch.isdigit() for ch in text)
