#!/bin/sh
# SPEC-SEC-DOCKER-AUTHZ-001 — pin who may reach the Docker daemon.
#
# The rule in .claude/rules/klai/platform/docker-socket-proxy.md enumerates which
# containers MUST NOT join the socket-proxy network. That is an open policy: a new
# service is permitted by default and only a human reading the rule would notice.
# This inverts it — every set below is pinned, so an addition fails CI and has to
# be argued for in a diff.
#
# Runs against the compose file rather than a live host, so it gates the change
# instead of reporting after the fact.

set -eu

COMPOSE="${1:-deploy/docker-compose.yml}"
FAIL=0

# ── The mutating lane ────────────────────────────────────────────────────────
# Members can create, start and remove containers. Every one of them goes through
# klai-docker-authz, which inspects the container-create body.
EXPECTED_NETWORK_MEMBERS="docker-socket-proxy klai-docker-authz portal-api runtime-api-socket-proxy"

# ── The GET-only lane ────────────────────────────────────────────────────────
# CONTAINERS and NETWORKS are GET-side reads; POST and DELETE are unset, so
# members here can list, inspect and stream logs but cannot create, start,
# remove or connect anything. NETWORKS is required: Alloy's docker discovery
# computes network labels per target and 403s without it. A member that needs to
# mutate belongs on socket-proxy, behind klai-docker-authz.
EXPECTED_RO_NETWORK_MEMBERS="alloy docker-socket-proxy-ro"

# ── Raw socket ───────────────────────────────────────────────────────────────
# Only the two proxies. Alloy used to be here: its `:ro` mount protected the
# socket FILE and not the Docker API — a process that can write that byte stream
# can still POST /containers/create. It now uses the GET-only proxy, so the
# read-only intent is structural rather than a mount flag
# (SPEC-SEC-DOCKER-AUTHZ-001 REQ-U-002a).
EXPECTED_SOCKET_MOUNTERS="docker-socket-proxy docker-socket-proxy-ro"

network_members() {
    uv run --quiet --with pyyaml python -c "
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))
net = sys.argv[2]
print(' '.join(sorted(
    n for n, v in d['services'].items() if net in (v.get('networks') or [])
)))
" "$COMPOSE" "$1"
}

socket_mounters() {
    uv run --quiet --with pyyaml python -c "
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))
out = []
for name, svc in d['services'].items():
    for vol in svc.get('volumes') or []:
        spec = vol if isinstance(vol, str) else f\"{vol.get('source','')}:{vol.get('target','')}\"
        if spec.split(':')[0] == '/var/run/docker.sock':
            out.append(name)
print(' '.join(sorted(set(out))))
" "$COMPOSE"
}

check() {
    label="$1"; expected="$2"; actual="$3"; fix="$4"
    exp=$(echo "$expected" | tr ' ' '\n' | sed '/^$/d' | sort | tr '\n' ' ')
    act=$(echo "$actual"   | tr ' ' '\n' | sed '/^$/d' | sort | tr '\n' ' ')
    if [ "$exp" = "$act" ]; then
        echo "OK:   $label — $act"
    else
        echo "FAIL: $label drifted" >&2
        echo "      expected: $exp" >&2
        echo "      actual:   $act" >&2
        echo "      $fix" >&2
        FAIL=1
    fi
}

echo "── SPEC-SEC-DOCKER-AUTHZ-001 docker-access audit ──"

check "socket-proxy members (mutating)" "$EXPECTED_NETWORK_MEMBERS" "$(network_members socket-proxy)" \
    "A service here can create containers. Justify it, then update EXPECTED_NETWORK_MEMBERS."

check "socket-proxy-ro members (GET-only)" "$EXPECTED_RO_NETWORK_MEMBERS" "$(network_members socket-proxy-ro)" \
    "This lane cannot mutate. A member that needs to belongs on socket-proxy, behind klai-docker-authz."

# The GET-only lane is only GET-only while its proxy has no mutating verbs. A
# membership check alone would pass while POST: 1 quietly turned the lane into a
# second, unpoliced create path — found by mutating this very script.
ro_verbs=$(
    uv run --quiet --with pyyaml python -c "
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))
env = d['services'].get('docker-socket-proxy-ro', {}).get('environment') or {}
print(' '.join(sorted(k for k, v in env.items() if str(v) == '1')))
" "$COMPOSE"
)
check "socket-proxy-ro allowed verbs" "CONTAINERS NETWORKS" "$ro_verbs" \
    "This proxy must stay GET-only. POST or DELETE here creates a second, unpoliced container-create path."

check "raw docker.sock mounters" "$EXPECTED_SOCKET_MOUNTERS" "$(socket_mounters)" \
    "A raw socket mount bypasses both proxies. Route it through one, or justify and update EXPECTED_SOCKET_MOUNTERS."

echo "───────────────────────────────────────────────────"
if [ "$FAIL" -eq 0 ]; then
    echo "docker-access audit: OK"
else
    echo "docker-access audit: FAILED" >&2
fi
exit "$FAIL"
