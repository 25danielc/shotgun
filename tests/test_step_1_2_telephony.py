"""Step 1.2: outbound call through the ElevenLabs API.

Pass check (live, Daniel confirms): one call makes the phone ring within 5 s.
Offline: the request matches the documented API shape and failures raise CallError.
"""

import json
import time
from pathlib import Path

import httpx
import pytest

from app import telephony
from app.config import settings
from app.telephony import CallError


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "elevenlabs_api_key", "test-key")
    monkeypatch.setattr(settings, "elevenlabs_agent_id", "agent_1")
    monkeypatch.setattr(settings, "elevenlabs_phone_number_id", "phnum_1")
    monkeypatch.setattr(settings, "my_phone_number", "+17345550100")


def mock_client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_request_shape(configured):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["key"] = request.headers["xi-api-key"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"success": True, "conversation_id": "c1", "callSid": "CA1"}
        )

    variables = telephony.call_variables(
        "Ramen is $21.40. Confirm?", eta_minutes=31, pending_job_id=7
    )
    placed = await telephony.place_call(variables, client=mock_client(handler))

    assert placed == telephony.PlacedCall(conversation_id="c1", call_sid="CA1")
    assert seen["url"] == "https://api.elevenlabs.io/v1/convai/twilio/outbound-call"
    assert seen["key"] == "test-key"
    assert seen["body"] == {
        "agent_id": "agent_1",
        "agent_phone_number_id": "phnum_1",
        "to_number": "+17345550100",
        "conversation_initiation_client_data": {
            "dynamic_variables": {
                "greeting": "Ramen is $21.40. Confirm?",
                "summary": "",
                "eta_minutes": "31",
                "pending_job_id": "7",
                "drive_id": "",
                "call_kind": "",
                "caller_allowed": "yes",
            }
        },
    }


def test_call_variables_match_the_agents_dynamic_variables():
    """ElevenLabs needs every variable the agent defines; the config is the source of truth."""
    config = json.loads(
        (Path(__file__).parents[1] / "config" / "elevenlabs_agent.json").read_text()
    )
    agent = config["agent"]["conversation_config"]["agent"]
    defined = agent["dynamic_variables"]["dynamic_variable_placeholders"]
    assert set(telephony.call_variables("hi")) == set(defined)


async def test_missing_config_raises_before_any_request(monkeypatch):
    monkeypatch.setattr(settings, "elevenlabs_agent_id", "")

    def handler(request):
        raise AssertionError("no request should be made")

    with pytest.raises(CallError, match="ELEVENLABS_AGENT_ID"):
        await telephony.place_call(telephony.call_variables("hi"), client=mock_client(handler))


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"detail": "invalid api key"}),
        httpx.Response(200, json={"success": False, "message": "number not verified"}),
    ],
)
async def test_api_failures_raise_call_error(configured, response):
    with pytest.raises(CallError):
        await telephony.place_call(
            telephony.call_variables("hi"), client=mock_client(lambda request: response)
        )


async def test_network_error_raises_call_error(configured):
    def handler(request):
        raise httpx.ConnectError("down")

    with pytest.raises(CallError, match="ConnectError"):
        await telephony.place_call(telephony.call_variables("hi"), client=mock_client(handler))


@pytest.mark.live
async def test_live_outbound_call_rings_phone():
    """Rings MY_PHONE_NUMBER for real. Daniel confirms it rang within 5 s."""
    start = time.monotonic()
    placed = await telephony.place_call(
        telephony.call_variables("Hey, it's Shotgun. Step one point two test call.")
    )
    print(f"API accepted in {time.monotonic() - start:.1f}s, conversation {placed.conversation_id}")
    assert placed.conversation_id
