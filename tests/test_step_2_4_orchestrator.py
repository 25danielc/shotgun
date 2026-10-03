"""Step 2.4 pass check: 5 fixture utterances produce the expected job types and deadlines.

The pass check itself calls Claude (live, `make test-live`). Offline tests use a fake client to
check the request we send, how replies become jobs, and the failure paths.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import jobs, orchestrator
from app.config import settings
from app.jobs import JobState, JobType
from app.orchestrator import PlanError

NOW = datetime(2026, 10, 3, 18, 0, tzinfo=UTC)
UTTERANCES = json.loads(
    (Path(__file__).parent / "fixtures" / "planner_utterances.json").read_text()
)
THREE_PART = UTTERANCES[0]["request"]


def tool_use(name, **args):
    return SimpleNamespace(type="tool_use", name=name, input=args, id=f"toolu_{name}")


def reply(*blocks, stop_reason="tool_use", category=None):
    details = SimpleNamespace(category=category) if category else None
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason, stop_details=details)


class FakeClient:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.beta = SimpleNamespace(messages=self)

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


THREE_JOBS = reply(
    SimpleNamespace(type="thinking", thinking=""),
    tool_use(
        "create_food_job",
        label="Ramen for when you're home",
        order="my usual ramen",
        restaurant=None,
        deadline_minutes=31,
    ),
    tool_use(
        "create_email_job",
        label="Email Alex to come over",
        to="Alex",
        subject="Come over?",
        body="Want to come over tonight?",
        deadline_minutes=None,
    ),
    tool_use(
        "create_coder_job",
        label="Fix Sarah's login bug",
        title="Login bug Sarah filed",
        description="Fix the login bug Sarah filed",
        deadline_minutes=None,
    ),
)


# --- the request we send ----------------------------------------------------------------------


async def test_request_follows_sonnet_5_5_rules():
    client = FakeClient(THREE_JOBS)
    await orchestrator.plan_request(THREE_PART, now=NOW, eta_minutes=31, client=client)
    [call] = client.calls
    assert call["model"] == settings.orchestrator_model == "claude-sonnet-5-5"
    assert "tool_choice" not in call  # forced tool_choice is a 400 on Sonnet 5.5
    assert "thinking" not in call  # {"type": "disabled"} is a 400; adaptive is the default
    assert call["output_config"] == {"effort": "low"}
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["fallbacks"] == "default"
    assert "Drive time left: 31 minutes" in call["messages"][0]["content"]
    assert THREE_PART in call["messages"][0]["content"]


def test_every_tool_is_strict_and_fully_required():
    for tool in orchestrator.TOOLS:
        schema = tool["input_schema"]
        assert tool["strict"] is True
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
    assert set(orchestrator.TOOL_TYPES.values()) == jobs.WORKER_TYPES


# --- replies become plans -------------------------------------------------------------------------


async def test_three_part_reply_becomes_three_jobs():
    plan = await orchestrator.plan_request(
        THREE_PART, now=NOW, eta_minutes=31, client=FakeClient(THREE_JOBS)
    )
    assert [(j.type, j.deadline_minutes) for j in plan.jobs] == [
        (JobType.FOOD, 31),
        (JobType.EMAIL, None),
        (JobType.CODER, None),
    ]
    assert plan.jobs[1].details == {
        "to": "Alex",
        "subject": "Come over?",
        "body": "Want to come over tonight?",
    }
    assert plan.unsupported == []


async def test_unsupported_parts_are_reported_not_invented():
    client = FakeClient(
        reply(
            tool_use(
                "report_unsupported",
                part="turn on the heated seats",
                reason="I can't control the car.",
            )
        )
    )
    plan = await orchestrator.plan_request("turn on the heated seats", now=NOW, client=client)
    assert plan.jobs == []
    assert plan.unsupported == [
        {"part": "turn on the heated seats", "reason": "I can't control the car."}
    ]


async def test_no_tool_call_gets_one_reprompt():
    text_only = reply(SimpleNamespace(type="text", text="Sure!"), stop_reason="end_turn")
    client = FakeClient(text_only, THREE_JOBS)
    plan = await orchestrator.plan_request(THREE_PART, now=NOW, client=client)
    assert len(plan.jobs) == 3
    second = client.calls[1]["messages"]
    assert second[1] == {"role": "assistant", "content": text_only.content}
    assert second[2]["role"] == "user"


async def test_no_tool_call_twice_fails():
    text_only = reply(SimpleNamespace(type="text", text="Sure!"), stop_reason="end_turn")
    with pytest.raises(PlanError, match="no jobs"):
        await orchestrator.plan_request(
            THREE_PART, now=NOW, client=FakeClient(text_only, text_only)
        )


async def test_refusal_fails():
    refused = reply(stop_reason="refusal", category="general_harms")
    with pytest.raises(PlanError, match="declined"):
        await orchestrator.plan_request("x", now=NOW, client=FakeClient(refused))


async def test_api_error_fails():
    import anthropic

    error = anthropic.APIError.__new__(anthropic.APIError)
    Exception.__init__(error, "overloaded")
    with pytest.raises(PlanError, match="request failed"):
        await orchestrator.plan_request("x", now=NOW, client=FakeClient(error))


def test_negative_deadline_means_asap():
    plan = orchestrator.parse_plan(
        [
            tool_use(
                "create_research_job",
                label="Ramen nearby",
                query="ramen",
                near="destination",
                count=3,
                deadline_minutes=-5,
            )
        ]
    )
    assert plan.jobs[0].deadline_minutes is None


# --- plan job -> worker jobs in the table ---------------------------------------------------------


async def test_plan_job_creates_worker_jobs_with_deadlines(db):
    plan_job = await jobs.create_job(db, JobType.PLAN, request=THREE_PART)
    plan_job = await jobs.claim_next(db, [JobType.PLAN])
    created = await orchestrator.process_plan_job(
        db, plan_job, now=NOW, eta_minutes=31, client=FakeClient(THREE_JOBS)
    )

    assert [(j.type, j.state, j.deadline) for j in created] == [
        (JobType.FOOD, JobState.QUEUED, NOW + timedelta(minutes=31)),
        (JobType.EMAIL, JobState.QUEUED, None),
        (JobType.CODER, JobState.QUEUED, None),
    ]
    assert created[2].request == "Fix Sarah's login bug"
    assert created[2].details["plan_id"] == plan_job.id
    done = await jobs.get_job(db, plan_job.id)
    assert done.state is JobState.DONE
    assert done.result == {"job_ids": [j.id for j in created], "unsupported": []}
    assert done.summary == (
        "On it: Ramen for when you're home, Email Alex to come over, Fix Sarah's login bug."
    )


async def test_worker_jobs_inherit_the_drive_and_preapproval(db):
    """D17: a plan dispatched with a yes up front passes both on to every part."""
    from app import drives

    drive = await drives.open_drive(db)
    yes = {"condition": "merge it if the tests pass", "require_tests_pass": True}
    await jobs.create_job(db, JobType.PLAN, request=THREE_PART, drive_id=drive.id, preapproval=yes)
    plan_job = await jobs.claim_next(db, [JobType.PLAN])
    created = await orchestrator.process_plan_job(
        db, plan_job, now=NOW, client=FakeClient(THREE_JOBS)
    )
    assert {(j.drive_id, json.dumps(j.preapproval)) for j in created} == {
        (drive.id, json.dumps(yes))
    }


async def test_failed_plan_marks_plan_job_failed(db):
    await jobs.create_job(db, JobType.PLAN, request="x")
    plan_job = await jobs.claim_next(db, [JobType.PLAN])
    refused = reply(stop_reason="refusal")
    assert (
        await orchestrator.process_plan_job(db, plan_job, now=NOW, client=FakeClient(refused)) == []
    )
    failed = await jobs.get_job(db, plan_job.id)
    assert failed.state is JobState.FAILED
    assert failed.summary == "I couldn't work out that request."
    assert "declined" in failed.error


# --- the pass check (live) ------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.parametrize("case", UTTERANCES, ids=[c["request"][:40] for c in UTTERANCES])
async def test_fixture_utterances_produce_expected_jobs(case):
    plan = await orchestrator.plan_request(
        case["request"], now=datetime.now(UTC), eta_minutes=case["eta_minutes"]
    )
    got = sorted((j.type.value, j.deadline_minutes) for j in plan.jobs)
    want = sorted((e["type"], e["deadline_minutes"]) for e in case["expected"])
    print(case["request"], "->", got, plan.unsupported)
    assert got == want
    assert len(plan.unsupported) == case.get("unsupported", 0)
