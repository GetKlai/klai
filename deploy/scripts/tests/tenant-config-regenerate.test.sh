#!/usr/bin/env bash
# Tests for tenant-config-regenerate.sh.
#
# No Docker daemon and no portal-api needed. The stub docker runs the python the
# script would run inside portal-api, locally, against a fake httpx module that
# answers from fixture files. That exercises the real verdict logic (status code,
# errors array) rather than asserting which shape was sent.
#
# Run: bash deploy/scripts/tests/tenant-config-regenerate.test.sh

set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
target="$script_dir/tenant-config-regenerate.sh"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

failures=0
case_dir=""

mkdir -p "$work/bin" "$work/py"

# Every call the script makes lands in $FIXTURES/calls, in order, so a case can
# assert that the lock came before the health poll and the poll before the
# regenerate. The script refuses to run without flock; this stub also records
# the file behind fd 9 (/proc on Linux, F_GETPATH on macOS), so a case can prove
# which lock was taken.
cat > "$work/bin/flock" <<'STUB'
#!/usr/bin/env bash
if [[ -e "/proc/$$/fd/9" ]]; then
  lock="$(readlink "/proc/$$/fd/9")"
else
  lock="$(python3 -c 'import fcntl; print(fcntl.fcntl(9, fcntl.F_GETPATH, bytes(1024)).rstrip(b"\0").decode())')"
fi
echo "flock $* lock=$lock" >> "$FIXTURES/calls"
exit 0
STUB

cat > "$work/bin/docker" <<'STUB'
#!/usr/bin/env bash
set -u
# Expected: compose -f <file> exec -T [-e K=V]... portal-api python -c <code>
[[ "$1" == compose && "$2" == -f && "$4" == exec && "$5" == -T ]] \
  || { echo "stub: unexpected docker args: $*" >&2; exit 90; }
shift 5
while [[ "$1" == -e ]]; do export "${2?}"; shift 2; done
[[ "$1" == portal-api && "$2" == python && "$3" == -c ]] \
  || { echo "stub: unexpected exec args: $*" >&2; exit 90; }
INTERNAL_SECRET=test-secret PYTHONPATH="$FAKE_PY" exec python3 -c "$4"
STUB

cat > "$work/py/httpx.py" <<'PY'
import json
import os

_fx = os.environ["FIXTURES"]


def _read(name, default=""):
    path = os.path.join(_fx, name)
    return open(path).read().strip() if os.path.exists(path) else default


def _log(line):
    with open(os.path.join(_fx, "calls"), "a") as f:
        f.write(line + "\n")


class ConnectError(Exception):
    pass


class Response:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text

    def json(self):
        return json.loads(self.text)


def get(url, timeout):
    _log(f"health {url}")
    calls = sum(1 for line in open(os.path.join(_fx, "calls")) if line.startswith("health "))
    ok_after = _read("health_ok_after", "0")
    if ok_after == "never" or calls <= int(ok_after):
        raise ConnectError("connection refused")
    return Response(int(_read("health_status", "200")), "ok")


def post(url, headers, timeout):
    _log(f"regenerate {url} auth={headers['Authorization']}")
    return Response(int(_read("regen_status", "200")), _read("regen_body"))
PY

chmod +x "$work/bin/flock" "$work/bin/docker"

new_case() {
    case_dir="$work/$1"
    mkdir -p "$case_dir/fixtures"
    : > "$case_dir/fixtures/calls"
    echo '{"tenants_updated": ["acme"], "tenants_skipped": [], "errors": []}' > "$case_dir/fixtures/regen_body"
}

fx() { printf '%s\n' "$2" > "$case_dir/fixtures/$1"; }

# Extra VAR=value arguments go into the script's environment.
run_target() {
    set +e
    env PATH="$work/bin:$PATH" \
    KLAI_COMPOSE_DIR="$case_dir" \
    KLAI_DOCKER_BIN="$work/bin/docker" \
    KLAI_AUTHZ_WAIT_ATTEMPTS=3 \
    KLAI_AUTHZ_WAIT_INTERVAL=0 \
    FIXTURES="$case_dir/fixtures" \
    FAKE_PY="$work/py" \
    "$@" bash "$target" > "$case_dir/out" 2>&1
    rc=$?
    set -e
}

fail() {
    echo "  FAIL $1"
    sed 's/^/       /' "$case_dir/out"
    echo "       --- calls ---"
    sed 's/^/       /' "$case_dir/fixtures/calls"
    failures=$((failures + 1))
}

check() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else fail "$1 (expected $2, got $3)"; fi; }
check_output() { if grep -qF -- "$2" "$case_dir/out"; then echo "  ok   $1"; else fail "$1 (output lacks: $2)"; fi; }
check_calls() { if grep -qE -- "$2" "$case_dir/fixtures/calls"; then echo "  ok   $1"; else fail "$1 (calls lack: $2)"; fi; }
check_no_calls() { if grep -qE -- "$2" "$case_dir/fixtures/calls"; then fail "$1 (calls contain: $2)"; else echo "  ok   $1"; fi; }

echo "tenant-config-regenerate.sh"

# Regenerating while the proxy is down writes every tenant's config and then
# fails each container action. The script has to stop before the first write.
new_case proxy_never_answers
fx health_ok_after never
run_target
check "proxy never answers -> 1" 1 "$rc"
check_output "  says why" "klai-docker-authz did not answer"
check_calls "  polled every attempt" "^health .*klai-docker-authz:2375/health"
check "  polled exactly the configured number of times" 3 "$(grep -c '^health ' "$case_dir/fixtures/calls")"
check_no_calls "  never called regenerate" "^regenerate "

# Answering is not enough: a proxy that answers 500 cannot authorise the
# container actions the regenerate is about to ask for.
new_case proxy_answers_500
fx health_status 500
run_target
check "the proxy answers 500 on /health -> 1" 1 "$rc"
check_no_calls "  never called regenerate" "^regenerate "

new_case proxy_answers
fx health_ok_after 1
run_target
check "proxy answers on the second poll -> 0" 0 "$rc"
check_output "  reports success" "Tenant configs regenerated successfully"
check_output "  prints the tenants it updated" "tenants_updated (1): ['acme']"
check_calls "  calls the regenerate endpoint with the internal secret" \
    "^regenerate http://localhost:8010/internal/librechat/regenerate auth=Bearer test-secret$"
# The same host lock compose-up.sh takes, held before the proxy is polled, so a
# klai-docker-authz deploy cannot start between "proxy answers" and the regenerate.
check "  takes the host lock compose-up.sh takes, first" \
    "flock -w 900 9 lock=$(cd "$case_dir" && pwd -P)/.container-deploy.lock" \
    "$(head -n1 "$case_dir/fixtures/calls")"

# Finding 3D (2026-08-13): the endpoint reports per-tenant failures in its errors
# array on a 200, so the status code alone would call a partial failure green.
new_case errors_on_200
fx regen_body '{"tenants_updated": ["acme"], "errors": ["beta: container restart failed"]}'
run_target
check "HTTP 200 with a non-empty errors array -> 1" 1 "$rc"
check_output "  lists the error" "  - beta: container restart failed"
check_output "  says why it failed" "non-empty errors array on HTTP 200"

# The other half of belt and braces: an empty errors array does not make a
# non-200 green.
new_case regen_500_no_errors
fx regen_status 500
run_target
check "regenerate answers HTTP 500 with an empty errors array -> 1" 1 "$rc"
check_output "  names the status" "regenerate endpoint returned HTTP 500"

new_case recreate_containers
run_target RECREATE_CONTAINERS=true
check "RECREATE_CONTAINERS=true -> 0" 0 "$rc"
check_calls "  passes recreate_containers=true to the endpoint" \
    "^regenerate http://localhost:8010/internal/librechat/regenerate\?recreate_containers=true "

echo
if [[ "$failures" -eq 0 ]]; then
    echo "tenant-config-regenerate: all cases passed"
else
    echo "tenant-config-regenerate: $failures case(s) failed"
    exit 1
fi
