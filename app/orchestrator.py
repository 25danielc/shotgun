"""Orchestrator: turns a spoken multi-part request into worker jobs with deadlines.

Build step 2.4: 5 fixture utterances produce the expected job types and deadlines.
Deadline scheduling ("order at arrival minus prep time") is step 5.2.

Flow: dispatch_task stores the request as a queued `plan` job. `run_planner()` (started by the
app lifespan when DATABASE_URL and ANTHROPIC_API_KEY are set) claims plan jobs, asks Claude for a
plan, then in one transaction creates the worker jobs and marks the plan job done. Never on the
voice webhook's hot path.

Claude call (claude-api skill, checked 2026-10-03, anthropic SDK 1.11):
- Model `claude-sonnet-5-5` (settings.orchestrator_model) via AsyncAnthropic.
- One strict tool per job type, plus report_unsupported. Sonnet 5.5 rejects forced tool_choice
  ("any"/"tool") with a 400, so tool_choice stays auto, the prompt says to use the tools, and a
  reply with no tool call gets one re-prompt.
- output_config effort "low": short, structured extraction. Adaptive thinking stays on (the
  default; Sonnet 5.5 returns 400 for {"type": "disabled"}).
- Server-side refusal fallback: betas ["server-side-fallback-2026-07-01"] + fallbacks "default".
  A final stop_reason "refusal" fails the plan job.

Deadlines: Claude returns minutes from now (it is given the drive time). We turn that into a
timestamp. A null deadline means "as soon as possible".
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import anthropic
from psycopg import AsyncConnection

from app import jobs
from app.config import settings
from app.jobs import Job, JobState, JobType

log = logging.getLogger(__name__)

MAX_TOKENS = 16000
EFFORT = "low"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
POLL_SECONDS = 1.0

SYSTEM_PROMPT = """You plan errands for Shotgun, an assistant the user talks to on a phone call \
while driving. You receive one spoken request, which may have several parts, plus the current \
time and the drive time left. Turn every part into exactly one job by calling the matching tool. \
Call several tools in one reply when the request has several parts. Do not reply in text.

Job types:
- create_coder_job: fix a bug or change code in the demo app repository.
- create_email_job: send an email. Also use it when the user says "text" or "message" someone: \
there is no SMS, so it becomes an email.
- create_food_job: order food for delivery.
- create_research_job: look up places (restaurants, shops) and report back.
- report_unsupported: anything else (controlling the car, calendar, payments outside food \
orders, reservations, anything you can't map). Never invent a job for it.

Deadlines: set deadline_minutes to minutes from now when the user gives a time. "When I get \
home", "when I arrive" or "there when I get there" means exactly the drive time left. "N \
minutes after I get home" means drive time plus N. A clock time means minutes from the current \
time to it. Otherwise use null, meaning as soon as possible. If the drive time is unknown and \
the user ties a job to arrival, use null.

label is what the user will hear later: under 8 words, plain speech, e.g. "Fix Sarah's login \
bug". Keep names exactly as spoken. Don't add details the user didn't say."""

# Nullable fields use anyOf: strict tool schemas list anyOf and null as supported, not type arrays.
_DEADLINE = {
    "anyOf": [{"type": "integer"}, {"type": "null"}],
    "description": "Minutes from now the job must be finished by, or null for as soon as possible.",
}
_LABEL = {"type": "string", "description": "Under 8 words, spoken back to the user later."}


def _tool(name: str, description: str, properties: dict[str, Any]) -> dict[str, Any]:
    properties = {"label": _LABEL, **properties, "deadline_minutes": _DEADLINE}
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
    }


TOOLS: list[dict[str, Any]] = [
    _tool(
        "create_coder_job",
        "Fix a bug or make a code change in the demo app repository.",
        {
            "title": {"type": "string", "description": "Issue title, e.g. 'Login fails ...'."},
            "description": {"type": "string", "description": "What is wrong, in the user's words."},
        },
    ),
    _tool(
        "create_email_job",
        "Send an email (also used for 'text' or 'message', since there is no SMS).",
        {
            "to": {"type": "string", "description": "Recipient as spoken: a name or address."},
            "subject": {"type": "string"},
            "body": {"type": "string", "description": "Short email body in the user's voice."},
        },
    ),
    _tool(
        "create_food_job",
        "Order food for delivery.",
        {
            "order": {"type": "string", "description": "What to order, as spoken."},
            "restaurant": {
                "anyOf": [{"type": "string"}, {"type": "null"}],
                "description": "Restaurant if named, else null.",
            },
        },
    ),
    _tool(
        "create_research_job",
        "Look up places and report back.",
        {
            "query": {"type": "string", "description": "What to search for, e.g. 'ramen'."},
            "near": {
                "type": "string",
                "enum": ["destination", "current_location"],
                "description": "Where to search. Default destination.",
            },
            "count": {"type": "integer", "description": "How many results. Default 3."},
        },
    ),
    {
        "name": "report_unsupported",
        "description": "A part of the request Shotgun can't do. Never invent a job instead.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "part": {"type": "string", "description": "The part of the request, as spoken."},
                "reason": {"type": "string", "description": "Short reason, plain speech."},
            },
            "required": ["part", "reason"],
            "additionalProperties": False,
        },
    },
]
TOOL_TYPES = {
    "create_coder_job": JobType.CODER,
    "create_email_job": JobType.EMAIL,
    "create_food_job": JobType.FOOD,
    "create_research_job": JobType.RESEARCH,
}


class PlanError(RuntimeError):
    pass


@dataclass(frozen=True)
class PlannedJob:
    type: JobType
    label: str
    details: dict[str, Any]
    deadline_minutes: int | None


@dataclass
class Plan:
    jobs: list[PlannedJob] = field(default_factory=list)
    unsupported: list[dict[str, str]] = field(default_factory=list)


def user_message(request: str, now: datetime, eta_minutes: int | None) -> str:
    drive = f"{eta_minutes} minutes" if eta_minutes is not None else "unknown"
    return (
        f"Current time: {now.astimezone():%A %H:%M %Z}\n"
        f"Drive time left: {drive}\n"
        f"Request: {request}"
    )


def parse_plan(content: list[Any]) -> Plan:
    """Collect tool calls from a response's content blocks into a Plan."""
    plan = Plan()
    for block in content:
        if getattr(block, "type", None) != "tool_use":
            continue
        args = dict(block.input)
        if block.name == "report_unsupported":
            plan.unsupported.append({"part": args["part"], "reason": args["reason"]})
            continue
        job_type = TOOL_TYPES.get(block.name)
        if job_type is None:
            raise PlanError(f"unknown tool {block.name}")
        deadline = args.pop("deadline_minutes")
        if deadline is not None and deadline < 0:
            deadline = None
        label = args.pop("label").strip()
        plan.jobs.append(PlannedJob(job_type, label, args, deadline))
    return plan


async def plan_request(
    request: str,
    *,
    now: datetime,
    eta_minutes: int | None = None,
    client: anthropic.AsyncAnthropic | None = None,
) -> Plan:
    """Ask Claude to split a spoken request into jobs. Raises PlanError."""
    client = client or anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    messages: list[dict[str, Any]] = [
        {"role": "user", "content": user_message(request, now, eta_minutes)}
    ]
    for attempt in range(2):
        try:
            response = await client.beta.messages.create(
                model=settings.orchestrator_model,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
                output_config={"effort": EFFORT},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.APIError as exc:
            raise PlanError(f"Claude request failed: {type(exc).__name__}: {exc}") from exc
        if response.stop_reason == "refusal":
            category = getattr(response.stop_details, "category", None)
            raise PlanError(f"Claude declined the request (category {category})")
        plan = parse_plan(response.content)
        if plan.jobs or plan.unsupported:
            return plan
        if attempt == 0:  # auto tool_choice gave no tool call: re-prompt once
            messages += [
                {"role": "assistant", "content": response.content},
                {"role": "user", "content": "Use the tools to create the jobs."},
            ]
    raise PlanError("Claude returned no jobs")


def spoken_summary(plan: Plan) -> str:
    labels = [job.label for job in plan.jobs]
    parts = [f"On it: {', '.join(labels)}." if labels else "I couldn't make any jobs from that."]
    parts += [f"I can't {item['part']}: {item['reason']}" for item in plan.unsupported]
    return " ".join(parts)


async def process_plan_job(
    conn: AsyncConnection,
    plan_job: Job,
    *,
    now: datetime | None = None,
    eta_minutes: int | None = None,
    client: anthropic.AsyncAnthropic | None = None,
) -> list[Job]:
    """Plan a claimed (running) plan job: create its worker jobs and mark it done, or failed."""
    now = now or datetime.now(UTC)
    try:
        plan = await plan_request(
            plan_job.request or "", now=now, eta_minutes=eta_minutes, client=client
        )
    except PlanError as exc:
        log.error("plan job %s failed: %s", plan_job.id, exc)
        await jobs.transition(
            conn,
            plan_job.id,
            JobState.FAILED,
            summary="I couldn't work out that request.",
            error=str(exc),
        )
        return []
    created = []
    async with conn.transaction():
        for planned in plan.jobs:
            deadline = (
                now + timedelta(minutes=planned.deadline_minutes)
                if planned.deadline_minutes is not None
                else None
            )
            created.append(
                await jobs.create_job(
                    conn,
                    planned.type,
                    {**planned.details, "label": planned.label, "plan_id": plan_job.id},
                    request=planned.label,
                    source=f"plan:{plan_job.id}",
                    deadline=deadline,
                )
            )
        await jobs.transition(
            conn,
            plan_job.id,
            JobState.DONE,
            summary=spoken_summary(plan),
            result={"job_ids": [job.id for job in created], "unsupported": plan.unsupported},
            note=f"planned {len(created)} job(s)",
        )
    log.info("plan job %s -> jobs %s", plan_job.id, [job.id for job in created])
    return created


async def run_planner(pool, *, client: anthropic.AsyncAnthropic | None = None) -> None:
    """Background loop: claim queued plan jobs and process them, one at a time."""
    client = client or anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    while True:
        try:
            async with pool.connection() as conn:
                plan_job = await jobs.claim_next(conn, [JobType.PLAN])
                if plan_job is not None:
                    # TODO(step 5.2): pass the live ETA once trips are tracked.
                    await process_plan_job(conn, plan_job, client=client)
                    continue
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("planner loop error")
        await asyncio.sleep(POLL_SECONDS)
