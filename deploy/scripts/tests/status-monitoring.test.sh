#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
WORKFLOW="${WORKFLOW_FILE:-$ROOT/.github/workflows/deploy-compose.yml}"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/source" "$tmp/dest" "$tmp/bin"

for name in push-health.sh gpu-health.sh audit-monitor-coverage.sh push-component-health.sh; do
    printf '#!/usr/bin/env bash\necho public\n' > "$tmp/source/$name"
    printf '#!/usr/bin/env bash\necho private\n' > "$tmp/dest/$name"
done
printf '#!/usr/bin/env bash\necho current\n' > "$tmp/source/backup.sh"
# retrieval-api-oneoff.sh bind-mounts this helper from the synced directory.
printf 'print("launch")\n' > "$tmp/source/retrieval-api-oneoff-launch.py"
# The extracted fragment calls rb_keep from compose-rollback.sh, which the real
# step sources earlier; this test is about which files get installed, not about
# their .prev copies.
{
    echo 'rb_keep() { :; }'
    awk '/# Sync public-safe deploy scripts/{copy=1} /test -x \/opt\/klai\/scripts\/backup.sh/{copy=0} copy' \
        "$WORKFLOW" | sed -e "s#deploy/scripts#$tmp/source#g" \
            -e "s#/opt/klai/scripts#$tmp/dest#g"
} > "$tmp/sync.sh"
bash -e "$tmp/sync.sh"
for name in push-health.sh gpu-health.sh audit-monitor-coverage.sh push-component-health.sh; do
    if ! grep -qx 'echo private' "$tmp/dest/$name"; then
        echo "$name was overwritten by the public deploy" >&2
        exit 1
    fi
done
grep -qx 'echo current' "$tmp/dest/backup.sh"
grep -qx 'print("launch")' "$tmp/dest/retrieval-api-oneoff-launch.py"

printf 'MISTRAL_API_KEY=provider-key\nMISTRAL_API_KEY_2=provider-key-two\nMISTRAL_VIBE_KEY=vibe-key\nKUMA_TOKEN_MISTRAL=heartbeat-token\n' > "$tmp/env"
# Answers PROVIDER_STATUS for MISTRAL_API_KEY and PROVIDER_STATUS_2 for
# MISTRAL_API_KEY_2; any other key reaching Mistral fails the probe run.
cat > "$tmp/bin/curl" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
config=$(cat)
if [[ " $* " == *" -w "* ]]; then
    [[ "$config" == *'url = "https://api.mistral.ai/v1/chat/completions"'* ]]
    [[ "$config" == *'max_tokens'* ]]
    case "$config" in
        *'header = "Authorization: Bearer provider-key"'*) status="${PROVIDER_STATUS:-200}" ;;
        *'header = "Authorization: Bearer provider-key-two"'*) status="${PROVIDER_STATUS_2:-200}" ;;
        *) exit 99 ;;
    esac
    headers_file="" body_file=""
    while [[ $# -gt 0 ]]; do
        case "$1" in -D) headers_file="$2"; shift 2;; -o) body_file="$2"; shift 2;; *) shift;; esac
    done
    if [[ -n "${REFLECT_SECRET:-}" ]]; then
        [[ -z "$headers_file" ]] || printf 'mistral-correlation-id: provider-key\r\n' > "$headers_file"
        [[ "$body_file" == /dev/null ]] || printf '{"error":"provider-key rejected"}\n' > "$body_file"
    else
        [[ -z "$headers_file" ]] || printf '\n' > "$headers_file"
        [[ "$body_file" == /dev/null ]] || : > "$body_file"
    fi
    printf '%s' "$status"
else
    printf '%s\n' "$config" > "$HEARTBEAT_LOG"
    [[ -z "${DELIVERY_FAIL:-}" ]]
fi
STUB
chmod +x "$tmp/bin/curl"
fingerprint() { printf '%s' "$1" | python3 -c 'import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest()[:12])'; }
fp1=$(fingerprint provider-key)
fp2=$(fingerprint provider-key-two)
state="$tmp/state/litellm/mistral-keys.json"
key_state() {  # fingerprint field -> value from the status file
    python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["keys"][sys.argv[2]][sys.argv[3]])' "$state" "$1" "$2"
}
probe() {
    PATH="$tmp/bin:$PATH" KLAI_ENV_FILE="${ENV:-$tmp/env}" MISTRAL_PROBE_LOG_FILE="$tmp/probe.log" \
        MISTRAL_PROBE_STATE_FILE="$state" HEARTBEAT_LOG="$tmp/heartbeat" \
        bash "$ROOT/scripts/mistral-api-probe.sh" >/dev/null
}

probe
grep -q 'heartbeat-token?status=up' "$tmp/heartbeat"
[[ $(key_state "$fp1" state) == ok && $(key_state "$fp2" state) == ok ]]
grep -q "\"status\":\"ok\".*\"key_fingerprint\":\"$fp1\"" "$tmp/probe.log"
grep -q "\"status\":\"ok\".*\"key_fingerprint\":\"$fp2\"" "$tmp/probe.log"
if grep -q 'provider-key\|vibe-key' "$tmp/probe.log" "$state"; then
    echo "provider key leaked in successful probe log or status file" >&2
    exit 1
fi

# Full is a state of one workspace: the other key stays ok and keeps the
# `since` of its unchanged state, and the file is replaced by rename (a new
# inode, no temp file left), never rewritten in place where LiteLLM could
# read half of it.
printf '{"generated_at":"2026-10-01T00:00:00Z","keys":{"%s":{"state":"ok","since":"2026-10-01T00:00:00Z","http_status":200},"%s":{"state":"ok","since":"2026-10-01T00:00:00Z","http_status":200}}}' \
    "$fp1" "$fp2" > "$state"
inode() { python3 -c 'import os,sys; print(os.stat(sys.argv[1]).st_ino)' "$state"; }
inode_before=$(inode)
: > "$tmp/probe.log"
PROVIDER_STATUS=402 probe
[[ $(inode) != "$inode_before" ]]
[[ $(ls -A "$tmp/state/litellm") == mistral-keys.json ]]
[[ $(key_state "$fp1" state) == full && $(key_state "$fp1" http_status) == 402 ]]
[[ $(key_state "$fp1" since) != 2026-10-01T00:00:00Z ]]
[[ $(key_state "$fp2" state) == ok && $(key_state "$fp2" since) == 2026-10-01T00:00:00Z ]]
grep -q "\"status\":\"full\",\"http_status\":402,\"key_env\":\"MISTRAL_API_KEY\",\"key_fingerprint\":\"$fp1\",\"error\":\"provider_budget_exhausted\",\"keys_total\":2,\"keys_ok\":1" "$tmp/probe.log"
grep -q 'heartbeat-token?status=up' "$tmp/heartbeat"

# No usable key: one workspace full and the other key rejected counts as
# zero usable keys, not as one key in reserve.
: > "$tmp/probe.log"
PROVIDER_STATUS=402 PROVIDER_STATUS_2=401 probe
[[ $(grep -c '"keys_total":2,"keys_ok":0' "$tmp/probe.log") == 2 ]]
grep -q 'heartbeat-token?status=down' "$tmp/heartbeat"

# Every key full.
: > "$tmp/probe.log"
PROVIDER_STATUS=402 PROVIDER_STATUS_2=402 probe
[[ $(grep -c '"status":"full".*"keys_ok":0' "$tmp/probe.log") == 2 ]]
grep -q 'heartbeat-token?status=down' "$tmp/heartbeat"

PROVIDER_STATUS=401 PROVIDER_STATUS_2=401 REFLECT_SECRET=1 probe
grep -q 'heartbeat-token?status=down' "$tmp/heartbeat"
grep -q '"error":"provider_http_error"' "$tmp/probe.log"
[[ $(key_state "$fp1" state) == error ]]
if grep -q 'provider-key' "$tmp/probe.log" "$state"; then
    echo "reflected provider key leaked in failed probe log or status file" >&2
    exit 1
fi
grep -v KUMA_TOKEN_MISTRAL "$tmp/env" > "$tmp/env-without-heartbeat"
if ENV="$tmp/env-without-heartbeat" probe 2>"$tmp/error"; then
    echo "missing heartbeat configuration passed" >&2
    exit 1
fi
grep -qx 'Mistral status heartbeat configuration missing' "$tmp/error"
if grep -q 'provider-key\|heartbeat-token' "$tmp/error"; then
    echo "credential leaked in missing-configuration error" >&2
    exit 1
fi
if DELIVERY_FAIL=1 probe 2>"$tmp/error"; then
    echo "failed heartbeat delivery passed" >&2
    exit 1
fi
grep -qx 'Mistral status heartbeat delivery failed' "$tmp/error"
if grep -q 'provider-key\|heartbeat-token' "$tmp/error"; then
    echo "credential leaked in delivery error" >&2
    exit 1
fi

# An unreadable env file is a run with zero usable keys, so the same alert
# matches it, instead of a line no rule reads while the probe looks alive.
: > "$tmp/probe.log"
ENV="$tmp/no-such-env" probe
grep -q '"error":"env_file_unreadable","keys_total":0,"keys_ok":0' "$tmp/probe.log"
