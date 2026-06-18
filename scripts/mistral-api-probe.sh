#!/usr/bin/env bash
# Probe Mistral API auth for the deployed Klai workspace key.
#
# Emits one JSON line to stdout and to /opt/klai/logs for Alloy file scraping.
set -euo pipefail

ENV_FILE="${KLAI_ENV_FILE:-/opt/klai/.env}"
URL="${MISTRAL_PROBE_URL:-https://api.mistral.ai/v1/models}"
TIMEOUT="${MISTRAL_PROBE_TIMEOUT:-10}"
LOG_FILE="${MISTRAL_PROBE_LOG_FILE:-/opt/klai/logs/mistral-api-probe.log}"

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
  exit 0
fi

key_suffix="${key: -4}"
headers_file=$(mktemp)
body_file=$(mktemp)
curl_error_file="$body_file.curlerr"
trap 'rm -f "$headers_file" "$body_file" "$curl_error_file"' EXIT

http_status=$(
  curl -sS -m "$TIMEOUT" \
    -D "$headers_file" \
    -o "$body_file" \
    -w '%{http_code}' \
    -H "Authorization: Bearer ${key}" \
    "$URL" 2>"$curl_error_file" || true
)

correlation_id=$(
  awk 'BEGIN{IGNORECASE=1} /^mistral-correlation-id:/ {gsub("\r","",$2); print $2; exit} /^x-kong-request-id:/ {gsub("\r","",$2); print $2; exit}' "$headers_file"
)

if [[ "$http_status" == "200" ]]; then
  emit ok 200 "$key_suffix" "" "$correlation_id"
  exit 0
fi

error_body=$(head -c 500 "$body_file" 2>/dev/null || true)
curl_error=$(head -c 500 "$curl_error_file" 2>/dev/null || true)
if [[ -n "$curl_error" ]]; then
  error_body="${error_body} curl_error=${curl_error}"
fi
emit fail "${http_status:-0}" "$key_suffix" "$error_body" "$correlation_id"
