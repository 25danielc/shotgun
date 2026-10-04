"""Step 3.2 pass check: job -> draft in Gmail, state needs_approval; approve -> sent.

Offline: a fake Composio API (httpx MockTransport) answering like the shapes checked live on
2026-10-03 (`data.response_data.id` is the draft id). Nothing irreversible happens before a yes.
Live (`make test-live T=tests/test_step_3_2_email_worker.py`): one draft to the throwaway
account itself, then deleted. It never sends.
"""

import json

import httpx
import pytest

from app import jobs
from app.config import settings
from app.jobs import JobState, JobType
from app.workers import email
from tests.helpers import DANIEL, TOOLS_SECRET

SEND_YES = {"condition": "send this exact message"}
BODY = "Hey Alex, I'll be there in five minutes."


@pytest.fixture(autouse=True)
def book(monkeypatch):
    monkeypatch.setattr(
        settings, "email_contacts", "Alex=alex@example.com; Erica = erica@example.org"
    )


class FakeComposio:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail or {}

    def handler(self, request):
        slug = request.url.path.rsplit("/", 1)[-1]
        body = json.loads(request.content)
        self.calls.append((slug, body["arguments"]))
        assert request.headers["x-api-key"] == "test-composio"
        assert body["user_id"] == "shotgun-demo"
        if slug in self.fail:
            return httpx.Response(200, json={"successful": False, "error": self.fail[slug]})
        data = {
            "GMAIL_CREATE_EMAIL_DRAFT": {"id": "r123", "message": {"id": "m1"}},
            "GMAIL_SEND_DRAFT": {"id": "m1", "threadId": "t1", "labelIds": ["SENT"]},
        }[slug]
        return httpx.Response(200, json={"successful": True, "data": {"response_data": data}})

    def client(self):
        transport = httpx.MockTransport(self.handler)
        return email.Composio(
            "test-composio", "shotgun-demo", httpx.AsyncClient(transport=transport)
        )

    def slugs(self):
        return [slug for slug, _ in self.calls]


@pytest.fixture
def fake():
    return FakeComposio()


async def email_job(db, to="Alex", body=BODY, preapproval=None):
    await jobs.create_job(
        db,
        JobType.EMAIL,
        {"to": to, "body": body, "label": f"Email {to}"},
        request=body,
        preapproval=preapproval,
    )
    return await jobs.claim_next(db, [JobType.EMAIL])


# --- who it goes to ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("said", "address"),
    [
        ("Alex", "alex@example.com"),
        ("alex", "alex@example.com"),
        ("Alex Chen", "alex@example.com"),  # first name is enough
        ("Erica.", "erica@example.org"),
        ("sam at gmail dot com", "sam@gmail.com"),
        ("sam@gmail.com", "sam@gmail.com"),
        ("Bob", None),  # never guesses
        ("", None),
    ],
)
def test_recipient(said, address):
    assert email.recipient(said) == address


# --- the pass check ---------------------------------------------------------------------------


async def test_draft_is_held_for_a_yes_then_sent(db, fake):
    mail = fake.client()
    job = await email.start_job(db, mail, await email_job(db))
    assert job.state is JobState.NEEDS_APPROVAL
    assert job.summary == f'Your email to Alex is ready: "{BODY}" Want me to send it?'
    assert fake.calls == [
        (
            "GMAIL_CREATE_EMAIL_DRAFT",
            {
                "recipient_email": "alex@example.com",
                "subject": "Hey Alex, I'll be there in…",
                "body": BODY,
            },
        )
    ]
    assert job.result["draft_id"] == "r123"
    assert await email.send_approved(db, mail) is None  # nothing sent before a yes

    await jobs.transition(db, job.id, JobState.APPROVED, expect=JobState.NEEDS_APPROVAL)
    sent = await email.send_approved(db, mail)
    assert (sent.state, sent.summary) == (JobState.DONE, "I sent your email to Alex.")
    assert fake.calls[-1] == ("GMAIL_SEND_DRAFT", {"draft_id": "r123"})


async def test_pre_approved_email_sends_with_no_question(db, fake):
    mail = fake.client()
    job = await email.start_job(db, mail, await email_job(db, preapproval=SEND_YES))
    assert job.state is JobState.APPROVED
    done = await email.send_approved(db, mail)
    assert done.state is JobState.DONE
    assert fake.slugs() == ["GMAIL_CREATE_EMAIL_DRAFT", "GMAIL_SEND_DRAFT"]
    states = [to for _, to, _ in await jobs.events(db, job.id)]
    assert "needs_approval" not in states


async def test_unknown_recipient_fails_without_touching_gmail(db, fake):
    job = await email.start_job(db, fake.client(), await email_job(db, to="Bob"))
    assert (job.state, job.summary) == (JobState.FAILED, "I don't have an email address for Bob.")
    assert fake.calls == []


async def test_draft_failure_fails_the_job(db):
    fake = FakeComposio(fail={"GMAIL_CREATE_EMAIL_DRAFT": "quota"})
    job = await email.start_job(db, fake.client(), await email_job(db))
    assert (job.state, job.summary) == (JobState.FAILED, "I couldn't write your email to Alex.")


async def test_send_failure_leaves_the_draft(db):
    fake = FakeComposio(fail={"GMAIL_SEND_DRAFT": "rate limited"})
    mail = fake.client()
    await email.start_job(db, mail, await email_job(db, preapproval=SEND_YES))
    failed = await email.send_approved(db, mail)
    assert (failed.state, failed.summary) == (
        JobState.FAILED,
        "I couldn't send your email to Alex. It's still a draft.",
    )


async def test_tick_drafts_and_sends_in_one_pass(db, fake):
    await jobs.create_job(
        db,
        JobType.EMAIL,
        {"to": "Erica", "body": "On my way."},
        request="On my way.",
        preapproval=SEND_YES,
    )
    await email.tick(db, fake.client())
    assert fake.slugs() == ["GMAIL_CREATE_EMAIL_DRAFT", "GMAIL_SEND_DRAFT"]
    assert fake.calls[0][1]["recipient_email"] == "erica@example.org"


async def test_voice_dispatch_stores_who_and_the_exact_text(http, db):
    body = {
        "type": "email",
        "to": "Alex",
        "details": BODY,
        "label": "Email Alex",
        "preapproval": SEND_YES,
        "caller": DANIEL,
        "called": "+15555550199",
    }
    reply = (
        await http.post(
            "/tools/dispatch_task", json=body, headers={"X-Shotgun-Secret": TOOLS_SECRET}
        )
    ).json()
    job = await jobs.get_job(db, reply["job_id"])
    assert (job.type, job.details["to"], job.details["body"]) == (JobType.EMAIL, "Alex", BODY)
    assert job.preapproval == SEND_YES


# --- live: one draft to the account itself, deleted; never sends ------------------------------


@pytest.mark.live
async def test_live_draft_to_self_then_delete(monkeypatch):
    monkeypatch.undo()  # the real EMAIL_CONTACTS doesn't matter here; settings from .env
    mail = email.make_composio()
    try:
        me = (await mail.run("GMAIL_GET_PROFILE", {})).get("emailAddress")
        assert me and "@" in me
        draft = await mail.run(
            "GMAIL_CREATE_EMAIL_DRAFT",
            {"recipient_email": me, "subject": "Shotgun live check", "body": "Delete me."},
        )
        assert draft.get("id")
        await mail.run("GMAIL_DELETE_DRAFT", {"draft_id": draft["id"]})
    finally:
        await mail.aclose()
