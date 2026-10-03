"""Offline checks for config/elevenlabs_agent.json, config/elevenlabs_prompt.md and
scripts/apply_agent.py (steps 1.1, 2.3; D17: the agent stays on the call)."""

import importlib.util
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("apply_agent", ROOT / "scripts" / "apply_agent.py")
apply_agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(apply_agent)

BASE = "https://shotgun.example.com"
INLINE = ("search_web", "draft_message", "set_destination")
BACKGROUND = ("dispatch_task", "get_status", "approve_action")


def conversation():
    return apply_agent.load_config(BASE)["agent"]["conversation_config"]


def prompt_text():
    return conversation()["agent"]["prompt"]["prompt"]


def tools():
    return {
        t["tool_config"]["name"]: t["tool_config"] for t in apply_agent.load_config(BASE)["tools"]
    }


def test_base_url_substituted_and_dynamic_variables_kept():
    config = apply_agent.load_config(BASE + "/")
    urls = [t["tool_config"]["api_schema"]["url"] for t in config["tools"]]
    assert sorted(urls) == sorted(f"{BASE}/tools/{n}" for n in INLINE + BACKGROUND)
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
    for name in [t["tool_config"]["name"] for t in config["tools"]] + ["end_call", "skip_turn"]:
        assert name in prompt


def test_prompt_comes_from_its_own_file():
    assert prompt_text() == (ROOT / "config" / "elevenlabs_prompt.md").read_text().strip()
    assert "prompt_file" not in conversation()["agent"]["prompt"]


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
    body = apply_agent.agent_body(config, "full", ["t1", "t2", "t3", "t4", "t5", "t6"])
    assert body["conversation_config"]["agent"]["prompt"]["tool_ids"] == [
        "t1",
        "t2",
        "t3",
        "t4",
        "t5",
        "t6",
    ]
    webhook = body["platform_settings"]["workspace_overrides"][
        "conversation_initiation_client_data_webhook"
    ]
    assert webhook["url"] == f"{BASE}/tools/init"
    assert webhook["request_headers"]["X-Shotgun-Secret"] == {"secret_id": "sec_123"}


def test_agent_stays_on_the_call():
    """D17 replaces the 14:25 "always hang up" rule: no hanging up to continue later."""
    prompt = prompt_text()
    for gone in ("I'll call you back", "Never leave the line open", "only delivers a message"):
        assert gone not in prompt
    assert "You never hang up" in prompt
    assert "dispatching a job doesn't end the call" in prompt
    assert prompt.count("How can I help") == 1  # only in "Never open with ..."


def test_end_call_has_exactly_three_reasons():
    prompt = prompt_text()
    assert "end_call is allowed for exactly three reasons" in prompt
    assert '1. The driver says goodbye, "that\'s all"' in prompt
    assert "2. The caller check above says the line is private." in prompt
    assert "3. Silence" in prompt
    end_call = conversation()["agent"]["prompt"]["built_in_tools"]["end_call"]
    assert end_call["params"] == {"system_tool_type": "end_call"}


def test_silence_rule_asks_at_60_s_and_hangs_up_30_s_later():
    """60 s of silence -> "Anything else?"; 30 s more -> end the call (D17, Daniel 17:43).

    ElevenLabs caps turn_timeout at 30 s, so the 60 s is 30 s turn timeout + a 30 s skip_turn
    wait, after which the agent checks in.
    """
    conv = conversation()
    turn = conv["turn"]
    skip = conv["agent"]["prompt"]["built_in_tools"]["skip_turn"]
    assert skip["params"]["system_tool_type"] == "skip_turn"
    assert turn["turn_timeout"] <= 30  # documented maximum
    assert turn["turn_timeout"] + skip["params"]["wait_timeout_secs"] == 60
    prompt = prompt_text()
    assert "call skip_turn and say nothing" in prompt
    assert 'check in, say exactly "Anything else?" and nothing more' in prompt
    assert 'still say nothing after "Anything else?"' in prompt and "call end_call" in prompt
    # The next silent turn comes turn_timeout (30 s) after "Anything else?".
    assert turn["turn_timeout"] == 30
    # Backstop if the model misses it: never earlier than the 90 s rule.
    assert turn["silence_end_call_timeout"] >= 90


def test_inline_tools_cover_the_8_s_budget_and_speak_first():
    for name in INLINE:
        config = tools()[name]
        assert 8 < config["response_timeout_secs"] <= 15
        assert config["pre_tool_speech"] == "force"
    assert "say a short filler first" in prompt_text()


def test_background_tools_keep_the_short_timeout():
    for name in BACKGROUND:
        assert tools()[name]["response_timeout_secs"] == 5
        assert "pre_tool_speech" not in tools()[name]


def test_dispatch_takes_type_details_and_a_nested_preapproval():
    schema = tools()["dispatch_task"]["api_schema"]["request_body_schema"]
    assert schema["required"] == ["details"]
    pre = schema["properties"]["preapproval"]
    assert pre["type"] == "object"
    assert pre["required"] == ["condition"]
    assert {k: v["type"] for k, v in pre["properties"].items()} == {
        "condition": "string",
        "require_tests_pass": "boolean",
        "max_usd": "number",
    }
    assert "repeat the condition back" in prompt_text()


def test_get_status_is_scoped_to_the_drive():
    properties = tools()["get_status"]["api_schema"]["request_body_schema"]["properties"]
    assert properties["drive_id"]["dynamic_variable"] == "drive_id"


def test_lookups_just_search_and_never_mention_helpers():
    """Daniel 18:30: "find me 3 nearby ramen places" got "I can't do that, but I can open a
    research task and have someone do it". Lookups go straight to search_web, and the agent
    speaks as itself."""
    prompt = prompt_text()
    assert "don't ask first and don't explain, just say the filler, call search_web" in prompt
    assert "places near them" in prompt
    assert "never say you can't search" in prompt
    assert 'Never mention tasks, background jobs, workers, agents or "someone else"' in prompt
    for offer in ("offer to look it up", "in the background", "have someone"):
        assert offer not in prompt
    search = tools()["search_web"]
    assert search["api_schema"]["request_body_schema"]["properties"]["drive_id"] == {
        "type": "string",
        "dynamic_variable": "drive_id",
    }


def test_agent_talks_like_a_friend_not_a_screen_reader():
    """Daniel 18:43: stiff, read every street number and closing time, said "Sent" for a job
    that had only been dispatched."""
    prompt = prompt_text()
    assert "Talk like a friend riding along" in prompt
    assert "is a note for you, not a script: say it in your own words, shorter" in prompt
    assert "Never read out street numbers, full addresses, coordinates or exact times" in prompt
    assert 'never "Sent" or "Done"' in prompt
    assert "where they are right now" in prompt


def test_agent_is_honest_about_what_it_cant_do_yet():
    """Daniel 18:40: it said "Sent." for an email, but there is no email worker (3.2 stretch);
    the job failed and the arrival call had to admit it. Same for food (3.4, no worker)."""
    prompt = prompt_text()
    assert "You can't send messages yet" in prompt
    assert "Never call dispatch_task for a message, and never say it's sent." in prompt
    assert "You can't order food yet" in prompt
    assert "type email" not in prompt and "type food" not in prompt


def test_voicemail_leaves_the_update_and_hangs_up():
    """The 19:30 arrival call reached Daniel's voicemail and talked to the greeting for 112 s."""
    vm = conversation()["agent"]["prompt"]["built_in_tools"]["voicemail_detection"]
    assert vm["params"]["system_tool_type"] == "voicemail_detection"
    assert "{{summary}}" in vm["params"]["voicemail_message"]
    assert "call voicemail_detection right away" in prompt_text()
    placeholders = conversation()["agent"]["dynamic_variables"]["dynamic_variable_placeholders"]
    assert "summary" in placeholders


def test_destination_is_only_asked_on_departure_calls():
    """The same arrival call asked "Where are you headed?"."""
    assert "Only on a departure call" in prompt_text()
    assert "Never ask on an arrival or exception call" in prompt_text()
