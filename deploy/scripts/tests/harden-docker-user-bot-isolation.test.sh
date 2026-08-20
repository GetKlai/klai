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
WORKFLOW="$REPO_ROOT/.github/workflows/deploy-compose.yml"
FAIL=0

run_script() {
    # $1 = subnet the docker stub reports ("" = network not found)
    # $2 = meeting-api's address on that network ("" = pin missing)
    local subnet="$1"
    local MEETING_IP="${2-172.29.0.10}"
    local tmp; tmp="$(mktemp -d)"

    cat > "$tmp/docker" <<STUB
#!/usr/bin/env bash
case "\$*" in
  *IPAM*)      [ -n "$subnet" ] && echo "$subnet" ;;
  *meeting-api*) [ -n "$MEETING_IP" ] && echo "$MEETING_IP" ;;
esac
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

    # Redirect persistence into the fixture directory while preserving every
    # executable branch of the production script.
    sed "s|/etc/iptables/rules.v4|$tmp/rules.v4|g" "$SCRIPT" \
        | PATH="$tmp:$PATH" bash -s -- eth0 >"$tmp/out" 2>"$tmp/err"
    LAST_RC=$?
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

check "a resolved network and pin exit successfully" \
    test "$LAST_RC" -eq 0

check "subnet comes from Docker, not a literal" \
    bash -c 'echo "$0" | grep -q -- "-s 172.29.0.0/16"' "$LAST_CALLS"

check "the SPEC's stale 172.27.0.0/16 is not used" \
    bash -c '! echo "$0" | grep -q -- "172.27.0.0/16"' "$LAST_CALLS"

check "a default DROP is installed for the bot subnet" \
    bash -c 'echo "$0" | grep -q -- "-I INPUT 1 -s 172.29.0.0/16 -j DROP"' "$LAST_CALLS"

check "transcription (8000) is excepted for the pinned address only" \
    bash -c 'echo "$0" | grep -q -- "-I INPUT 1 -s 172.29.0.10 -p tcp --dport 8000 -j ACCEPT"' "$LAST_CALLS"

check "the whole subnet is NOT excepted — that would cover every bot" \
    bash -c '! echo "$0" | grep -q -- "-I INPUT 1 -s 172.29.0.0/16 -p tcp --dport 8000"' "$LAST_CALLS"

check "the old whole-subnet exception is cleared on re-run" \
    bash -c 'echo "$0" | grep -q -- "-D INPUT -s 172.29.0.0/16 -p tcp --dport 8000 -j ACCEPT"' "$LAST_CALLS"

# Both are inserted at position 1, so the LAST insert ends up on top. The ACCEPT
# has to be the last one written or the DROP shadows it and transcription dies.
check "the exception is inserted after the DROP, so it lands above it" \
    bash -c '
      drop=$(echo "$0" | grep -n -- "-I INPUT 1 -s 172.29.0.0/16 -j DROP" | tail -1 | cut -d: -f1)
      acc=$(echo "$0" | grep -n -- "-I INPUT 1 -s 172.29.0.10 -p tcp --dport 8000" | tail -1 | cut -d: -f1)
      [ -n "$drop" ] && [ -n "$acc" ] && [ "$acc" -gt "$drop" ]' "$LAST_CALLS"

check "established host-initiated connections are accepted above the DROP" \
    bash -c '
      drop=$(echo "$0" | grep -n -- "-I INPUT 1 -s 172.29.0.0/16 -j DROP" | tail -1 | cut -d: -f1)
      established=$(echo "$0" | grep -n -- "-I INPUT 1 -s 172.29.0.0/16 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT" | tail -1 | cut -d: -f1)
      [ -n "$drop" ] && [ -n "$established" ] && [ "$established" -gt "$drop" ]' "$LAST_CALLS"

check "existing rules are cleared first, so re-runs do not stack" \
    bash -c 'echo "$0" | grep -q -- "-D INPUT -s 172.29.0.0/16 -j DROP"' "$LAST_CALLS"

check "the established-connection rule is cleared first on re-run" \
    bash -c 'echo "$0" | grep -q -- "-D INPUT -s 172.29.0.0/16 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT"' "$LAST_CALLS"

# Network gone: the script must say so rather than quietly leaving bots exposed.
run_script ""

check "a missing bot network produces no INPUT rules" \
    bash -c '! echo "$0" | grep -q -- "-I INPUT"' "$LAST_CALLS"

check "a missing bot network warns loudly" \
    bash -c 'echo "$0" | grep -qi "WARNING.*not found"' "$LAST_OUT"

check "a missing bot network fails the firewall unit" \
    test "$LAST_RC" -ne 0

# The pin disappearing must fail CLOSED: deny stays, exception is not installed,
# and it says so. Silently widening back to the subnet would undo the whole point.
run_script "172.29.0.0/16" ""

check "a missing pin still installs the deny" \
    bash -c 'echo "$0" | grep -q -- "-I INPUT 1 -s 172.29.0.0/16 -j DROP"' "$LAST_CALLS"

# Match the INSERT specifically. The cleanup -D probes carry the same text, so a
# looser grep passes even when the rule really was installed.
check "a missing pin installs NO exception (fails closed)" \
    bash -c '! echo "$0" | grep -q -- "-I INPUT 1 .*--dport 8000 -j ACCEPT"' "$LAST_CALLS"

check "a missing pin warns loudly" \
    bash -c 'echo "$0" | grep -qi "WARNING.*transcription"' "$LAST_OUT"

check "a missing pin fails the firewall unit" \
    test "$LAST_RC" -ne 0

# Execute the real tail of the embedded deploy script with boundary commands
# stubbed. This catches ordering and exit-code bugs without SSH or Docker.
run_workflow_tail() {
    local scenario="$1"
    local tmp; tmp="$(mktemp -d)"

    awk '
      /echo "::group::SPEC-MCP-RETRIEVAL-001 follow-up/ { capture=1 }
      capture && /# SPEC-SEC-024 M4.3 — non-blocking post-deploy smoke-test/ { exit }
      capture { sub(/^            /, ""); print }
    ' "$WORKFLOW" | sed 's|cd /opt/klai|cd "$WORK_DIR"|' >"$tmp/workflow-tail.sh"

    cat >"$tmp/docker" <<'STUB'
#!/usr/bin/env bash
case "$*" in
  "compose config --services")
    case "$SCENARIO" in
      config_fail) printf 'portal\nlitellm\n'; exit 23 ;;
      empty)       printf 'litellm\n'; exit 0 ;;
      *)           printf 'portal\nlitellm\n'; exit 0 ;;
    esac
    ;;
  compose\ up\ -d\ --remove-orphans*)
    echo "compose-up $*" >>"$CALLS"
    [ "$SCENARIO" = compose_fail ] && exit 42
    exit 0
    ;;
esac
exit 0
STUB
    cat >"$tmp/sudo" <<'STUB'
#!/usr/bin/env bash
echo "sudo $*" >>"$CALLS"
exit 0
STUB
    cat >"$tmp/systemctl" <<'STUB'
#!/usr/bin/env bash
echo "systemctl $*" >>"$CALLS"
exit 0
STUB
    chmod +x "$tmp/docker" "$tmp/sudo" "$tmp/systemctl"

    set +e
    PATH="$tmp:$PATH" SCENARIO="$scenario" CALLS="$tmp/calls" WORK_DIR="$tmp" \
        bash -e "$tmp/workflow-tail.sh" >"$tmp/out" 2>&1
    WORKFLOW_RC=$?
    set -e
    WORKFLOW_OUT="$(cat "$tmp/out")"
    WORKFLOW_CALLS="$(cat "$tmp/calls" 2>/dev/null || true)"
    rm -rf "$tmp"
}

run_workflow_tail config_fail
check "a compose-config pipeline failure aborts before compose up" \
    test "$WORKFLOW_RC" -eq 23
check "a compose-config failure cannot trigger an all-services recreate" \
    bash -c '! grep -q "compose-up" <<<"$0"' "$WORKFLOW_CALLS"

run_workflow_tail empty
check "an empty env-drift service list fails loudly" \
    bash -c '[ "$1" -ne 0 ] && grep -qi "no env-drift services" <<<"$0"' "$WORKFLOW_OUT" "$WORKFLOW_RC"
check "an empty service list never reaches compose up" \
    bash -c '! grep -q "compose-up" <<<"$0"' "$WORKFLOW_CALLS"

run_workflow_tail compose_fail
check "a failed main compose retains its original exit code" \
    test "$WORKFLOW_RC" -eq 42
check "a failed main compose still re-applies the firewall exactly once" \
    bash -c '[ "$(grep -c "^sudo systemctl restart klai-harden-firewall.service$" <<<"$0")" -eq 1 ]' "$WORKFLOW_CALLS"
check "the env-drift compose call still excludes litellm" \
    bash -c 'grep -q "compose-up .* portal" <<<"$0" && ! grep -q "compose-up .* litellm" <<<"$0"' "$WORKFLOW_CALLS"

run_workflow_tail success
check "a successful main compose re-applies the firewall exactly once" \
    bash -c '[ "$1" -eq 0 ] && [ "$(grep -c "^sudo systemctl restart klai-harden-firewall.service$" <<<"$0")" -eq 1 ]' "$WORKFLOW_CALLS" "$WORKFLOW_RC"

echo "──────────────────────────────"
if [ "$FAIL" -eq 0 ]; then
    echo "bot host-isolation guard: OK"
else
    echo "bot host-isolation guard: FAILED" >&2
fi
exit "$FAIL"
