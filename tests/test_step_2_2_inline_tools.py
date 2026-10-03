"""Step 2.2 pass check, inline tools (D17): search_web answers 5 sample queries in under 8 s
each (live), and draft_message returns draft text.

Offline: a fake Anthropic client goes through the real app (ASGI), with the shapes the server
web-search tool returns. Live: real Haiku + web search, timed through the same endpoint.
"""

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app import inline
from app.config import settings
from app.main import app

FIXTURES = Path(__file__).parent / "fixtures" / "elevenlabs"
SECRET = "t" * 32
DANIEL = "+15555550100"
INLINE_LIMIT = 8.0
NOW = datetime(2026, 10, 3, 18, 30, tzinfo=settings.tz)
SCORE = "Michigan leads Ohio State 21 to 14 early in the third quarter."


def sample(name: str, **changes) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text()) | changes


def block(kind, **fields):
    return SimpleNamespace(type=kind, **fields)


def answered(text, stop_reason="end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[
            block("server_tool_use", id="srvtoolu_1", name="web_search", input={"query": "x"}),
            block(
                "web_search_tool_result",
                tool_use_id="srvtoolu_1",
                content=[SimpleNamespace(type="web_search_result", url="https://found.example")],
            ),
            block("text", text=text, citations=None),
        ],
    )


def paused():
    return SimpleNamespace(
        stop_reason="pause_turn",
        content=[block("server_tool_use", id="srvtoolu_1", name="web_search", input={})],
    )


def drafted(text):
    return SimpleNamespace(stop_reason="end_turn", content=[block("text", text=text)])


class FakeClient:
    """Stands in for AsyncAnthropic: with_options() and messages.create()."""

    def __init__(self, *responses, delay=0.0):
        self.responses = list(responses)
        self.requests = []
        self.options = []
        self.delay = delay
        self.messages = self

    def with_options(self, **options):
        self.options.append(options)
        return self

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        await asyncio.sleep(self.delay)
        reply = self.responses.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture(autouse=True)
def fake_home(monkeypatch):
    """Never the real HOME_ADDRESS from .env in tests."""
    monkeypatch.setattr(settings, "home_address", "500 Main St, Ann Arbor, MI 48104")


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setattr(settings, "tools_shared_secret", SECRET)
    monkeypatch.setattr(settings, "allowed_caller_number", DANIEL)
    # The shared client's connection pool belongs to one event loop; each test gets a new one.
    monkeypatch.setattr(inline, "_shared", None)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def use_fake(monkeypatch, fake):
    monkeypatch.setattr(inline, "default_client", lambda: fake)
    return fake


async def call(client, tool, body):
    start = time.perf_counter()
    response = await client.post(f"/tools/{tool}", json=body, headers={"X-Shotgun-Secret": SECRET})
    return response, time.perf_counter() - start


# --- search_web -------------------------------------------------------------------------------


async def test_search_web_returns_a_spoken_answer_inline(client, monkeypatch):
    fake = use_fake(monkeypatch, FakeClient(answered(SCORE)))
    response, elapsed = await call(client, "search_web", sample("search_web"))
    assert response.status_code == 200
    assert elapsed < INLINE_LIMIT
    assert response.json()["ok"] is True
    assert response.json()["message"] == SCORE
    [request] = fake.requests
    assert request["model"] == "claude-haiku-4-5"
    assert request["tools"] == [
        {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 2,
            "user_location": {
                "type": "approximate",
                "city": "Ann Arbor",
                "region": "MI",
                "country": "US",
                "timezone": "America/Detroit",
            },
        }
    ]
    assert "output_config" not in request and "thinking" not in request  # Haiku 4.5 rejects effort
    assert "Michigan football game" in request["messages"][0]["content"]
    assert fake.options == [{"timeout": INLINE_LIMIT, "max_retries": 0}]


async def test_search_web_answer_is_made_speakable():
    fake = FakeClient(answered("**Michigan** leads 21-14 (see https://espn.example/game)."))
    assert await inline.search_web("score", now=NOW, client=fake) == "Michigan leads 21-14."


async def test_pause_turn_gets_one_continuation():
    fake = FakeClient(paused(), answered(SCORE))
    assert await inline.search_web("score", now=NOW, client=fake) == SCORE
    first, second = fake.requests
    assert second["messages"][0] == first["messages"][0]
    assert second["messages"][1]["role"] == "assistant"


async def test_slow_search_gives_up_within_the_budget(client, monkeypatch):
    use_fake(monkeypatch, FakeClient(answered(SCORE), delay=5))
    monkeypatch.setattr(inline, "BUDGET_SECONDS", 0.2)
    response, elapsed = await call(client, "search_web", sample("search_web"))
    assert elapsed < 1
    assert response.json() == {
        "ok": False,
        "message": "I couldn't find that quickly.",  # the agent decides what to say, not us
        "job_id": None,
        "drive_id": None,
        "jobs": None,
    }


async def test_api_error_is_a_spoken_fallback_not_a_500(client, monkeypatch):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    use_fake(monkeypatch, FakeClient(anthropic.APIConnectionError(request=request)))
    response, _ = await call(client, "search_web", sample("search_web"))
    assert response.status_code == 200
    assert response.json()["ok"] is False


async def test_blank_query_makes_no_request(client, monkeypatch):
    fake = use_fake(monkeypatch, FakeClient())
    response, _ = await call(client, "search_web", sample("search_web", query="  "))
    assert response.json()["ok"] is False
    assert fake.requests == []


async def test_nearby_uses_the_drives_location_and_destination(http, db, monkeypatch):
    """Daniel 18:30: "find me 3 nearby ramen places" must just search, near him."""
    from app import drives

    drive = await drives.open_drive(db, lat=42.2754, lng=-83.7417)
    await db.execute("update drives set destination = 'home' where id = %s", (drive.id,))
    fake = use_fake(monkeypatch, FakeClient(answered("Tomukun on Liberty is open until 10.")))
    body = sample("search_web", query="find me 3 nearby ramen places", drive_id=str(drive.id))
    response = await http.post(
        "/tools/search_web", json=body, headers={"X-Shotgun-Secret": "t" * 32}
    )
    assert response.json() == {
        "ok": True,
        "message": "Tomukun on Liberty is open until 10.",
        "job_id": None,
        "drive_id": None,
        "jobs": None,
    }
    question = fake.requests[0]["messages"][0]["content"]
    assert "Driver is in or near Ann Arbor, MI, at about (lat, lng) 42.2754, -83.7417" in question
    assert "Driver is heading to: home" in question
    assert "500 Main St" not in question  # the home address pulled answers toward home
    assert question.endswith("Question: find me 3 nearby ramen places")


async def test_search_without_a_drive_still_answers(client, monkeypatch):
    fake = use_fake(monkeypatch, FakeClient(answered(SCORE)))
    response, _ = await call(client, "search_web", sample("search_web", drive_id=""))
    assert response.json()["ok"] is True
    assert "at about (lat, lng)" not in fake.requests[0]["messages"][0]["content"]


@pytest.mark.parametrize(
    ("said", "spoken"),
    [
        ("Tomukun Noodle Bar at 505 East Liberty.", "Tomukun Noodle Bar on East Liberty."),
        ("Comet Coffee at 16 Nickels Arcade", "Comet Coffee on Nickels Arcade"),
        ("Joe's at 300 Main Street", "Joe's on Main Street"),
        ("Michigan lost 20-14 to Minnesota.", "Michigan lost 20-14 to Minnesota."),
        ("It's 44 degrees with 3 Ramen places open", "It's 44 degrees with 3 Ramen places open"),
    ],
)
def test_spoken_answers_never_carry_house_numbers(said, spoken):
    assert inline.without_house_numbers(said) == spoken


# --- draft_message ----------------------------------------------------------------------------


async def test_draft_message_returns_draft_text(client, monkeypatch):
    text = "Hey Alex, come over tonight.\nI'm bringing ramen!"
    fake = use_fake(monkeypatch, FakeClient(drafted(text)))
    response, elapsed = await call(client, "draft_message", sample("draft_message"))
    assert response.status_code == 200
    assert elapsed < INLINE_LIMIT
    assert response.json()["ok"] is True
    assert response.json()["message"] == "Hey Alex, come over tonight. I'm bringing ramen!"
    [request] = fake.requests
    assert request["model"] == "claude-haiku-4-5"
    assert "tools" not in request  # a draft sends nothing and looks nothing up
    assert request["messages"][0]["content"] == (
        "To: Alex\nWhat to say: come over tonight, I'm bringing ramen"
    )


async def test_failed_draft_asks_again(client, monkeypatch):
    use_fake(monkeypatch, FakeClient(drafted("   ")))
    response, _ = await call(client, "draft_message", sample("draft_message"))
    assert response.json() == {
        "ok": False,
        "message": "I couldn't write that one. Can you say it again?",
        "job_id": None,
        "drive_id": None,
        "jobs": None,
    }


# --- live pass check --------------------------------------------------------------------------

LIVE_QUERIES = [
    "What's the score of the Michigan football game?",
    "What's the weather in Ann Arbor tonight?",
    "Is the Costco on Ellsworth Road in Ann Arbor open right now?",
    "Who won the Lions game last week?",
    "What's the price of gas near Ann Arbor?",
]


@pytest.mark.live
@pytest.mark.parametrize("query", LIVE_QUERIES)
async def test_live_search_web_answers_each_sample_in_under_8_s(client, query):
    response, elapsed = await call(client, "search_web", sample("search_web", query=query))
    reply = response.json()
    print(f"{elapsed:.1f}s  ok={reply['ok']}  {reply['message']}")
    assert response.status_code == 200
    assert elapsed < INLINE_LIMIT
    assert reply["ok"] is True
    assert reply["message"]


@pytest.mark.live
async def test_live_draft_message_in_under_8_s(client):
    response, elapsed = await call(client, "draft_message", sample("draft_message"))
    print(f"{elapsed:.1f}s  {response.json()['message']}")
    assert elapsed < INLINE_LIMIT
    assert response.json()["ok"] is True
