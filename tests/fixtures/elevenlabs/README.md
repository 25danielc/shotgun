Sample bodies ElevenLabs sends to our webhook tools: the LLM's parameters plus the dynamic
variables bound in config/elevenlabs_agent.json. `+15555550100` stands for ALLOWED_CALLER_NUMBER
and `+15555550199` for the Shotgun Twilio number; scripts/curl_tools.sh swaps in the real values.
Used by tests/test_step_2_2_voice_tools.py and scripts/curl_tools.sh (step 2.2 pass check).
