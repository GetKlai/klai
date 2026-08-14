#!/bin/sh
# SPEC-SEC-DOCKER-AUTHZ-001 — pin who may reach the Docker daemon.
#
# The existing rule in .claude/rules/klai/platform/docker-socket-proxy.md
# enumerates which containers MUST NOT join the socket-proxy network. That is an
# open policy: a new service is permitted by default and only a human reading the
# rule would notice. This inverts it — the member set and the raw-socket mount set
# are both pinned, so an addition fails CI and has to be argued for in a diff.
#
# Runs on the compose file, not on a live host, so it gates the change rather
# than reporting after the fact.

set -eu

COMPOSE="${1:-deploy/docker-compose.yml}"
FAIL=0

# Services permitted on the socket-proxy network. Adding one means the service can
# reach the Docker API; that is a security-review event.
EXPECTED_NETWORK_MEMBERS="docker-socket-proxy klai-docker-authz portal-api runtime-api-socket-proxy"

# Services permitted to bind the raw host socket. Everything else must go through
# klai-docker-authz, whose whole purpose is to inspect what they send.
#   docker-socket-proxy — the proxy itself
#   alloy               — log collection; needs GET only, tracked in the SPEC as
#                         an open item because a read-only socket FILE mount does
#                         not make the Docker API read-only
EXPECTED_SOCKET_MOUNTERS="docker-socket-proxy alloy"

actual_network_members=$(
    uv run --quiet --with pyyaml python - "$COMPOSE" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))
print(" ".join(sorted(
    n for n, v in d["services"].items()
    if "socket-proxy" in (v.get("networks") or [])
)))
PY
)

actual_socket_mounters=$(
    uv run --quiet --with pyyaml python - "$COMPOSE" <<'PY'
import sys, yaml
d = yaml.safe_load(open(sys.argv[1]))
out = []
for name, svc in d["services"].items():
    for vol in svc.get("volumes") or []:
        spec = vol if isinstance(vol, str) else f"{vol.get('source','')}:{vol.get('target','')}"
        if spec.split(":")[0] == "/var/run/docker.sock":
            out.append(name)
print(" ".join(sorted(set(out))))
PY
)

check() {
    label="$1"; expected="$2"; actual="$3"; fix="$4"
    exp=$(echo "$expected" | tr ' ' '\n' | sort | tr '\n' ' ')
    act=$(echo "$actual"   | tr ' ' '\n' | sort | tr '\n' ' ')
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
check "socket-proxy network members" "$EXPECTED_NETWORK_MEMBERS" "$actual_network_members" \
    "A service here can reach the Docker API. Justify it, then update EXPECTED_NETWORK_MEMBERS."
check "raw docker.sock mounters" "$EXPECTED_SOCKET_MOUNTERS" "$actual_socket_mounters" \
    "A raw socket mount bypasses klai-docker-authz entirely. Route it through the proxy, or justify and update EXPECTED_SOCKET_MOUNTERS."

echo "───────────────────────────────────────────────────"
[ "$FAIL" -eq 0 ] && echo "docker-access audit: OK" || echo "docker-access audit: FAILED" >&2
exit "$FAIL"
