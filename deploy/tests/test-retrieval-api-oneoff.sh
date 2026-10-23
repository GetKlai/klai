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
    # $4 = "ok"/"missing"/"live" — the measurement key file holds a key, is absent, or holds the live service's key
    local name="$1" health="$2" running="${3:-true}" key_mode="${4:-ok}"
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/bin" "$tmp/code/retrieval_api"

    export KLAI_MEASUREMENT_KEY_FILE="$tmp/measurement-key"
    case "$key_mode" in
        ok) printf 'measurement-test-key' >"$KLAI_MEASUREMENT_KEY_FILE" ;;
        live) printf 'live-master-key' >"$KLAI_MEASUREMENT_KEY_FILE" ;;
    esac

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
            *'{{range .Config.Env}}'*) printf 'FOO=bar\nRERANKER_CANDIDATES=20\nCOREFERENCE_MODEL=klai-fast\nLITELLM_API_KEY=live-master-key\nSENTRY_DSN=https://example.com/1\n' ;;
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
        bash "$SCRIPT" --name "$name" --code "$tmp/code" --env RERANKER_CANDIDATES=60 --env LITELLM_API_KEY=live-master-key >"$tmp/out.log" 2>"$tmp/err.log"
    LAST_RC=$?
    set -e

    LAST_CALLS="$(cat "$tmp/calls.log" 2>/dev/null || true)"
    LAST_ENV_FILE="$tmp/env-file"
    LAST_ERR="$(cat "$tmp/err.log" 2>/dev/null || true)"
    unset ONEOFF_TEST_TMP ONEOFF_TEST_HEALTH ONEOFF_TEST_RUNNING KLAI_MEASUREMENT_KEY_FILE
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
check "every model of the one-off's own LLM calls is the Vibe alias, whatever the live value" \
    bash -c 'for k in COREFERENCE_MODEL GRAPHITI_LLM_MODEL SYNTHESIS_MODEL; do [ "$(grep -c "^$k=" "$0")" = 1 ] && grep -q "^$k=klai-ingest$" "$0" || exit 1; done' "$LAST_ENV_FILE"
check "a caller cannot put a pinned model back on a shared key" \
    bash -c 'bash "$0" --name x --code /nonexistent --env COREFERENCE_MODEL=klai-fast 2>&1 | grep -q "fixed to klai-ingest"' "$SCRIPT"
check "a healthy one-off is not removed" calls_lack "rm -f"
check "the LiteLLM key is the measurement key, even when --env names the live one" \
    bash -c '[ "$(grep -c "^LITELLM_API_KEY=" "$0")" = 1 ] && grep -q "^LITELLM_API_KEY=measurement-test-key$" "$0" && ! grep -q "live-master-key" "$0"' "$LAST_ENV_FILE"

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

for key_mode in missing live; do
    run_oneoff ra-test ok true "$key_mode"

    check "a measurement key file that is $key_mode is a refusal" test "$LAST_RC" -ne 0
    check "docker create was never invoked when the measurement key is $key_mode" calls_lack "create"
    check "the refusal for a $key_mode key names the script that creates it" \
        bash -c 'echo "$0" | grep -q "litellm-measurement-key.sh"' "$LAST_ERR"
done

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
