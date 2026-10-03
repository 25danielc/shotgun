"""Inline voice tools: work done during the call, answered in under 8 s (D17).

Build step 2.2. The webhooks are in app/voice_tools.py (POST /tools/search_web,
/tools/draft_message); this module is the Claude side. Try it by hand:
    uv run python -m app.inline search "Michigan football score"
    uv run python -m app.inline draft Alex "running ten minutes late"

The voice agent says a filler line ("one sec, checking") before calling these, then reads the
answer back on the same call. Background work (anything slower) goes through dispatch_task.

Claude API (claude-api skill, read 2026-10-03, anthropic SDK 1.11):
- Model `claude-haiku-4-5` (settings.inline_model). Haiku 4.5 takes no `output_config.effort`
  (it errors) and runs without thinking when `thinking` is omitted, which is what we want here.
- search_web uses the server tool `web_search_20250305` (the basic variant; Haiku can't use
  20260209). Measured 2026-10-03: Haiku + 20250305 answered a place lookup in 3.7 s. max_uses 2
  keeps it inside the budget. `pause_turn` gets one continuation if time is left.
- Each request runs with the SDK timeout at the budget and max_retries=0, inside an overall
  asyncio.wait_for, so a slow answer becomes a polite "I couldn't get that quickly" well before
  the ElevenLabs tool timeout (about 15 s, step 2.3).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime
from typing import Any

import anthropic

from app.config import settings
from app.workers.research import ResearchError, parse_answer, speakable

log = logging.getLogger(__name__)

BUDGET_SECONDS = 8.0
MAX_SEARCHES = 2
WEB_SEARCH = {"type": "web_search_20250305", "name": "web_search", "max_uses": MAX_SEARCHES}

SEARCH_PROMPT = """You answer one question for Shotgun, an assistant a driver talks to on a phone \
call. Search the web if the answer depends on anything current, then answer. Your answer is read \
aloud while they drive, so:
- One or two short sentences, under 40 words. The answer first, no preamble.
- Plain speech: no URLs, no markdown, no lists, no symbols. Round numbers, times like "10 PM".
- Places: only places that are exactly what they asked for (asked for ramen: ramen shops, not \
other restaurants). Each place once. Use the driver's location; give each place's street and \
closing time if you found it. If you found fewer than they asked for, say how many.
- Never read out the driver's home address or coordinates; say "near you" or "near home".
- If you can't find a reliable answer, say so in one sentence. Never guess opening hours, scores \
or prices."""

DRAFT_PROMPT = """You draft one short message for a driver, who will hear it read aloud and then \
approve or change it. Write only the message text in the driver's voice: no subject line, no \
greeting like "Dear", no sign-off placeholder, no quotes around it. One to three sentences, under \
50 words, friendly and plain. Keep names exactly as given. Don't invent facts, times or places \
the driver didn't mention."""


class InlineError(RuntimeError):
    pass


_shared: anthropic.AsyncAnthropic | None = None


def default_client() -> anthropic.AsyncAnthropic:
    """One client per process, so each call reuses a warm connection."""
    global _shared
    if _shared is None:
        _shared = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    return _shared


def _client(client: anthropic.AsyncAnthropic | None) -> anthropic.AsyncAnthropic:
    return (client or default_client()).with_options(timeout=BUDGET_SECONDS, max_retries=0)


def search_message(query: str, now: datetime, near: dict[str, str] | None = None) -> str:
    """The question plus where the driver is: `near` has optional "location" (plug-in lat,lng)
    and "destination" (what they said), from the drive."""
    near = near or {}
    lines = [f"Current time: {now.astimezone(settings.tz):%A %I:%M %p %Z}"]
    if near.get("location"):
        lines.append(f"Driver's location when the drive started (lat, lng): {near['location']}")
    if near.get("destination"):
        lines.append(f"Driver is heading to: {near['destination']}")
    if settings.home_address:
        lines.append(f"Driver's home: {settings.home_address}")
    lines.append(f"Question: {query}")
    return "\n".join(lines)


async def _search(
    query: str, now: datetime, near: dict[str, str] | None, client: anthropic.AsyncAnthropic
) -> str:
    user = {"role": "user", "content": search_message(query, now, near)}
    messages: list[dict[str, Any]] = [user]
    for _ in range(2):  # the first try, plus one continuation after pause_turn
        response = await client.messages.create(
            model=settings.inline_model,
            max_tokens=1024,
            system=SEARCH_PROMPT,
            tools=[WEB_SEARCH],
            messages=messages,
        )
        if response.stop_reason == "pause_turn":
            messages = [user, {"role": "assistant", "content": response.content}]
            continue
        if response.stop_reason == "refusal":
            raise InlineError("Claude declined the question")
        try:
            return parse_answer(response.content).text
        except ResearchError as exc:
            raise InlineError(str(exc)) from exc
    raise InlineError("search still running after one continuation")


async def search_web(
    query: str,
    *,
    now: datetime | None = None,
    near: dict[str, str] | None = None,
    client: anthropic.AsyncAnthropic | None = None,
    budget: float | None = None,
) -> str:
    """A spoken answer to one question. Raises InlineError (including on timeout)."""
    budget = budget or BUDGET_SECONDS
    try:
        return await asyncio.wait_for(
            _search(query, now or datetime.now(settings.tz), near, _client(client)), budget
        )
    except TimeoutError as exc:
        raise InlineError(f"no answer within {budget:.0f} s") from exc
    except anthropic.APIError as exc:
        raise InlineError(f"Claude request failed: {type(exc).__name__}: {exc}") from exc


async def _draft(to: str, intent: str, client: anthropic.AsyncAnthropic) -> str:
    response = await client.messages.create(
        model=settings.inline_model,
        max_tokens=400,
        system=DRAFT_PROMPT,
        messages=[{"role": "user", "content": f"To: {to}\nWhat to say: {intent}"}],
    )
    if response.stop_reason == "refusal":
        raise InlineError("Claude declined the draft")
    text = speakable("".join(b.text for b in response.content if b.type == "text"))
    if not text:
        raise InlineError("Claude returned no draft")
    return text


async def draft_message(
    to: str,
    intent: str,
    *,
    client: anthropic.AsyncAnthropic | None = None,
    budget: float | None = None,
) -> str:
    """Draft text for the agent to read back. Sends nothing. Raises InlineError."""
    budget = budget or BUDGET_SECONDS
    try:
        return await asyncio.wait_for(_draft(to, intent, _client(client)), budget)
    except TimeoutError as exc:
        raise InlineError(f"no draft within {budget:.0f} s") from exc
    except anthropic.APIError as exc:
        raise InlineError(f"Claude request failed: {type(exc).__name__}: {exc}") from exc


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Step 2.2: try an inline tool.")
    sub = parser.add_subparsers(dest="tool", required=True)
    sub.add_parser("search").add_argument("query")
    draft = sub.add_parser("draft")
    draft.add_argument("to")
    draft.add_argument("intent")
    args = parser.parse_args()
    if args.tool == "search":
        print(asyncio.run(search_web(args.query)))
    else:
        print(asyncio.run(draft_message(args.to, args.intent)))
