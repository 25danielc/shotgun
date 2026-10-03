"""Step 1.5: /events triggers an outbound call, with a shared-secret check.

Pass check (live, Daniel confirms): curl /events rings the phone; wrong secret returns 401.
Offline: auth rules, the background call, and no call on disconnect.
"""

import os

import httpx
import pytest
from fastapi.testclient import TestClient

from app import events, telephony
from app.config import settings
from app.main import app

SECRET = "s" * 32
CONNECTED = {
    "source": "ios_shortcut",
    "event": "carplay_connected",
    "location": {"lat": 42.28, "lng": -83.74},
}


@pytest.fixture
def calls(monkeypatch):
    monkeypatch.setattr(settings, "events_shared_secret", SECRET)
    placed = []

    async def fake_place_call(variables, **kwargs):
        placed.append(variables)
        return telephony.PlacedCall(conversation_id="c1", call_sid="CA1")

    monkeypatch.setattr(telephony, "place_call", fake_place_call)
    return placed


client = TestClient(app)


def post(body, secret=SECRET):
    headers = {"X-Shotgun-Secret": secret} if secret is not None else {}
    return client.post("/events", json=body, headers=headers)


def test_connected_rings_the_phone(calls):
    response = post(CONNECTED)
    assert response.status_code == 202
    assert response.json() == {"accepted": True, "calling": True}
    assert len(calls) == 1
    assert calls[0]["greeting"]


@pytest.mark.parametrize("secret", ["wrong", None, ""])
def test_wrong_or_missing_secret_is_401_and_no_call(calls, secret):
    assert post(CONNECTED, secret=secret).status_code == 401
    assert calls == []


def test_wrong_secret_with_empty_body_is_still_401(calls):
    response = client.post("/events", content=b"{}", headers={"X-Shotgun-Secret": "wrong"})
    assert response.status_code == 401


def test_unconfigured_secret_fails_closed(calls, monkeypatch):
    monkeypatch.setattr(settings, "events_shared_secret", "")
    assert post(CONNECTED, secret="").status_code == 503
    assert calls == []


def test_disconnect_does_not_call(calls):
    body = {"source": "ios_shortcut", "event": "carplay_disconnected", "location": None}
    response = post(body)
    assert response.status_code == 202
    assert response.json()["calling"] is False
    assert calls == []


def test_location_is_optional_and_strings_are_coerced(calls):
    body = {
        "source": "ios_shortcut",
        "event": "carplay_connected",
        "location": {"lat": "42.28", "lng": "-83.74"},
    }
    assert post(body).status_code == 202
    assert post({"source": "ios_shortcut", "event": "carplay_connected"}).status_code == 202


def test_bad_body_with_good_secret_is_422(calls):
    assert post({"event": "carplay_connected"}).status_code == 422
    assert calls == []


async def test_call_failure_is_logged_not_raised(monkeypatch, caplog):
    async def failing(variables, **kwargs):
        raise telephony.CallError("missing ELEVENLABS_AGENT_ID")

    monkeypatch.setattr(telephony, "place_call", failing)
    await events.ring_on_plug_in(events.Event(source="test", event="carplay_connected"))
    assert "plug-in call failed" in caplog.text


@pytest.mark.live
def test_live_events_rings_phone_and_rejects_wrong_secret():
    """Against the deployed URL. Daniel confirms the phone rang."""
    base = (os.environ.get("BASE_URL") or settings.public_base_url).rstrip("/")
    wrong = httpx.post(f"{base}/events", json=CONNECTED, headers={"X-Shotgun-Secret": "wrong"})
    assert wrong.status_code == 401
    ok = httpx.post(
        f"{base}/events",
        json=CONNECTED,
        headers={"X-Shotgun-Secret": settings.events_shared_secret},
    )
    assert ok.status_code == 202
