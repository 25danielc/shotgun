#!/usr/bin/env bash
# Step 2.2 pass check: POST each sample ElevenLabs payload to a running server with curl and
# require HTTP 200 in under 500 ms (background tools) or 8 s (inline tools, which call Claude).
# Usage: scripts/curl_tools.sh [BASE_URL]
# (default: PUBLIC_BASE_URL from .env, else http://localhost:8000).
# Reads TOOLS_SHARED_SECRET, ALLOWED_CALLER_NUMBER and TWILIO_PHONE_NUMBER from the environment,
# else .env, and swaps them into the samples. Never prints the secret.
set -euo pipefail
cd "$(dirname "$0")/.."

env_value() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- || true; }
BASE="${1:-${PUBLIC_BASE_URL:-$(env_value PUBLIC_BASE_URL)}}"
BASE="${BASE:-http://localhost:8000}"
BASE="${BASE%/}"
SECRET="${TOOLS_SHARED_SECRET:-$(env_value TOOLS_SHARED_SECRET)}"
DANIEL="${ALLOWED_CALLER_NUMBER:-$(env_value ALLOWED_CALLER_NUMBER)}"
TWILIO="${TWILIO_PHONE_NUMBER:-$(env_value TWILIO_PHONE_NUMBER)}"
[[ -n "$SECRET" && -n "$DANIEL" ]] || { echo "Set TOOLS_SHARED_SECRET and ALLOWED_CALLER_NUMBER in .env"; exit 1; }

failed=0
reply="$(mktemp)"
trap 'rm -f "$reply"' EXIT
for entry in dispatch_task:0.5 get_status:0.5 approve_action:0.5 search_web:8 draft_message:8; do
  tool="${entry%%:*}"; limit="${entry##*:}"
  # dispatch uses a research-only sample: the multi-part one would make the live planner file a
  # real coder issue.
  fixture="$tool"; [[ "$tool" == dispatch_task ]] && fixture=dispatch_task_curl
  body="$(sed -e "s/+15555550100/${DANIEL}/" -e "s/+15555550199/${TWILIO:-+15555550199}/" \
    "tests/fixtures/elevenlabs/${fixture}.json")"
  out="$(curl -sS -o "$reply" -w '%{http_code} %{time_total}' \
    -X POST "${BASE}/tools/${tool}" -H "Content-Type: application/json" \
    -H "X-Shotgun-Secret: ${SECRET}" --data "${body}")"
  code="${out%% *}"; secs="${out##* }"
  verdict="OK"
  if [[ "$code" != "200" ]] || awk "BEGIN{exit !($secs >= $limit)}"; then verdict="FAIL"; failed=1; fi
  printf '%-15s %s  %4s  %.3fs  %s\n' "$tool" "$verdict" "$code" "$secs" "$(head -c 160 "$reply")"
done
exit "$failed"
