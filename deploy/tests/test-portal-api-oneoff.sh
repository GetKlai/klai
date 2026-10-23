#!/usr/bin/env bash
# deploy/scripts/portal-api-oneoff.sh clones klai-core-portal-api-1 for a
# throwaway one-off container. This pins the parts a wrong clone would get
# silently wrong: the networks and bind mounts the live service depends on,
# the Sentry/GlitchTip variables a scratch container must not page on, and the
# refusal to clone a stopped service.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/portal-api-oneoff.sh"
FAIL=0

run_oneoff() {
    # $1 = "true"/"false" — what docker inspect reports for .State.Running
    # $2 = "abs" (default; --out <tmp>/out), "rel" (--out out, run from <tmp>/work)
    # $3 = "ok" (default), "missing" (no measurement key file), "live" (the file holds the live master key)
    # $4 = what the stub gateway answers the probe to klai-primary: "refused" (default, LiteLLM's key check),
    #      "open" (200: a wider key) or "budget" (402 from the router: the key check let it through)
    local running="$1"
    local out_mode="${2:-abs}"
    local key_mode="${3:-ok}"
    export ONEOFF_TEST_GATEWAY="${4:-refused}"
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/bin" "$tmp/out" "$tmp/work"

    export KLAI_MEASUREMENT_KEY_FILE="$tmp/measurement-key"
    case "$key_mode" in
        ok) printf 'measurement-test-key' >"$KLAI_MEASUREMENT_KEY_FILE" ;;
        live) printf 'live-master-key' >"$KLAI_MEASUREMENT_KEY_FILE" ;;
    esac

    export ONEOFF_TEST_TMP="$tmp"
    export ONEOFF_TEST_RUNNING="$running"
    # `docker exec ... id -u` reports the uid the real container runs as; the
    # stub reports our own uid so the script's `chown` needs no privilege.
    export ONEOFF_TEST_UID="$(id -u)"

    cat >"$tmp/bin/curl" <<'STUB'
#!/usr/bin/env bash
cat >> "$ONEOFF_TEST_TMP/curl-config"
case "$ONEOFF_TEST_GATEWAY" in
    refused) printf '{"error": {"message": "denied", "type": "key_model_access_denied", "param": "model", "code": "403"}}\n403' ;;
    open) printf '{"id": "chatcmpl-1", "choices": []}\n200' ;;
    budget) printf '{"error": {"message": "spent", "type": "budget_exceeded", "code": "402"}}\n402' ;;
esac
STUB
    chmod +x "$tmp/bin/curl"

    cat >"$tmp/bin/docker" <<'STUB'
#!/usr/bin/env bash
echo "$*" >> "$ONEOFF_TEST_TMP/calls.log"
case "$1" in
    inspect)
        case "$*" in
            *'.IPAddress'*)
                echo "172.18.0.99"
                ;;
            *'{{.State.Running}}'*)
                echo "$ONEOFF_TEST_RUNNING"
                ;;
            *'{{.Image}}'*)
                echo "sha256:deadbeef"
                ;;
            *'{{range .Config.Env}}'*)
                printf 'FOO=bar\nSENTRY_DSN=https://example.com/1\nGLITCHTIP_DSN=https://example.com/2\nLITELLM_MASTER_KEY=live-master-key\nLITELLM_GENERAL_CHAT_KEY=live-chat-key\nLITELLM_URL=http://litellm:4000\n'
                ;;
            *'NetworkSettings.Networks'*)
                printf 'klai-core_default\nklai-core_llm\n'
                ;;
            *'.Mounts'*)
                printf 'bind|/opt/klai/data|/data|false\nvolume|klai_somevol|/var/lib/x|true\nbind|/opt/klai/librechat|/librechat|true\n'
                ;;
        esac
        ;;
    exec)
        printf '%s\n' "$ONEOFF_TEST_UID"
        ;;
    run)
        echo "$*" >> "$ONEOFF_TEST_TMP/run.log"
        args=("$@")
        for ((i = 0; i < ${#args[@]}; i++)); do
            if [ "${args[$i]}" = "--env-file" ]; then
                cp "${args[$((i + 1))]}" "$ONEOFF_TEST_TMP/run-env-file"
            fi
        done
        ;;
esac
exit 0
STUB
    chmod +x "$tmp/bin/docker"

    set +e
    if [ "$out_mode" = "rel" ]; then
        # A relative --out has to resolve against the cwd the operator ran
        # the script from, not against $tmp.
        (cd "$tmp/work" && PATH="$tmp/bin:$PATH" bash "$SCRIPT" --out out -- echo hello >"$tmp/out.log" 2>"$tmp/err.log")
    else
        PATH="$tmp/bin:$PATH" bash "$SCRIPT" --out "$tmp/out" -- echo hello >"$tmp/out.log" 2>"$tmp/err.log"
    fi
    LAST_RC=$?
    set -e

    LAST_RUN_LOG="$(cat "$tmp/run.log" 2>/dev/null || true)"
    LAST_CALLS="$(cat "$tmp/calls.log" 2>/dev/null || true)"
    LAST_ENV_FILE="$tmp/run-env-file"
    LAST_ERR="$(cat "$tmp/err.log" 2>/dev/null || true)"
    LAST_TMP="$tmp"
    LAST_CURL_CONFIG="$(cat "$tmp/curl-config" 2>/dev/null || true)"

    unset ONEOFF_TEST_TMP ONEOFF_TEST_RUNNING ONEOFF_TEST_UID KLAI_MEASUREMENT_KEY_FILE ONEOFF_TEST_GATEWAY
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

echo "── portal-api one-off clone guard ──"

run_oneoff true

check "docker run was invoked" \
    test -n "$LAST_RUN_LOG"

check "both service networks are attached" \
    bash -c 'echo "$0" | grep -q -- "--network klai-core_default" && echo "$0" | grep -q -- "--network klai-core_llm"' "$LAST_RUN_LOG"

check "the bind mount is carried over with its read-only flag" \
    bash -c 'echo "$0" | grep -q -- "--mount type=bind,source=/opt/klai/data,destination=/data,readonly"' "$LAST_RUN_LOG"

check "the LibreChat directory with every tenant's LiteLLM key is NOT cloned" \
    bash -c '! echo "$0" | grep -q -- "/librechat"' "$LAST_RUN_LOG"

check "the probe asks the gateway for klai-primary with the key from the file, on stdin" \
    bash -c 'echo "$0" | grep -q "Bearer measurement-test-key" && echo "$0" | grep -q "172.18.0.99:4000/v1/chat/completions" && echo "$0" | grep -q "klai-primary"' "$LAST_CURL_CONFIG"

check "a named volume mount is NOT cloned (only bind mounts are)" \
    bash -c '! echo "$0" | grep -q -- "klai_somevol"' "$LAST_RUN_LOG"

check "the host output directory is mounted at /out" \
    bash -c 'echo "$0" | grep -q -- "--mount type=bind,source=${1}/out,destination=/out"' "$LAST_RUN_LOG" "$LAST_TMP"

check "the container is unmanaged: --rm, a klai-oneoff name and label" \
    bash -c 'echo "$0" | grep -q -- "--rm" && echo "$0" | grep -Eq -- "--name klai-oneoff-[0-9]+" && echo "$0" | grep -q -- "--label klai.oneoff=true"' "$LAST_RUN_LOG"

check "the command runs from the backend working directory" \
    bash -c 'echo "$0" | grep -q -- "-w /repo/klai-portal/backend"' "$LAST_RUN_LOG"

check "the cloned image and the command are both on the docker run line" \
    bash -c 'echo "$0" | grep -q -- "sha256:deadbeef echo hello"' "$LAST_RUN_LOG"

check "the image's own entrypoint (migrate-then-serve) is cleared, or the command never runs" \
    bash -c 'echo "$0" | grep -Eq -- "--entrypoint[[:space:]]+sha256:deadbeef"' "$LAST_RUN_LOG"

check "the Sentry DSN is dropped from the cloned environment" \
    bash -c '! grep -q "^SENTRY_DSN=" "$0"' "$LAST_ENV_FILE"

check "the GlitchTip DSN is dropped from the cloned environment" \
    bash -c '! grep -q "^GLITCHTIP_DSN=" "$0"' "$LAST_ENV_FILE"

check "an unrelated variable survives the clone" \
    bash -c 'grep -q "^FOO=bar$" "$0"' "$LAST_ENV_FILE"

check "every LiteLLM key in the clone is the measurement key, none is a live one" \
    bash -c 'grep -q "^LITELLM_MASTER_KEY=measurement-test-key$" "$0" && grep -q "^LITELLM_GENERAL_CHAT_KEY=measurement-test-key$" "$0" && ! grep -q "live-" "$0"' "$LAST_ENV_FILE"

check "a LiteLLM setting that is not a key is left as it is" \
    bash -c 'grep -q "^LITELLM_URL=http://litellm:4000$" "$0"' "$LAST_ENV_FILE"

check "the output dir is chowned to the uid the image's process actually runs as" \
    bash -c 'echo "$0" | grep -q -- "exec klai-core-portal-api-1 id -u"' "$LAST_CALLS"

run_oneoff true rel

check "a relative --out resolves to an absolute path before the mount" \
    bash -c 'echo "$0" | grep -q -- "--mount type=bind,source=${1}/work/out,destination=/out"' "$LAST_RUN_LOG" "$LAST_TMP"

run_oneoff false

check "a stopped service is refused" \
    test "$LAST_RC" -ne 0

check "docker run was never invoked against a stopped service" \
    test -z "$LAST_RUN_LOG"

check "the refusal says why" \
    bash -c 'echo "$0" | grep -qi "not running"' "$LAST_ERR"

for key_mode in missing live; do
    run_oneoff true abs "$key_mode"

    check "a measurement key file that is $key_mode is a refusal" \
        test "$LAST_RC" -ne 0

    check "docker run was never invoked when the measurement key is $key_mode" \
        test -z "$LAST_RUN_LOG"

    check "the refusal for a $key_mode key names the script that creates it" \
        bash -c 'echo "$0" | grep -q "litellm-measurement-key.sh"' "$LAST_ERR"
done

for gateway in open budget; do
    run_oneoff true abs ok "$gateway"

    check "a key the gateway does not refuse klai-primary ($gateway) is a refusal" \
        test "$LAST_RC" -ne 0

    check "docker run was never invoked when the probe got '$gateway'" \
        test -z "$LAST_RUN_LOG"

    check "the refusal for '$gateway' says the key check did not refuse klai-primary" \
        bash -c 'echo "$0" | grep -q "did not refuse klai-primary"' "$LAST_ERR"
done

echo "─────────────────────────────"
if [ "$FAIL" -eq 0 ]; then
    echo "portal-api one-off clone guard: OK"
else
    echo "portal-api one-off clone guard: FAILED" >&2
fi
exit "$FAIL"
