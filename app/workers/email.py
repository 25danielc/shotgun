"""Email worker via Composio Gmail tools on a throwaway Gmail account (step 3.2).

Pass check: job -> draft in Gmail; with no pre-approval, needs_approval; approve -> sent.
Sending is irreversible: the draft is created first and only sent after a yes, given up front
(preapproval "send this exact message", which the agent asks for after reading the draft back)
or on the arrival call (approve_action). app/approvals.py decides which.

Flow, each a run_email() tick:
1. claim a queued email job; resolve `details.to` to an address (EMAIL_CONTACTS, or an address
   said in full); create the Gmail draft; store draft_id; settle -> approved | needs_approval.
   Unknown recipient -> failed, "I don't have an email address for Alex." (never guesses).
2. an `approved` email job -> GMAIL_SEND_DRAFT -> done "I sent your email to Alex."

Composio REST (OpenAPI https://backend.composio.dev/api/v3/openapi.json, read 2026-10-03):
    POST /api/v3/tools/execute/{tool_slug}   header x-api-key
         body {user_id: COMPOSIO_USER_ID, arguments: {...}}
         -> {successful, error, data: {response_data: ...}}
    GMAIL_CREATE_EMAIL_DRAFT {recipient_email, subject, body} -> response_data.id (the draft id)
    GMAIL_SEND_DRAFT {draft_id} -> response_data {id, threadId, labelIds}
Shapes checked live the same day with a draft to the account itself, then deleted. The Gmail
account was connected through POST /api/v3/connected_accounts/link (Composio-managed auth config).
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx
from psycopg import AsyncConnection
from psycopg.rows import class_row

from app import approvals, jobs
from app.config import settings
from app.jobs import Job, JobState, JobType

log = logging.getLogger(__name__)

EXECUTE_URL = "https://backend.composio.dev/api/v3/tools/execute/{slug}"
POLL_SECONDS = 3.0
TIMEOUT = 20.0
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ComposioError(RuntimeError):
    pass


class Composio:
    """Minimal Composio tool runner for one user (the throwaway Gmail)."""

    def __init__(self, api_key: str, user_id: str, client: httpx.AsyncClient | None = None):
        self.api_key, self.user_id = api_key, user_id
        self.client = client or httpx.AsyncClient(timeout=TIMEOUT)

    async def run(self, slug: str, arguments: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self.client.post(
                EXECUTE_URL.format(slug=slug),
                json={"user_id": self.user_id, "arguments": arguments},
                headers={"x-api-key": self.api_key},
            )
        except httpx.HTTPError as exc:
            raise ComposioError(f"{slug}: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise ComposioError(f"{slug}: HTTP {response.status_code} {response.text[:160]}")
        data = response.json()
        if not data.get("successful"):
            raise ComposioError(f"{slug}: {str(data.get('error'))[:160]}")
        return (data.get("data") or {}).get("response_data") or {}

    async def aclose(self) -> None:
        await self.client.aclose()


def contacts() -> dict[str, str]:
    """EMAIL_CONTACTS "Alex=alex@example.com; Erica=erica@example.com" -> {"alex": ...}."""
    found = {}
    for pair in re.split(r"[;,\n]", settings.email_contacts):
        name, _, address = pair.partition("=")
        if name.strip() and EMAIL.match(address.strip()):
            found[name.strip().lower()] = address.strip()
    return found


def recipient(to: str) -> str | None:
    """An address for who the driver named: a contact (first name is enough), or an address."""
    to = " ".join((to or "").split())
    spoken = re.sub(r"\s+at\s+", "@", to.lower()).replace(" dot ", ".")
    if EMAIL.match(spoken.replace(" ", "")):
        return spoken.replace(" ", "")
    book = contacts()
    key = to.lower().strip(" .")
    return book.get(key) or book.get(key.split(" ")[0] if key else "")


def subject_for(body: str) -> str:
    words = body.split()
    return " ".join(words[:6]).rstrip(".,!?") + ("…" if len(words) > 6 else "")


def name_of(job: Job) -> str:
    return (job.details.get("to") or "them").strip()


def question_for(job: Job, body: str) -> str:
    return f'Your email to {name_of(job)} is ready: "{body}" Want me to send it?'


async def start_job(conn: AsyncConnection, mail: Composio, job: Job) -> Job:
    """For a claimed job: draft it, then hold or approve by pre-approval."""
    body = (job.details.get("body") or job.request or "").strip()
    address = recipient(job.details.get("to") or "")
    if address is None or not body:
        why = (
            f"I don't have an email address for {name_of(job)}."
            if address is None
            else "There was nothing to send."
        )
        return await jobs.transition(conn, job.id, JobState.FAILED, summary=why, error=why)
    try:
        draft = await mail.run(
            "GMAIL_CREATE_EMAIL_DRAFT",
            {"recipient_email": address, "subject": subject_for(body), "body": body},
        )
    except ComposioError as exc:
        log.error("email job %s: %s", job.id, exc)
        return await jobs.transition(
            conn,
            job.id,
            JobState.FAILED,
            summary=f"I couldn't write your email to {name_of(job)}.",
            error=str(exc),
        )
    job = await jobs.update_result(conn, job.id, {"draft_id": draft.get("id"), "to": name_of(job)})
    log.info("email job %s: drafted for %s", job.id, name_of(job))
    return await approvals.settle(conn, job, question=question_for(job, body))


async def send_approved(conn: AsyncConnection, mail: Composio) -> Job | None:
    """Send the draft of one approved email job (only ever after a yes)."""
    async with conn.transaction():
        cur = conn.cursor(row_factory=class_row(Job))
        await cur.execute(
            """select * from jobs where type = 'email' and state = 'approved'
               order by updated_at, id limit 1 for update skip locked"""
        )
        job = await cur.fetchone()
        if job is None:
            return None
        draft_id = (job.result or {}).get("draft_id")
        try:
            if not draft_id:
                raise ComposioError("no draft recorded")
            sent = await mail.run("GMAIL_SEND_DRAFT", {"draft_id": draft_id})
        except ComposioError as exc:
            log.error("email job %s: send failed: %s", job.id, exc)
            return await jobs.transition(
                conn,
                job.id,
                JobState.FAILED,
                summary=f"I couldn't send your email to {name_of(job)}. It's still a draft.",
                error=str(exc),
            )
        await jobs.update_result(conn, job.id, {"message_id": sent.get("id")})
        return await jobs.transition(
            conn,
            job.id,
            JobState.DONE,
            summary=f"I sent your email to {name_of(job)}.",
            note="sent",
        )


async def tick(conn: AsyncConnection, mail: Composio) -> None:
    job = await jobs.claim_next(conn, [JobType.EMAIL])
    if job is not None:
        await start_job(conn, mail, job)
    await send_approved(conn, mail)


def make_composio() -> Composio:
    return Composio(settings.composio_api_key, settings.composio_user_id)


async def run_email(pool) -> None:
    """Background loop started by the app lifespan."""
    mail = make_composio()
    try:
        while True:
            try:
                async with pool.connection() as conn:
                    await tick(conn, mail)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("email loop error")
            await asyncio.sleep(POLL_SECONDS)
    finally:
        await mail.aclose()
