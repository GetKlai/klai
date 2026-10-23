#!/usr/bin/env bash
# Sourced by portal-api-oneoff.sh and retrieval-api-oneoff.sh: every LITELLM_*KEY variable in a one-off's environment
# is the measurement key, a virtual key that can call klai-judge and klai-ingest only, so the keys the clone is handed
# cannot reach a chat alias. On 23 Oct 2026 measurement rounds on the master key spent the chat workspaces' monthly
# Mistral limit and took chat down for every tenant. The tenants' own LiteLLM keys are kept out by
# portal-api-oneoff.sh (no /librechat mount, a fresh PORTAL_SECRETS_KEY). The key is created by
# litellm-measurement-key.sh. No flag skips this.

MEASUREMENT_KEY_FILE="${KLAI_MEASUREMENT_KEY_FILE:-/opt/klai/state/litellm/measurement-key}"
LITELLM_CONTAINER="${KLAI_LITELLM_CONTAINER:-klai-core-litellm-1}"

# Reads the key into $measurement_key and proves at the gateway that it is no wider than the measurement key: one
# request to klai-primary has to fail on LiteLLM's own key check (key_model_access_denied), which happens before the
# router and costs nothing. A hand-placed wider key gets a 200, or a 402 or 429 from the router, and aborts here.
measurement_key_load_and_probe() {
    measurement_key="$(cat "$MEASUREMENT_KEY_FILE" 2>/dev/null || true)"
    if [[ -z "$measurement_key" ]]; then
        echo "ERROR: no LiteLLM measurement key in $MEASUREMENT_KEY_FILE; create it with deploy/scripts/litellm-measurement-key.sh." >&2
        exit 1
    fi
    local ip out status
    ip="$(docker inspect --format '{{(index .NetworkSettings.Networks "klai-net").IPAddress}}' "$LITELLM_CONTAINER")"
    # The key reaches curl on stdin, never in argv where ps would show it.
    out="$(printf 'header = "Authorization: Bearer %s"\nheader = "Content-Type: application/json"\nurl = "http://%s:4000/v1/chat/completions"\ndata = "{\\"model\\": \\"klai-primary\\", \\"max_tokens\\": 1, \\"messages\\": [{\\"role\\": \\"user\\", \\"content\\": \\"ok\\"}]}"\n' \
        "$measurement_key" "$ip" | curl --config - -sS -m 30 -w '\n%{http_code}' 2>&1 || true)"
    status="${out##*$'\n'}"
    if [[ ! "$status" =~ ^40[13]$ ]] || ! grep -Eq '"type" *: *"key_model_access_denied"' <<< "$out"; then
        echo "ERROR: LiteLLM's key check did not refuse klai-primary on the key in $MEASUREMENT_KEY_FILE (HTTP $status), so it is not the measurement key; recreate it with litellm-measurement-key.sh." >&2
        exit 1
    fi
}

# measurement_key_swap ENV_FILE CONTAINER: replaces the value of every LITELLM_*KEY variable with the measurement key.
# A measurement key equal to a key the live service holds is that key under another name, and an environment with no
# LITELLM_*KEY variable means it was renamed and this swap no longer covers it: both refuse. Names match in any case,
# because pydantic-settings reads them case-insensitively (litellm_api_key=... sets LITELLM_API_KEY).
measurement_key_swap() {
    local env_file="$1" container="$2" swapped rc=0
    swapped="$(MEASUREMENT_KEY="$measurement_key" awk '
        toupper(substr($0, 1, index($0, "="))) ~ /^LITELLM_[A-Z0-9_]*KEY=$/ {
            eq = index($0, "=")
            if (substr($0, eq + 1) == ENVIRON["MEASUREMENT_KEY"]) live = 1
            print substr($0, 1, eq) ENVIRON["MEASUREMENT_KEY"]
            n++
            next
        }
        { print }
        END { exit live ? 2 : (n ? 0 : 3) }' "$env_file")" || rc=$?
    case "$rc" in
        0) printf '%s\n' "$swapped" > "$env_file" ;;
        2) echo "ERROR: $MEASUREMENT_KEY_FILE holds a key $container uses; recreate it with litellm-measurement-key.sh." >&2; exit 1 ;;
        *) echo "ERROR: $container has no LITELLM_*KEY variable to replace with the measurement key." >&2; exit 1 ;;
    esac
}
