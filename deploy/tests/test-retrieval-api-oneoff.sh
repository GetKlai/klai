#!/usr/bin/env bash
# deploy/scripts/retrieval-api-oneoff.sh starts a throwaway retrieval-api beside the live one.
# This pins what a wrong clone would get silently wrong: a name that is the live service's own
# (or its compose alias), a container left behind when it never became healthy, the read-only
# launcher and the code mount, and the refusal to clone a stopped service.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/retrieval-api-oneoff.sh"
FAIL=0

run_oneoff() {
    # $1 = name given to --name; $2 = "ok"/"down" — whether the one-off ever answers /health;
    # $3 = "true"/"false" — what docker inspect reports for the live service's .State.Running
    local name="$1" health="$2" running="${3:-true}"
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/bin" "$tmp/code/retrieval_api"

    export ONEOFF_TEST_TMP="$tmp" ONEOFF_TEST_HEALTH="$health" ONEOFF_TEST_RUNNING="$running"

    cat >"$tmp/bin/docker" <<'STUB'
#!/usr/bin/env bash
echo "$*" >> "$ONEOFF_TEST_TMP/calls.log"
case "$1" in
    inspect)
        case "$*" in
            *'{{.State.Running}}'*) echo "$ONEOFF_TEST_RUNNING" ;;
            *'{{.Image}}'*) echo "sha256:deadbeef" ;;
            *'{{.Config.WorkingDir}}'*) echo "/repo/klai-retrieval-api" ;;
            *'.Aliases'*) printf 'retrieval-api\nabc123def456\n' ;;
            *'{{range .Config.Env}}'*) printf 'FOO=bar\nRERANKER_CANDIDATES=20\nSENTRY_DSN=https://example.com/1\n' ;;
            *'NetworkSettings.Networks'*) printf 'klai-net\nklai-net-postgres\n' ;;
        esac
        ;;
    create)
        for arg in "$@"; do
            if [ "${prev:-}" = "--env-file" ]; then cp "$arg" "$ONEOFF_TEST_TMP/env-file"; fi
            prev="$arg"
        done
        ;;
    exec)
        [ "$ONEOFF_TEST_HEALTH" = ok ] || exit 1
        ;;
esac
exit 0
STUB
    chmod +x "$tmp/bin/docker"

    set +e
    PATH="$tmp/bin:$PATH" KLAI_ONEOFF_HEALTH_TRIES=2 \
        bash "$SCRIPT" --name "$name" --code "$tmp/code" --env RERANKER_CANDIDATES=60 >"$tmp/out.log" 2>"$tmp/err.log"
    LAST_RC=$?
    set -e

    LAST_CALLS="$(cat "$tmp/calls.log" 2>/dev/null || true)"
    LAST_ENV_FILE="$tmp/env-file"
    LAST_ERR="$(cat "$tmp/err.log" 2>/dev/null || true)"
    unset ONEOFF_TEST_TMP ONEOFF_TEST_HEALTH ONEOFF_TEST_RUNNING
}

check() {
    desc="$1"
    shift
    if "$@"; then
        echo "OK:   $desc"
    else
        echo "FAIL: $desc" >&2
        FAIL=1
    fi
}

calls_have() { echo "$LAST_CALLS" | grep -q -- "$1"; }
calls_lack() { ! echo "$LAST_CALLS" | grep -q -- "$1"; }

echo "── retrieval-api one-off clone guard ──"

run_oneoff ra-test ok

check "the one-off succeeds when it answers /health" test "$LAST_RC" -eq 0
check "it is created from the live image with its working directory" \
    calls_have "-w /repo/klai-retrieval-api"
check "it is unmanaged: removed on exit, labelled, named as asked" \
    bash -c 'echo "$0" | grep -q -- "create --rm" && echo "$0" | grep -q -- "--name ra-test" && echo "$0" | grep -q -- "--label klai.oneoff=true"' "$LAST_CALLS"
check "the checkout's package is mounted read-only over the image's" \
    bash -c 'echo "$0" | grep -q -- "destination=/repo/klai-retrieval-api/retrieval_api,readonly"' "$LAST_CALLS"
check "the launcher is mounted read-only" \
    bash -c 'echo "$0" | grep -q -- "retrieval-api-oneoff-launch.py,destination=/launch.py,readonly"' "$LAST_CALLS"
check "the second network is attached before start" \
    calls_have "network connect klai-net-postgres ra-test"
check "an override replaces the live value and the Sentry DSN is dropped" \
    bash -c 'grep -q "^RERANKER_CANDIDATES=60$" "$0" && ! grep -q "^RERANKER_CANDIDATES=20$" "$0" && ! grep -q "^SENTRY_DSN=" "$0" && grep -q "^FOO=bar$" "$0"' "$LAST_ENV_FILE"
check "a healthy one-off is not removed" calls_lack "rm -f"

for refused in klai-core-retrieval-api-1 retrieval-api; do
    run_oneoff "$refused" ok

    check "the name '$refused' of the live service is refused" test "$LAST_RC" -ne 0
    check "docker create was never invoked for '$refused'" calls_lack "create"
    check "the refusal for '$refused' says why" \
        bash -c 'echo "$0" | grep -qi "live service"' "$LAST_ERR"
done

run_oneoff ra-test down

check "a one-off that never answers /health fails the script" test "$LAST_RC" -ne 0
check "and is removed, not left running" calls_have "rm -f ra-test"

run_oneoff ra-test ok false

check "a stopped service is refused" test "$LAST_RC" -ne 0
check "docker create was never invoked against a stopped service" calls_lack "create"

echo "─────────────────────────────"
if [ "$FAIL" -eq 0 ]; then
    echo "retrieval-api one-off clone guard: OK"
else
    echo "retrieval-api one-off clone guard: FAILED" >&2
fi
exit "$FAIL"
