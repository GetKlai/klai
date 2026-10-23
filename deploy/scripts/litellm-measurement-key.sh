#!/usr/bin/env bash
# Create the LiteLLM virtual key that measurement one-offs use instead of the master key, or --verify it.
#
# On 23 October 2026 measurement rounds ran on klai-primary, klai-medium and klai-fast with the master key, spent
# both chat workspaces' monthly Mistral limit and took chat down for every tenant. meetlat routes to the Vibe aliases
# since #2006, but anything else run inside a one-off still held the master key. This key's model list is klai-judge
# and klai-ingest, so LiteLLM's own key check refuses every chat alias whoever uses it; key_type llm_api keeps it off
# the management routes, so it cannot mint a wider key; and its daily budget caps what a runaway measurement takes
# from the Vibe allowance. portal-api-oneoff.sh and retrieval-api-oneoff.sh put it in place of every LITELLM_*KEY
# variable and refuse to start without it. klai_pii_enforce.py honours the delegated org on this alias the way it
# does on the master key, so a replayed customer turn is masked as it would be live.
#
# Usage: litellm-measurement-key.sh           create the key once and write it to KEY_FILE (idempotent)
#        litellm-measurement-key.sh --verify  prove the gateway refuses klai-primary and serves klai-ingest
# Run as root on core-01: KEY_FILE lives in the root-owned /opt/klai/state/litellm and is handed to the deploy user.
# LiteLLM stores only the key's hash, so a key whose file is gone cannot be recovered: it is deleted and replaced.

set -euo pipefail

ENV_FILE="${KLAI_ENV_FILE:-/opt/klai/.env}"
KEY_FILE="${KLAI_MEASUREMENT_KEY_FILE:-/opt/klai/state/litellm/measurement-key}"
KEY_OWNER="${KLAI_DEPLOY_USER:-klai}"
LITELLM_CONTAINER="${KLAI_LITELLM_CONTAINER:-klai-core-litellm-1}"
# deploy/litellm/klai_pii_enforce.py names this alias too; tests/test_measurement_key_access.py keeps them equal.
KEY_ALIAS="klai-measurement"
# 20 USD a day is several rounds (a full replay of the reviewed widget set costs about 3 USD). The spend also counts
# against the aliases' own daily budgets in config.yaml (klai-ingest 20, klai-judge 30), so a day that spends all of
# it on klai-ingest also stops knowledge-ingest's background work until that budget resets.
KEY_BODY='{"key_alias": "'"$KEY_ALIAS"'", "models": ["klai-judge", "klai-ingest"], "key_type": "llm_api", "max_budget": 20, "budget_duration": "1d", "metadata": {"purpose": "measurement one-offs, deploy/scripts/litellm-measurement-key.sh"}}'

[[ $# -eq 0 || ( $# -eq 1 && "$1" == "--verify" ) ]] || { echo "Usage: $0 [--verify]" >&2; exit 1; }

url="${KLAI_LITELLM_URL:-}"
if [[ -z "$url" ]]; then
    url="http://$(docker inspect --format '{{(index .NetworkSettings.Networks "klai-net").IPAddress}}' "$LITELLM_CONTAINER"):4000"
fi

resp="$(mktemp)"
trap 'rm -f "$resp"' EXIT

# call KEY METHOD PATH [JSON]: prints the HTTP status and leaves the body in $resp. The key reaches curl on stdin,
# never in argv where ps would show it.
call() {
    local body="${4:-}"
    {
        printf 'header = "Authorization: Bearer %s"\nheader = "Content-Type: application/json"\n' "$1"
        printf 'request = "%s"\nurl = "%s%s"\n' "$2" "$url" "$3"
        [[ -z "$body" ]] || printf 'data = "%s"\n' "$(sed 's/["\\]/\\&/g' <<< "$body")"
    } | curl --config - -sS -m 60 -o "$resp" -w '%{http_code}'
}

# json EXPR: evaluates a Python expression over the parsed response body, bound to `b`.
json() { python3 -c "import json, sys; b = json.load(open(sys.argv[1])); print($1)" "$resp"; }

if [[ "${1:-}" == "--verify" ]]; then
    key="$(cat "$KEY_FILE" 2>/dev/null || true)"
    [[ -n "$key" ]] || { echo "ERROR: no measurement key in $KEY_FILE; run $0 first." >&2; exit 1; }
    chat='{"model": "%s", "max_tokens": 1, "messages": [{"role": "user", "content": "ok"}]}'
    fail=0

    # A 402 or 429 would come from the router after the key check let the request through, and so would a provider
    # 401: only LiteLLM's own key_model_access_denied proves that the key is what stops it.
    # shellcheck disable=SC2059 # $chat is the format, the alias the argument
    status="$(call "$key" POST /v1/chat/completions "$(printf "$chat" klai-primary)")"
    type="$(json "(b.get('error') or {}).get('type')" 2>/dev/null || echo unparsed)"
    echo "klai-primary: HTTP $status, error.type=$type"
    [[ "$status" =~ ^40[13]$ && "$type" == "key_model_access_denied" ]] || { echo "FAIL: klai-primary was not refused by the key check" >&2; fail=1; }

    # shellcheck disable=SC2059
    status="$(call "$key" POST /v1/chat/completions "$(printf "$chat" klai-ingest)")"
    echo "klai-ingest: HTTP $status"
    [[ "$status" == 200 ]] || { echo "FAIL: klai-ingest did not answer 200: $(head -c 300 "$resp")" >&2; fail=1; }

    status="$(call "$key" GET "/key/list?key_alias=$KEY_ALIAS")"
    echo "/key/list (a management route): HTTP $status"
    [[ "$status" =~ ^40[13]$ ]] || { echo "FAIL: the measurement key reached a management route" >&2; fail=1; }

    exit "$fail"
fi

master="$(awk '/^LITELLM_MASTER_KEY=/ {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE")"
[[ -n "$master" ]] || { echo "ERROR: no LITELLM_MASTER_KEY in $ENV_FILE." >&2; exit 1; }

status="$(call "$master" GET "/key/list?key_alias=$KEY_ALIAS&return_full_object=true")"
[[ "$status" == 200 ]] || { echo "ERROR: /key/list answered $status: $(head -c 300 "$resp")" >&2; exit 1; }
tokens="$(json "' '.join(k['token'] for k in b['keys'])")"

current="$(cat "$KEY_FILE" 2>/dev/null || true)"
if [[ -n "$current" ]]; then
    hash="$(printf '%s' "$current" | sha256sum | cut -d' ' -f1)"
    if [[ " $tokens " == *" $hash "* ]]; then
        chown "$KEY_OWNER:$KEY_OWNER" "$KEY_FILE"
        chmod 600 "$KEY_FILE"
        echo "$KEY_ALIAS already exists and $KEY_FILE holds it; nothing to do."
        exit 0
    fi
    echo "ERROR: $KEY_FILE holds a key that is not $KEY_ALIAS; move it aside and rerun." >&2
    exit 1
fi

if [[ -n "$tokens" ]]; then
    echo "$KEY_ALIAS exists but $KEY_FILE is empty or missing; LiteLLM keeps only its hash, so it is replaced."
    status="$(call "$master" POST /key/delete '{"key_aliases": ["'"$KEY_ALIAS"'"]}')"
    [[ "$status" == 200 ]] || { echo "ERROR: /key/delete answered $status: $(head -c 300 "$resp")" >&2; exit 1; }
fi

# On a host where the Mistral probe has not run yet the state directory does not exist; an existing one keeps its owner.
key_dir="$(dirname "$KEY_FILE")"
if [[ ! -d "$key_dir" ]]; then
    mkdir -p "$key_dir"
    chown "$KEY_OWNER:$KEY_OWNER" "$key_dir"
fi
status="$(call "$master" POST /key/generate "$KEY_BODY")"
[[ "$status" == 200 ]] || { echo "ERROR: /key/generate answered $status: $(head -c 300 "$resp")" >&2; exit 1; }
tmp="$(mktemp "$(dirname "$KEY_FILE")/.measurement-key.XXXXXX")"
json "b['key']" | tr -d '\n' > "$tmp"
chown "$KEY_OWNER:$KEY_OWNER" "$tmp"
chmod 600 "$tmp"
mv -f "$tmp" "$KEY_FILE"
echo "Created $KEY_ALIAS and wrote it to $KEY_FILE (owner $KEY_OWNER, mode 600). Check it with: $0 --verify"
