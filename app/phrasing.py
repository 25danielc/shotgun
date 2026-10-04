"""Natural wording for the arrival call (D17, step 4.1 polish after Daniel's 18:43 feedback).

The arrival call's first message is spoken verbatim, so stringing job summaries together sounded
like a screen reader ("Merged: Fix the login bug. The tests passed. Sorry, I can't handle this one
yet: ..."). `arrival_greeting()` asks Haiku to say the same facts the way a friend would, then
checks the result; anything off falls back to `plain_greeting()`, so a call never fails or
waits on this.

Rules for the model: only the facts given, no invented details, no street numbers or exact
times, under ~55 words, and when a question is pending it must end by asking it (the agent's
prompt routes a yes/no to approve_action for pending_job_id).

Claude API (claude-api skill, read 2026-10-03): claude-haiku-4-5 (settings.inline_model), no
`output_config.effort` (Haiku rejects it), no thinking. SDK timeout PHRASING_SECONDS with
max_retries=0 inside asyncio.wait_for.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import anthropic

from app.config import settings

log = logging.getLogger(__name__)

PHRASING_SECONDS = 4.0
MAX_CHARS = 420

PROMPT = """You are Shotgun, a friend riding along who handles errands for the driver. You're \
calling to catch them up. The message says where they are in the trip: if it gives minutes \
left, open by telling them, e.g. "Hey, you're about three minutes out." If it doesn't, never \
mention distance, time or arriving. Write exactly what you'll say when they pick up.

- Warm, casual, spoken: contractions, one breath per idea. Start with a quick hello like "Hey!" \
or "Hey, almost there."
- Two to four short sentences, under 55 words. Lead with the good news.
- Say only what's in the facts. Never add details, numbers, names or reasons that aren't there, \
and never promise anything ("I'll do that next", "I'll try again"): if something didn't work, \
say so plainly.
- No street numbers, addresses, exact times, URLs, lists or markdown.
- If there is a question, end with it, as a question, in plain words. Ask only that one.
- Output only the words to say."""


@dataclass(frozen=True)
class Fact:
    status: str  # "done", "didn't work", "still going", "waiting on you"
    text: str


class PhrasingError(RuntimeError):
    pass


def opener(minutes_left: int | None) -> str:
    if minutes_left is None:
        return "Hey, quick update."
    if minutes_left <= 1:
        return "Hey, you're just about there."
    return f"Hey, you're about {minutes_left} minutes out."


def plain_greeting(facts: list[Fact], question: str | None, minutes_left: int | None = None) -> str:
    """The fallback: how far out (only if known), the facts in order, question last."""
    parts = [f.text.strip() for f in facts]
    if question:
        parts.append(question.strip())
    body = " ".join(p for p in parts if p)
    return f"{opener(minutes_left)} {body}".strip()


def facts_message(facts: list[Fact], question: str | None, minutes_left: int | None = None) -> str:
    trip = (
        f"Minutes left in the drive: {minutes_left}"
        if minutes_left is not None
        else "Minutes left in the drive: unknown (don't mention it)"
    )
    lines = [trip]
    lines += ["Facts:"] + [f"- {f.status}: {f.text}" for f in facts] if facts else ["Facts: none"]
    lines.append(f"Question to ask: {question}" if question else "Question to ask: none")
    return "\n".join(lines)


def check(text: str, question: str | None) -> str:
    text = " ".join(text.split()).strip().strip('"')
    if not text:
        raise PhrasingError("empty")
    if len(text) > MAX_CHARS:
        raise PhrasingError(f"too long ({len(text)} chars)")
    if question and not text.endswith("?"):
        raise PhrasingError("the pending question isn't asked at the end")
    return text


async def _ask(facts: list[Fact], question: str | None, minutes_left: int | None, client) -> str:
    response = await client.messages.create(
        model=settings.inline_model,
        max_tokens=300,
        system=PROMPT,
        messages=[{"role": "user", "content": facts_message(facts, question, minutes_left)}],
    )
    if response.stop_reason == "refusal":
        raise PhrasingError("declined")
    return "".join(b.text for b in response.content if b.type == "text")


async def arrival_greeting(
    facts: list[Fact],
    question: str | None,
    minutes_left: int | None = None,
    *,
    client: anthropic.AsyncAnthropic | None = None,
) -> str:
    """Natural wording, or the plain fallback. Never raises."""
    if not settings.anthropic_api_key and client is None:
        return plain_greeting(facts, question, minutes_left)
    from app.inline import default_client

    client = (client or default_client()).with_options(timeout=PHRASING_SECONDS, max_retries=0)
    try:
        text = await asyncio.wait_for(_ask(facts, question, minutes_left, client), PHRASING_SECONDS)
        return check(text, question)
    except (PhrasingError, TimeoutError, anthropic.APIError) as exc:
        log.warning("arrival wording fell back to plain: %s", exc)
        return plain_greeting(facts, question, minutes_left)
