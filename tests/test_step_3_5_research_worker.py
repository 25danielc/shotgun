"""Step 3.5: research worker via Claude web search (D15).

Pass check (live): job -> 3 open restaurants near the destination with hours. Offline: a fake
Anthropic client returning the server-tool block shapes (server_tool_use,
web_search_tool_result, text with citations).
"""

from datetime import datetime
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from app import jobs, orchestrator
from app.config import settings
from app.jobs import JobState, JobType
from app.workers import research

NOW = datetime(2026, 10, 3, 18, 30, tzinfo=settings.tz)
ANSWER = "Tomukun on East Liberty is open until 10 PM, and Slurping Turtle until 9 PM."


def block(kind, **fields):
    return SimpleNamespace(type=kind, **fields)


def searched(*texts, stop_reason="end_turn"):
    content = [
        block("text", text="Let me search.", citations=None),
        block("server_tool_use", id="srvtoolu_1", name="web_search", input={"query": "ramen"}),
        block(
            "web_search_tool_result",
            tool_use_id="srvtoolu_1",
            content=[SimpleNamespace(type="web_search_result", url="https://found.example/1")],
        ),
        block("code_execution_tool_result", tool_use_id="srvtoolu_2", content=None),
        *texts,
    ]
    return SimpleNamespace(content=content, stop_reason=stop_reason)


def cited(text, *urls):
    return block("text", text=text, citations=[SimpleNamespace(url=u) for u in urls] or None)


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
        self.messages = self

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        reply = self.responses.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture(autouse=True)
def home(monkeypatch):
    monkeypatch.setattr(settings, "home_address", "500 Main St, Ann Arbor, MI")


async def research_job(db, **details):
    details = {"label": "Find ramen near home", "query": "ramen open now", **details}
    await jobs.create_job(db, JobType.RESEARCH, details, request="find ramen near home")
    return await jobs.claim_next(db, [JobType.RESEARCH])


# --- the request ------------------------------------------------------------------------------


async def test_request_uses_web_search_and_the_destination(db):
    job = await research_job(db, near="destination", count=3)
    client = FakeClient(searched(cited(ANSWER)))
    await research.research(job, now=NOW, client=client)
    [request] = client.requests
    assert request["model"] == "claude-sonnet-5-5"
    assert request["tools"] == [
        {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}
    ]
    prompt = request["messages"][0]["content"]
    assert "Saturday 06:30 PM EDT" in prompt
    assert "Search near: 500 Main St, Ann Arbor, MI" in prompt
    assert "Lookup: ramen open now" in prompt
    assert "How many: 3" in prompt


async def test_current_location_is_described_as_on_the_way(db):
    job = await research_job(db, near="current_location")
    assert "current location, on the way to 500 Main St" in research.request_for(job, NOW)


# --- the answer -------------------------------------------------------------------------------


def test_answer_is_the_text_after_the_last_search_with_sources():
    reply = searched(
        cited("Tomukun on East Liberty is open until 10 PM, ", "https://a.example/tomukun"),
        cited("and Slurping Turtle until 9 PM.", "https://b.example/turtle"),
    )
    answer = research.parse_answer(reply.content)
    assert answer.text == ANSWER
    assert answer.sources == ["https://a.example/tomukun", "https://b.example/turtle"]


def test_without_citations_sources_come_from_the_search_results():
    answer = research.parse_answer(searched(cited(ANSWER)).content)
    assert answer.sources == ["https://found.example/1"]


def test_answer_is_made_speakable():
    raw = "**Tomukun** is open until 10 PM (see https://x.example/menu).\n\n# Done"
    answer = research.parse_answer(searched(cited(raw)).content)
    assert answer.text == "Tomukun is open until 10 PM. Done"


def test_long_answer_is_cut_at_a_sentence():
    long = "First sentence is short. " + "word " * 100
    answer = research.parse_answer(searched(cited(long)).content)
    assert answer.text == "First sentence is short."


def test_no_text_is_an_error():
    with pytest.raises(research.ResearchError):
        research.parse_answer(searched().content)


async def test_pause_turn_resumes_without_an_extra_message(db):
    job = await research_job(db)
    paused = searched(stop_reason="pause_turn")
    client = FakeClient(paused, searched(cited(ANSWER)))
    answer = await research.research(job, now=NOW, client=client)
    assert answer.text == ANSWER
    second = client.requests[1]["messages"]
    assert [m["role"] for m in second] == ["user", "assistant"]
    assert second[1]["content"] is paused.content


async def test_endless_pause_gives_up(db):
    job = await research_job(db)
    client = FakeClient(*[searched(stop_reason="pause_turn")] * (research.MAX_CONTINUATIONS + 1))
    with pytest.raises(research.ResearchError, match="continuations"):
        await research.research(job, now=NOW, client=client)


# --- job states and the callback summary ------------------------------------------------------


async def test_job_finishes_done_with_a_spoken_summary(db):
    job = await research_job(db)
    answer = await research.research(job, now=NOW, client=FakeClient(searched(cited(ANSWER))))
    done = await research.finish(db, job, answer)
    assert (done.state, done.summary) == (JobState.DONE, ANSWER)
    assert done.result == {"answer": ANSWER, "sources": ["https://found.example/1"]}


async def test_api_error_fails_the_job_with_a_spoken_reason(db):
    job = await research_job(db)
    error = anthropic.APIConnectionError(request=httpx.Request("POST", "https://x"))
    with pytest.raises(research.ResearchError) as caught:
        await research.research(job, now=NOW, client=FakeClient(error))
    failed = await research.finish(db, job, caught.value)
    assert failed.state is JobState.FAILED
    assert failed.summary == "I couldn't look up Find ramen near home."


async def test_run_one_records_the_outcome(db, monkeypatch):
    job = await research_job(db)

    class OnePool:
        def connection(self):
            return _Conn()

    class _Conn:
        async def __aenter__(self):
            return db

        async def __aexit__(self, *exc):
            return False

    await research.run_one(OnePool(), job, FakeClient(searched(cited(ANSWER))))
    assert (await jobs.get_job(db, job.id)).state is JobState.DONE


def test_planner_research_tool_covers_general_lookups():
    tool = next(t for t in orchestrator.TOOLS if t["name"] == "create_research_job")
    assert "quick facts" in tool["description"]
    assert "quick facts" in orchestrator.SYSTEM_PROMPT


def test_planner_clock_is_local_not_server_utc():
    utc_noon = datetime.fromisoformat("2026-10-03T16:00:00+00:00")
    assert "Saturday 12:00 EDT" in orchestrator.user_message("x", utc_noon, None)


# --- the pass check (live) --------------------------------------------------------------------


@pytest.mark.live
async def test_live_three_open_restaurants_near_the_destination(db):
    job = await research_job(db, query="ramen restaurants open now", count=3)
    answer = await research.research(job)
    print(answer.text, answer.sources)
    assert answer.text
    assert len(answer.text) <= research.MAX_SPOKEN_CHARS
    assert "http" not in answer.text
    assert answer.sources, "expected cited sources"
