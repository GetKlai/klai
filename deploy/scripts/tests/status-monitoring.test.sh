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
awk '/# Sync public-safe deploy scripts/{copy=1} /test -x \/opt\/klai\/scripts\/backup.sh/{copy=0} copy' \
    "$WORKFLOW" | sed -e "s#deploy/scripts#$tmp/source#g" \
        -e "s#/opt/klai/scripts#$tmp/dest#g" > "$tmp/sync.sh"
bash -e "$tmp/sync.sh"
for name in push-health.sh gpu-health.sh audit-monitor-coverage.sh push-component-health.sh; do
    if ! grep -qx 'echo private' "$tmp/dest/$name"; then
        echo "$name was overwritten by the public deploy" >&2
        exit 1
    fi
done
grep -qx 'echo current' "$tmp/dest/backup.sh"

printf 'MISTRAL_API_KEY=provider-key\nKUMA_TOKEN_MISTRAL=heartbeat-token\n' > "$tmp/env"
cat > "$tmp/bin/curl" <<'STUB'
#!/usr/bin/env bash
set -euo pipefail
config=$(cat)
if [[ " $* " == *" -w "* ]]; then
    [[ "$config" == *'url = "https://api.mistral.ai/v1/models"'* ]]
    [[ "$config" == *'header = "Authorization: Bearer provider-key"'* ]]
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
    printf '%s' "${PROVIDER_STATUS:-200}"
else
    printf '%s\n' "$config" > "$HEARTBEAT_LOG"
    [[ -z "${DELIVERY_FAIL:-}" ]]
fi
STUB
chmod +x "$tmp/bin/curl"
PATH="$tmp/bin:$PATH" KLAI_ENV_FILE="$tmp/env" MISTRAL_PROBE_LOG_FILE="$tmp/probe.log" \
    HEARTBEAT_LOG="$tmp/heartbeat" bash "$ROOT/scripts/mistral-api-probe.sh" >/dev/null
grep -q 'heartbeat-token?status=up' "$tmp/heartbeat"
grep -q '"key_suffix":""' "$tmp/probe.log"
if grep -q 'provider-key\|"key_suffix":"-key"' "$tmp/probe.log"; then
    echo "provider key leaked in successful probe log" >&2
    exit 1
fi
PROVIDER_STATUS=401 REFLECT_SECRET=1 PATH="$tmp/bin:$PATH" KLAI_ENV_FILE="$tmp/env" \
    MISTRAL_PROBE_LOG_FILE="$tmp/probe.log" HEARTBEAT_LOG="$tmp/heartbeat" \
    bash "$ROOT/scripts/mistral-api-probe.sh" >/dev/null
grep -q 'heartbeat-token?status=down' "$tmp/heartbeat"
grep -q '"error":"provider_http_error"' "$tmp/probe.log"
if grep -q 'provider-key' "$tmp/probe.log"; then
    echo "reflected provider key leaked in failed probe log" >&2
    exit 1
fi
grep -v KUMA_TOKEN_MISTRAL "$tmp/env" > "$tmp/env-without-heartbeat"
if PATH="$tmp/bin:$PATH" KLAI_ENV_FILE="$tmp/env-without-heartbeat" \
    MISTRAL_PROBE_LOG_FILE="$tmp/probe.log" HEARTBEAT_LOG="$tmp/heartbeat" \
    bash "$ROOT/scripts/mistral-api-probe.sh" >/dev/null 2>"$tmp/error"; then
    echo "missing heartbeat configuration passed" >&2
    exit 1
fi
grep -qx 'Mistral status heartbeat configuration missing' "$tmp/error"
if grep -q 'provider-key\|heartbeat-token' "$tmp/error"; then
    echo "credential leaked in missing-configuration error" >&2
    exit 1
fi
if DELIVERY_FAIL=1 PATH="$tmp/bin:$PATH" KLAI_ENV_FILE="$tmp/env" \
    MISTRAL_PROBE_LOG_FILE="$tmp/probe.log" HEARTBEAT_LOG="$tmp/heartbeat" \
    bash "$ROOT/scripts/mistral-api-probe.sh" >/dev/null 2>"$tmp/error"; then
    echo "failed heartbeat delivery passed" >&2
    exit 1
fi
grep -qx 'Mistral status heartbeat delivery failed' "$tmp/error"
if grep -q 'provider-key\|heartbeat-token' "$tmp/error"; then
    echo "credential leaked in delivery error" >&2
    exit 1
fi
