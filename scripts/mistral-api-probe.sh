#!/usr/bin/env bash
# Probe Mistral API auth for the deployed Klai workspace key.
#
# Emits one JSON line to stdout and to /opt/klai/logs for Alloy file scraping.
set -euo pipefail

ENV_FILE="${KLAI_ENV_FILE:-/opt/klai/.env}"
URL="${MISTRAL_PROBE_URL:-https://api.mistral.ai/v1/models}"
TIMEOUT="${MISTRAL_PROBE_TIMEOUT:-10}"
LOG_FILE="${MISTRAL_PROBE_LOG_FILE:-/opt/klai/logs/mistral-api-probe.log}"

push_heartbeat() {
  local status="$1" token
  token=$(awk -F= '/^KUMA_TOKEN_MISTRAL=/ {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE")
  if [[ -z "$token" ]]; then
    echo "Mistral status heartbeat configuration missing" >&2
    return 1
  fi
  # Keep the token out of argv and diagnostics.
  if ! printf 'url = "https://status.getklai.com/api/push/%s?status=%s&msg=provider-api-auth"\n' \
    "$token" "$status" | curl --config - --fail --silent --max-time 10 --output /dev/null 2>/dev/null; then
    echo "Mistral status heartbeat delivery failed" >&2
    return 1
  fi
}

json_escape() {
  python3 -c 'import json,sys; print(json.dumps(sys.stdin.read())[1:-1])'
}

emit() {
  local status="$1"
  local http_status="$2"
  local key_suffix="$3"
  local error="$4"
  local correlation_id="$5"
  local escaped_error
  escaped_error=$(printf '%s' "$error" | json_escape)
  local line
  line=$(printf '{"service":"mistral-api-probe","event":"mistral_api_probe","msg":"mistral_api_probe","status":"%s","http_status":%s,"key_suffix":"%s","error":"%s","mistral_correlation_id":"%s"}' \
    "$status" "$http_status" "$key_suffix" "$escaped_error" "$correlation_id")
  printf '%s\n' "$line"
  if [[ -n "$LOG_FILE" ]]; then
    mkdir -p "$(dirname "$LOG_FILE")"
    printf '%s\n' "$line" >> "$LOG_FILE"
  fi
}

if [[ ! -r "$ENV_FILE" ]]; then
  emit fail 0 "" "env_file_unreadable:$ENV_FILE" ""
  exit 0
fi

key=$(
  awk -F= '
    /^MISTRAL_API_KEY=/ {
      sub(/^MISTRAL_API_KEY=/, "")
      print
      exit
    }
  ' "$ENV_FILE"
)

if [[ -z "$key" ]]; then
  emit fail 0 "" "missing_mistral_api_key" ""
  push_heartbeat down
  exit 0
fi

http_status=$(
  printf 'header = "Authorization: Bearer %s"\nurl = "%s"\n' "$key" "$URL" | curl --config - -sS -m "$TIMEOUT" \
    -o /dev/null -w '%{http_code}' 2>/dev/null || true
)

if [[ "$http_status" == "200" ]]; then
  emit ok 200 "" "" ""
  push_heartbeat up
  exit 0
fi

error_category=provider_transport_error
if [[ "$http_status" =~ ^[0-9]{3}$ && "$http_status" != "000" ]]; then
  error_category=provider_http_error
fi
emit fail "${http_status:-0}" "" "$error_category" ""
push_heartbeat down
