#!/usr/bin/env bash
# SPEC-SEC-022 REQ-2 — meeting bots must not reach the host.
#
# DOCKER-USER is inbound-only (-i $EXT_IF) and Docker's DOCKER-FORWARD handles
# container → container. Container → HOST is neither: it lands in INPUT, whose
# policy is ACCEPT here and which nothing filters, since ufw is uninstalled and
# only its empty chains remain.
#
# Measured on 2026-08-17: a container on vexa12-bots could reach the host's SSH
# port and all five GPU tunnel forwards — ollama, vLLM ×2, embeddings, reranker,
# all unauthenticated. The bots run Chromium against meeting pages we do not
# control, which is the entire premise of SPEC-SEC-022.
#
# This pins the shape of the fix, because each part of it was a trap:
#   - the subnet must come from Docker, not a literal. The SPEC was written
#     against 172.27.0.0/16; that range now belongs to vexa12-portal, so a
#     hardcoded CIDR would filter the wrong containers entirely.
#   - the ACCEPT for transcription must sit ABOVE the DROP, or it does nothing.
#   - the script re-runs on every boot and deploy, so it must not stack rules.
#   - a missing network must be loud. Silently skipping leaves bots wide open
#     while the script still reports success.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SCRIPT="$REPO_ROOT/deploy/scripts/harden-docker-user.sh"
FAIL=0

run_script() {
    # $1 = subnet the docker stub reports ("" = network not found)
    local subnet="$1"
    local tmp; tmp="$(mktemp -d)"

    cat > "$tmp/docker" <<STUB
#!/usr/bin/env bash
[ -n "$subnet" ] && echo "$subnet"
exit 0
STUB
    cat > "$tmp/iptables" <<STUB
#!/usr/bin/env bash
echo "iptables \$*" >> "$tmp/calls.log"
# -D probes must fail once nothing is left to delete, or the cleanup loops forever.
if [ "\$1" = "-D" ]; then
    grep -q "DELETED \$*" "$tmp/state" 2>/dev/null && exit 1
    echo "DELETED \$*" >> "$tmp/state"
    exit 1
fi
exit 0
STUB
    cat > "$tmp/iptables-save" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
    chmod +x "$tmp/docker" "$tmp/iptables" "$tmp/iptables-save"

    PATH="$tmp:$PATH" bash "$SCRIPT" eth0 >"$tmp/out" 2>"$tmp/err" || true
    cat "$tmp/calls.log" 2>/dev/null > "$tmp/calls.final"
    LAST_OUT="$(cat "$tmp/out" "$tmp/err" 2>/dev/null)"
    LAST_CALLS="$(cat "$tmp/calls.final" 2>/dev/null)"
    rm -rf "$tmp"
}

check() {
    desc="$1"; shift
    if "$@"; then echo "OK:   $desc"; else echo "FAIL: $desc" >&2; FAIL=1; fi
}

echo "── bot host-isolation guard ──"

run_script "172.29.0.0/16"

check "subnet comes from Docker, not a literal" \
    bash -c 'echo "$0" | grep -q -- "-s 172.29.0.0/16"' "$LAST_CALLS"

check "the SPEC's stale 172.27.0.0/16 is not used" \
    bash -c '! echo "$0" | grep -q -- "172.27.0.0/16"' "$LAST_CALLS"

check "a default DROP is installed for the bot subnet" \
    bash -c 'echo "$0" | grep -q -- "-I INPUT 1 -s 172.29.0.0/16 -j DROP"' "$LAST_CALLS"

check "transcription (8000) is excepted" \
    bash -c 'echo "$0" | grep -q -- "-s 172.29.0.0/16 -p tcp --dport 8000 -j ACCEPT"' "$LAST_CALLS"

# Both are inserted at position 1, so the LAST insert ends up on top. The ACCEPT
# has to be the last one written or the DROP shadows it and transcription dies.
check "the exception is inserted after the DROP, so it lands above it" \
    bash -c '
      drop=$(echo "$0" | grep -n -- "-I INPUT 1 -s 172.29.0.0/16 -j DROP" | tail -1 | cut -d: -f1)
      acc=$(echo "$0" | grep -n -- "-I INPUT 1 -s 172.29.0.0/16 -p tcp --dport 8000" | tail -1 | cut -d: -f1)
      [ -n "$drop" ] && [ -n "$acc" ] && [ "$acc" -gt "$drop" ]' "$LAST_CALLS"

check "existing rules are cleared first, so re-runs do not stack" \
    bash -c 'echo "$0" | grep -q -- "-D INPUT -s 172.29.0.0/16 -j DROP"' "$LAST_CALLS"

# Network gone: the script must say so rather than quietly leaving bots exposed.
run_script ""

check "a missing bot network produces no INPUT rules" \
    bash -c '! echo "$0" | grep -q -- "-I INPUT"' "$LAST_CALLS"

check "a missing bot network warns loudly" \
    bash -c 'echo "$0" | grep -qi "WARNING.*not found"' "$LAST_OUT"

echo "──────────────────────────────"
if [ "$FAIL" -eq 0 ]; then
    echo "bot host-isolation guard: OK"
else
    echo "bot host-isolation guard: FAILED" >&2
fi
exit "$FAIL"
