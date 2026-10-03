"""Offline checks for config/elevenlabs_agent.json and scripts/apply_agent.py (steps 1.1, 2.3)."""

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("apply_agent", ROOT / "scripts" / "apply_agent.py")
apply_agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(apply_agent)

BASE = "https://shotgun.example.com"


def test_base_url_substituted_and_dynamic_variables_kept():
    config = apply_agent.load_config(BASE + "/")
    urls = [t["tool_config"]["api_schema"]["url"] for t in config["tools"]]
    assert urls == [f"{BASE}/tools/{n}" for n in ("dispatch_task", "get_status", "approve_action")]
    assert config["agent"]["conversation_config"]["agent"]["first_message"] == "{{greeting}}"
    assert "_notes" not in config


def test_every_prompt_variable_has_a_placeholder():
    agent = apply_agent.load_config(BASE)["agent"]["conversation_config"]["agent"]
    used = set(re.findall(r"{{(\w+)}}", agent["prompt"]["prompt"] + agent["first_message"]))
    placeholders = agent["dynamic_variables"]["dynamic_variable_placeholders"]
    assert used <= set(placeholders)


def test_prompt_mentions_every_tool():
    config = apply_agent.load_config(BASE)
    prompt = config["agent"]["conversation_config"]["agent"]["prompt"]["prompt"]
    for name in [t["tool_config"]["name"] for t in config["tools"]] + ["end_call"]:
        assert name in prompt


def test_every_tool_sends_the_secret_and_the_caller():
    config = apply_agent.with_secret_id(apply_agent.load_config(BASE), "sec_123")
    for tool in config["tools"]:
        schema = tool["tool_config"]["api_schema"]
        assert schema["request_headers"]["X-Shotgun-Secret"] == {"secret_id": "sec_123"}
        properties = schema["request_body_schema"]["properties"]
        assert properties["caller"]["dynamic_variable"] == "system__caller_id"
        assert properties["called"]["dynamic_variable"] == "system__called_number"


def test_greet_stage_has_no_tools_or_server_webhook():
    body = apply_agent.agent_body(apply_agent.load_config(BASE), "greet", [])
    assert body["conversation_config"]["agent"]["prompt"]["tool_ids"] == []
    platform = body["platform_settings"]
    assert "workspace_overrides" not in platform
    assert platform["overrides"]["enable_conversation_initiation_client_data_from_webhook"] is False


def test_full_stage_wires_tools_and_caller_webhook():
    config = apply_agent.with_secret_id(apply_agent.load_config(BASE), "sec_123")
    body = apply_agent.agent_body(config, "full", ["t1", "t2", "t3"])
    assert body["conversation_config"]["agent"]["prompt"]["tool_ids"] == ["t1", "t2", "t3"]
    webhook = body["platform_settings"]["workspace_overrides"][
        "conversation_initiation_client_data_webhook"
    ]
    assert webhook["url"] == f"{BASE}/tools/init"
    assert webhook["request_headers"]["X-Shotgun-Secret"] == {"secret_id": "sec_123"}
