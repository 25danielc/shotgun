"""Push config/elevenlabs_agent.json to ElevenLabs: agent, tools, secret and phone number.

Steps 1.1 and 2.3. Idempotent: re-run after any change to the config or the base URL.

    uv run python scripts/apply_agent.py --stage greet   # 1.1: agent + Twilio number, no tools
    uv run python scripts/apply_agent.py --stage full    # 2.3: + webhook tools, caller webhook
    add --dry-run to print the request bodies without calling the API

The prompt text lives in config/elevenlabs_prompt.md (D17) and is inlined into the agent body.

Stage "greet" leaves out tools and the conversation-initiation webhook, because both point at
our server and would break calls before it's deployed. Stage "full" needs PUBLIC_BASE_URL and
TOOLS_SHARED_SECRET, and must not run until /tools/init is built and deployed (step 2.3): with the
webhook enabled and no endpoint behind it, every call to the agent fails.

Endpoints checked against https://api.elevenlabs.io/openapi.json on 2026-10-03:
  POST/PATCH /v1/convai/agents/create, /v1/convai/agents/{id}
  GET/POST /v1/convai/tools, PATCH /v1/convai/tools/{id}
  GET/POST /v1/convai/secrets ({type: "new"}), PATCH /v1/convai/secrets/{id} ({type: "update"})
  GET/POST /v1/convai/phone-numbers, PATCH /v1/convai/phone-numbers/{id} ({agent_id})
Prints IDs to copy into .env. Never prints secret values.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402

API = "https://api.elevenlabs.io/v1/convai"
CONFIG = ROOT / "config" / "elevenlabs_agent.json"
SECRET_PLACEHOLDER = "tools_secret"  # noqa: S105 - a name, not a secret
SECRET_NAME = "shotgun_tools_secret"  # noqa: S105 - a name, not a secret


def load_config(base_url: str) -> dict[str, Any]:
    """The agent config with the base URL filled in and the prompt read from its own file."""
    text = CONFIG.read_text().replace("{{BASE_URL}}", base_url.rstrip("/"))
    config = json.loads(text)
    config.pop("_notes", None)
    prompt = config["agent"]["conversation_config"]["agent"]["prompt"]
    prompt["prompt"] = (CONFIG.parent / prompt.pop("prompt_file")).read_text().strip()
    return config


def with_secret_id(obj: Any, secret_id: str) -> Any:
    """Replace every {"secret_id": "tools_secret"} placeholder with the real workspace secret id."""
    if isinstance(obj, dict):
        if obj == {"secret_id": SECRET_PLACEHOLDER}:
            return {"secret_id": secret_id}
        return {key: with_secret_id(value, secret_id) for key, value in obj.items()}
    if isinstance(obj, list):
        return [with_secret_id(item, secret_id) for item in obj]
    return obj


def agent_body(config: dict[str, Any], stage: str, tool_ids: list[str]) -> dict[str, Any]:
    agent = copy.deepcopy(config["agent"])
    prompt = agent["conversation_config"]["agent"]["prompt"]
    prompt["tool_ids"] = tool_ids
    voice = agent["conversation_config"].get("tts", {}).get("voice_id", "")
    if voice.startswith("TODO"):
        del agent["conversation_config"][
            "tts"
        ]  # keep ElevenLabs' default voice until one is picked
    if stage == "greet":
        platform = agent["platform_settings"]
        platform["overrides"]["enable_conversation_initiation_client_data_from_webhook"] = False
        platform.pop("workspace_overrides", None)
    return agent


class ElevenLabs:
    def __init__(self, api_key: str, dry_run: bool):
        self.dry_run = dry_run
        self.offline = dry_run and not api_key  # dry run without a key: skip lookups too
        self.http = httpx.Client(base_url=API, headers={"xi-api-key": api_key}, timeout=30)

    def call(self, method: str, path: str, body: dict | None = None, *, log_body: bool = True):
        if self.offline and method == "GET":
            return {}
        if self.dry_run and method != "GET":
            shown = json.dumps(body, indent=2) if log_body else "<redacted>"
            print(f"[dry-run] {method} {path}\n{shown}\n")
            return {}
        response = self.http.request(method, path, json=body)
        if response.status_code >= 400:
            sys.exit(f"{method} {path} -> HTTP {response.status_code}: {response.text[:400]}")
        return response.json() if response.content else {}


def upsert_secret(el: ElevenLabs, value: str) -> str:
    secrets = el.call("GET", "/secrets").get("secrets", [])
    existing = next((s for s in secrets if s.get("name") == SECRET_NAME), None)
    if existing:
        el.call(
            "PATCH",
            f"/secrets/{existing['secret_id']}",
            {"type": "update", "name": SECRET_NAME, "value": value},
            log_body=False,
        )
        return existing["secret_id"]
    created = el.call(
        "POST", "/secrets", {"type": "new", "name": SECRET_NAME, "value": value}, log_body=False
    )
    return created.get("secret_id", "<new-secret-id>")


def upsert_tools(el: ElevenLabs, tools: list[dict[str, Any]]) -> list[str]:
    ids = []
    for tool in tools:
        name = tool["tool_config"]["name"]
        found = el.call("GET", f"/tools?search={name}").get("tools", [])
        match = next((t for t in found if t.get("tool_config", {}).get("name") == name), None)
        if match:
            el.call("PATCH", f"/tools/{match['id']}", tool)
            ids.append(match["id"])
            print(f"tool {name}: updated {match['id']}")
        else:
            created = el.call("POST", "/tools", tool)
            ids.append(created.get("id", f"<new-{name}-id>"))
            print(f"tool {name}: created {ids[-1]}")
    return ids


def upsert_agent(el: ElevenLabs, body: dict[str, Any]) -> str:
    if settings.elevenlabs_agent_id:
        el.call("PATCH", f"/agents/{settings.elevenlabs_agent_id}", body)
        print(f"agent: updated {settings.elevenlabs_agent_id}")
        return settings.elevenlabs_agent_id
    agent_id = el.call("POST", "/agents/create", body).get("agent_id", "<new-agent-id>")
    print(f"agent: created. Add to .env and Railway:  ELEVENLABS_AGENT_ID={agent_id}")
    return agent_id


def upsert_phone_number(el: ElevenLabs, agent_id: str) -> None:
    phone_id = settings.elevenlabs_phone_number_id
    if not phone_id and settings.twilio_phone_number:
        numbers = el.call("GET", "/phone-numbers")
        match = next(
            (n for n in numbers if n.get("phone_number") == settings.twilio_phone_number), None
        )
        phone_id = match["phone_number_id"] if match else ""
    if phone_id:
        el.call("PATCH", f"/phone-numbers/{phone_id}", {"agent_id": agent_id})
        print(f"phone number: {phone_id} now answers with the agent")
    else:
        missing = [
            name
            for name in ("TWILIO_PHONE_NUMBER", "TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN")
            if not getattr(settings, name.lower())
        ]
        if missing:
            print(f"phone number: skipped, set {', '.join(missing)} in .env")
            return
        created = el.call(
            "POST",
            "/phone-numbers",
            {
                "provider": "twilio",
                "phone_number": settings.twilio_phone_number,
                "label": "Shotgun",
                "sid": settings.twilio_account_sid,
                "token": settings.twilio_auth_token,
                "agent_id": agent_id,
            },
            log_body=False,
        )
        phone_id = created.get("phone_number_id", "<new-phone-number-id>")
        print(f"phone number: imported. Add to .env:  ELEVENLABS_PHONE_NUMBER_ID={phone_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage", choices=["greet", "full"], required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not settings.elevenlabs_api_key and not args.dry_run:
        sys.exit("ELEVENLABS_API_KEY is not set in .env")
    if args.stage == "full" and not (settings.public_base_url and settings.tools_shared_secret):
        sys.exit("--stage full needs PUBLIC_BASE_URL and TOOLS_SHARED_SECRET in .env")

    config = load_config(settings.public_base_url or "https://example.invalid")
    el = ElevenLabs(settings.elevenlabs_api_key, args.dry_run)
    tool_ids: list[str] = []
    if args.stage == "full":
        secret_id = upsert_secret(el, settings.tools_shared_secret)
        config = with_secret_id(config, secret_id)
        tool_ids = upsert_tools(el, config["tools"])
    agent_id = upsert_agent(el, agent_body(config, args.stage, tool_ids))
    upsert_phone_number(el, agent_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
