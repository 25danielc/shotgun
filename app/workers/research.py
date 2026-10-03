"""Research worker via Claude web search (stretch, step 3.5; D15: no Places API).

Pass check: job -> 3 open restaurants near the destination with hours.
Read-only, so no approval gate: queued -> running -> done (or failed), and the callback reads
`summary` aloud. Try it by hand:
    uv run python -m app.workers.research "ramen open now"     # prints the spoken answer

Claude API (bundled claude-api skill docs, read 2026-10-03):
- Server tool {"type": "web_search_20250305", "name": "web_search", "max_uses": N} on
  claude-sonnet-5-5. It runs on Anthropic's side, so there is nothing to implement here. Not the
  20260209 version: its dynamic filtering adds code-execution rounds. Measured live 2026-10-03 on
  the same lookup: 13.6 s and 1 place (20260209) vs 3.5 s and 3 places (20250305).
- stop_reason "pause_turn": the server-side loop hit its iteration limit. Re-send the user turn
  plus the paused assistant content, with no extra "continue" message, and it resumes.
- The answer is the text after the last tool block. Its text may carry no citations (seen live
  2026-10-03), so sources fall back to the URLs in the web_search_tool_result blocks. Sources are
  stored in `result`, never read aloud.

Each job runs in its own task, so a 20 s search doesn't hold a pool connection or block others.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import anthropic

from app import jobs
from app.config import settings
from app.jobs import Job, JobState, JobType

log = logging.getLogger(__name__)

POLL_SECONDS = 1.0
MAX_TOKENS = 4000
MAX_SEARCHES = 3
MAX_CONTINUATIONS = 3
MAX_PARALLEL = 3
MAX_SPOKEN_CHARS = 320
WEB_SEARCH = {"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_SEARCHES}

SYSTEM_PROMPT = """You do one lookup for Shotgun, an assistant a driver talks to on a phone call. \
Search the web, then answer. Your answer is read aloud on a call, so:
- At most 2 short sentences, under 45 words.
- Plain speech: no URLs, no markdown, no lists, no symbols. Times like "10 PM", round numbers.
- For places: give up to the requested number that are open now (or at the time asked), each \
with its closing time, near the place the driver asked about.
- If you can't find a reliable answer, say so in one sentence. Never guess opening hours."""


class ResearchError(RuntimeError):
    pass


@dataclass
class Answer:
    text: str
    sources: list[str] = field(default_factory=list)


def request_for(job: Job, now: datetime) -> str:
    details = job.details
    query = details.get("query") or details.get("label") or job.request or ""
    near = details.get("near") or "destination"
    place = settings.home_address or "unknown"
    if near == "current_location":
        place = f"the driver's current location, on the way to {place}"
    lines = [
        f"Current time: {now.astimezone(settings.tz):%A %I:%M %p %Z}",
        f"Driver's destination: {settings.home_address or 'unknown'}",
        f"Search near: {place}",
        f"Lookup: {query}",
    ]
    if details.get("count"):
        lines.append(f"How many: {details['count']}")
    if job.request and job.request != query:
        lines.append(f"What the driver said: {job.request}")
    return "\n".join(lines)


def _speakable(text: str) -> str:
    text = re.sub(r"\s*\([^()]*https?://[^()]*\)", "", text)  # "(see https://...)"
    text = re.sub(r"https?://[^\s)]+", "", text)
    text = re.sub(r"[*_#`>|]", "", text)
    text = " ".join(text.split())
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    if len(text) > MAX_SPOKEN_CHARS:
        cut = text[:MAX_SPOKEN_CHARS]
        end = cut.rfind(". ")
        text = cut[: end + 1] if end > 0 else cut.rsplit(" ", 1)[0] + "."
    return text


def parse_answer(content: list[Any]) -> Answer:
    """Spoken text after the last tool block, plus source URLs (citations, else search results)."""
    last_tool = max((i for i, block in enumerate(content) if block.type != "text"), default=-1)
    final = [block for block in content[last_tool + 1 :] if block.type == "text"]
    cited = [
        getattr(citation, "url", None)
        for block in final
        for citation in getattr(block, "citations", None) or []
    ]
    found = [
        getattr(item, "url", None)
        for block in content
        if block.type == "web_search_tool_result" and isinstance(block.content, list)
        for item in block.content
    ]
    sources = list(dict.fromkeys(url for url in cited or found if url))
    text = _speakable("".join(block.text for block in final))
    if not text:
        raise ResearchError("Claude returned no answer text")
    return Answer(text, sources[:5])


async def research(
    job: Job, *, now: datetime | None = None, client: anthropic.AsyncAnthropic | None = None
) -> Answer:
    """One web-search answer for a research job. Raises ResearchError."""
    client = client or anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    user = {"role": "user", "content": request_for(job, now or datetime.now(settings.tz))}
    messages: list[dict[str, Any]] = [user]
    for _ in range(MAX_CONTINUATIONS + 1):
        try:
            response = await client.messages.create(
                model=settings.orchestrator_model,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                tools=[WEB_SEARCH],
                messages=messages,
                output_config={"effort": "low"},
            )
        except anthropic.APIError as exc:
            raise ResearchError(f"Claude request failed: {type(exc).__name__}: {exc}") from exc
        if response.stop_reason == "pause_turn":
            messages = [user, {"role": "assistant", "content": response.content}]
            continue
        if response.stop_reason == "refusal":
            raise ResearchError("Claude declined the lookup")
        return parse_answer(response.content)
    raise ResearchError(f"still searching after {MAX_CONTINUATIONS} continuations")


def label_of(job: Job) -> str:
    return job.details.get("label") or job.details.get("query") or job.request or "that lookup"


async def finish(conn, job: Job, outcome: Answer | Exception) -> Job:
    if isinstance(outcome, Answer):
        return await jobs.transition(
            conn,
            job.id,
            JobState.DONE,
            summary=outcome.text,
            result={"answer": outcome.text, "sources": outcome.sources},
        )
    return await jobs.transition(
        conn,
        job.id,
        JobState.FAILED,
        summary=f"I couldn't look up {label_of(job)}.",
        error=str(outcome)[:500],
    )


async def run_one(pool, job: Job, client: anthropic.AsyncAnthropic) -> None:
    """Search without holding a connection, then record the outcome."""
    try:
        outcome: Answer | Exception = await research(job, client=client)
    except ResearchError as exc:
        outcome = exc
    async with pool.connection() as conn:
        done = await finish(conn, job, outcome)
    log.info("research job %s %s", job.id, done.state)


async def run_research(pool, *, client: anthropic.AsyncAnthropic | None = None) -> None:
    """Background loop started by the app lifespan: claim, then search in a task."""
    client = client or anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    slots = asyncio.Semaphore(MAX_PARALLEL)
    running: set[asyncio.Task] = set()

    async def guarded(job: Job) -> None:
        try:
            await run_one(pool, job, client)
        except Exception:
            log.exception("research job %s crashed", job.id)
        finally:
            slots.release()

    try:
        while True:
            await slots.acquire()
            try:
                async with pool.connection() as conn:
                    job = await jobs.claim_next(conn, [JobType.RESEARCH])
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("research loop error")
                job = None
            if job is None:
                slots.release()
                await asyncio.sleep(POLL_SECONDS)
                continue
            task = asyncio.create_task(guarded(job), name=f"research-{job.id}")
            running.add(task)
            task.add_done_callback(running.discard)
    finally:
        for task in running:
            task.cancel()


async def _demo(query: str) -> None:
    now = datetime.now(settings.tz)
    job = Job(
        id=0,
        type=JobType.RESEARCH,
        state=JobState.RUNNING,
        request=query,
        details={"query": query, "near": "destination", "count": 3},
        summary=None,
        result=None,
        error=None,
        deadline=None,
        source="demo",
        created_at=now,
        updated_at=now,
    )
    answer = await research(job)
    print(answer.text)
    for url in answer.sources:
        print("  source:", url)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Step 3.5: one research lookup, printed.")
    parser.add_argument("query")
    asyncio.run(_demo(parser.parse_args().query))
