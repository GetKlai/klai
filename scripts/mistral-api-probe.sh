#!/usr/bin/env bash
# Probe that every Mistral key LiteLLM routes over can actually complete a
# request, and publish the result for LiteLLM's klai_mistral_pool hook.
#
# It used to GET /v1/models, which only proves the key is valid. On
# 2026-09-26 the workspace hit its monthly spending limit: every completion
# returned 402 for hours while /v1/models kept answering 200 and this probe
# stayed green. A one-token completion sees what users see.
#
# It also used to test MISTRAL_API_KEY only. In October 2026 that workspace
# stayed full for three days while LiteLLM found out by letting one user
# request per alias fail every five minutes. Every MISTRAL_API_KEY and
# MISTRAL_API_KEY_<n> in the env file is probed now, and the per-key verdict
# is written atomically to STATE_FILE; klai_mistral_pool.py never offers a
# key that file calls full. The file is keyed by the first 12 hex chars of
# sha256(key), so it holds no secret.
#
# Emits one JSON line per key to stdout and to /opt/klai/logs for Alloy file
# scraping.
set -euo pipefail

ENV_FILE="${KLAI_ENV_FILE:-/opt/klai/.env}"
URL="${MISTRAL_PROBE_URL:-https://api.mistral.ai/v1/chat/completions}"
MODEL="${MISTRAL_PROBE_MODEL:-mistral-small-2603}"
# Keys are probed one after another and the heartbeat push allows 10s, so a
# run takes at most keys x TIMEOUT + 10s: 26s for two keys, inside the
# unit's TimeoutStartSec=30s that core-01 may still be running (the unit file
# is synced but only re-read after a manual daemon-reload, see the runbook).
TIMEOUT="${MISTRAL_PROBE_TIMEOUT:-8}"
LOG_FILE="${MISTRAL_PROBE_LOG_FILE:-/opt/klai/logs/mistral-api-probe.log}"
STATE_FILE="${MISTRAL_PROBE_STATE_FILE:-/opt/klai/state/litellm/mistral-keys.json}"

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

emit() {
  local line="$1"
  printf '%s\n' "$line"
  if [[ -n "$LOG_FILE" ]]; then
    mkdir -p "$(dirname "$LOG_FILE")"
    printf '%s\n' "$line" >> "$LOG_FILE"
  fi
}

if [[ ! -r "$ENV_FILE" ]]; then
  # keys_ok 0 is what the no-usable-key alert matches: a probe that cannot
  # read the keys cannot vouch for any of them, and the state file goes stale.
  emit "{\"service\":\"mistral-api-probe\",\"event\":\"mistral_api_probe\",\"msg\":\"mistral_api_probe\",\"status\":\"error\",\"http_status\":0,\"error\":\"env_file_unreadable\",\"keys_total\":0,\"keys_ok\":0}"
  exit 0
fi

# NAME FINGERPRINT HTTP_STATUS per key, fed to the writer below on stdin.
results=""
while IFS=$'\t' read -r name key; do
  fingerprint=$(printf '%s' "$key" | python3 -c 'import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest()[:12])')
  http_status=$(
    printf 'header = "Authorization: Bearer %s"\nheader = "Content-Type: application/json"\nurl = "%s"\ndata = "{\\"model\\":\\"%s\\",\\"max_tokens\\":1,\\"messages\\":[{\\"role\\":\\"user\\",\\"content\\":\\"ok\\"}]}"\n' \
      "$key" "$URL" "$MODEL" | curl --config - -sS -m "$TIMEOUT" \
      -o /dev/null -w '%{http_code}' 2>/dev/null || true
  )
  [[ "$http_status" =~ ^[0-9]{3}$ ]] || http_status=000
  results+="$name $fingerprint $http_status"$'\n'
done < <(
  awk '
    match($0, /^MISTRAL_API_KEY(_[0-9]+)?=/) {
      value = substr($0, RLENGTH + 1)
      if (value != "") printf "%s\t%s\n", substr($0, 1, RLENGTH - 1), value
    }
  ' "$ENV_FILE"
)

# Writes STATE_FILE (temp file + rename, so LiteLLM never reads half a file),
# keeps each key's `since` while its state is unchanged, and prints one log
# line per key. Prints nothing and writes {"keys": {}} when no key was found.
log_lines=$(printf '%s' "$results" | python3 -c '
import json, os, sys, tempfile
from datetime import datetime, timezone

state_file = sys.argv[1]
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
try:
    with open(state_file) as f:
        previous = json.load(f)["keys"]
except (OSError, ValueError, KeyError):
    previous = {}

probed = []
for line in sys.stdin.read().splitlines():
    name, fingerprint, http_status = line.split()
    http_status = int(http_status)
    if http_status == 200:
        state, error = "ok", ""
    elif http_status == 402:
        state, error = "full", "provider_budget_exhausted"
    elif http_status == 0:
        state, error = "error", "provider_transport_error"
    else:
        state, error = "error", "provider_http_error"
    probed.append((name, fingerprint, http_status, state, error))

keys = {}
for name, fingerprint, http_status, state, error in probed:
    before = previous.get(fingerprint) or {}
    since = before.get("since", now) if before.get("state") == state else now
    keys[fingerprint] = {"state": state, "since": since, "http_status": http_status}

os.makedirs(os.path.dirname(state_file), exist_ok=True)
fd, tmp = tempfile.mkstemp(dir=os.path.dirname(state_file), prefix=".mistral-keys.")
with os.fdopen(fd, "w") as f:
    json.dump({"generated_at": now, "keys": keys}, f)
os.chmod(tmp, 0o644)
os.replace(tmp, state_file)

# Only a key that completed counts as usable: a full key and a key that
# answers 401 or times out serve nobody alike.
usable = sum(1 for p in probed if p[3] == "ok")
for name, fingerprint, http_status, state, error in probed:
    print(json.dumps({
        "service": "mistral-api-probe", "event": "mistral_api_probe", "msg": "mistral_api_probe",
        "status": state, "http_status": http_status, "key_env": name, "key_fingerprint": fingerprint,
        "error": error, "keys_total": len(probed), "keys_ok": usable,
    }, separators=(",", ":")))
' "$STATE_FILE")

if [[ -z "$log_lines" ]]; then
  emit "{\"service\":\"mistral-api-probe\",\"event\":\"mistral_api_probe\",\"msg\":\"mistral_api_probe\",\"status\":\"error\",\"http_status\":0,\"error\":\"missing_mistral_api_key\",\"keys_total\":0,\"keys_ok\":0}"
  push_heartbeat down
  exit 0
fi

while IFS= read -r line; do
  emit "$line"
done <<< "$log_lines"

# Users are served while any key completes, so that is what the status page shows.
if grep -q '"status":"ok"' <<< "$log_lines"; then
  push_heartbeat up
else
  push_heartbeat down
fi
