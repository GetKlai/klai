#!/usr/bin/env bash
# deploy/scripts/portal-api-oneoff.sh clones klai-core-portal-api-1 for a
# throwaway one-off container. This pins the parts a wrong clone would get
# silently wrong: the networks and bind mounts the live service depends on,
# the Sentry/GlitchTip variables a scratch container must not page on, and the
# refusal to clone a stopped service. With --measurement it also holds the
# shared lock compose-up.sh and tenant-config-regenerate.sh wait on, for as
# long as the container runs.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/portal-api-oneoff.sh"
FAIL=0

run_oneoff() {
    # $1 = "true"/"false" — what docker inspect reports for .State.Running
    # $2 = "abs" (default; --out <tmp>/out), "rel" (--out out, run from <tmp>/work)
    # $3 = an extra option before --out (--measurement)
    local running="$1"
    local out_mode="${2:-abs}"
    local extra=()
    if [ -n "${3:-}" ]; then extra=("$3"); fi
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/bin" "$tmp/out" "$tmp/work"

    export ONEOFF_TEST_TMP="$tmp"
    export ONEOFF_TEST_RUNNING="$running"
    # `docker exec ... id -u` reports the uid the real container runs as; the
    # stub reports our own uid so the script's `chown` needs no privilege.
    export ONEOFF_TEST_UID="$(id -u)"
    export KLAI_MEASUREMENT_LOCK="$tmp/.measurement.lock"

    # A real flock(2) on the descriptor the script hands over, so the lock
    # outlives this stub exactly as it outlives flock(1): it belongs to the
    # script's open file, not to this process. Python, because macOS ships no
    # flock binary.
    cat >"$tmp/bin/flock" <<'STUB'
#!/usr/bin/env python3
import fcntl, os, sys
args = sys.argv[1:]
with open(os.path.join(os.environ["ONEOFF_TEST_TMP"], "flock.log"), "a") as log:
    log.write(" ".join(args) + "\n")
mode = fcntl.LOCK_SH if "-s" in args else fcntl.LOCK_EX
try:
    fcntl.flock(int(args[-1]), mode | (fcntl.LOCK_NB if "-n" in args else 0))
except BlockingIOError:
    sys.exit(1)
STUB
    chmod +x "$tmp/bin/flock"

    cat >"$tmp/bin/docker" <<'STUB'
#!/usr/bin/env bash
echo "$*" >> "$ONEOFF_TEST_TMP/calls.log"
case "$1" in
    inspect)
        case "$*" in
            *'{{.State.Running}}'*)
                echo "$ONEOFF_TEST_RUNNING"
                ;;
            *'{{.Image}}'*)
                echo "sha256:deadbeef"
                ;;
            *'{{range .Config.Env}}'*)
                printf 'FOO=bar\nSENTRY_DSN=https://example.com/1\nGLITCHTIP_DSN=https://example.com/2\n'
                ;;
            *'NetworkSettings.Networks'*)
                printf 'klai-core_default\nklai-core_llm\n'
                ;;
            *'.Mounts'*)
                printf 'bind|/opt/klai/data|/data|false\nvolume|klai_somevol|/var/lib/x|true\n'
                ;;
        esac
        ;;
    exec)
        printf '%s\n' "$ONEOFF_TEST_UID"
        ;;
    run)
        echo "$*" >> "$ONEOFF_TEST_TMP/run.log"
        # What a deploy asking for the lock would find while the container runs.
        python3 -c '
import fcntl, os, sys
if not os.path.exists(sys.argv[1]):
    print("no lock file")
    sys.exit()
try:
    fcntl.flock(open(sys.argv[1]), fcntl.LOCK_EX | fcntl.LOCK_NB)
    print("free")
except BlockingIOError:
    print("held")' "$KLAI_MEASUREMENT_LOCK" > "$ONEOFF_TEST_TMP/lock-during-run"
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
        (cd "$tmp/work" && PATH="$tmp/bin:$PATH" bash "$SCRIPT" ${extra[@]+"${extra[@]}"} --out out -- echo hello >"$tmp/out.log" 2>"$tmp/err.log")
    else
        PATH="$tmp/bin:$PATH" bash "$SCRIPT" ${extra[@]+"${extra[@]}"} --out "$tmp/out" -- echo hello >"$tmp/out.log" 2>"$tmp/err.log"
    fi
    LAST_RC=$?
    set -e

    LAST_RUN_LOG="$(cat "$tmp/run.log" 2>/dev/null || true)"
    LAST_CALLS="$(cat "$tmp/calls.log" 2>/dev/null || true)"
    LAST_ENV_FILE="$tmp/run-env-file"
    LAST_ERR="$(cat "$tmp/err.log" 2>/dev/null || true)"
    LAST_TMP="$tmp"
    LAST_FLOCK="$(cat "$tmp/flock.log" 2>/dev/null || true)"
    LAST_LOCK_DURING_RUN="$(cat "$tmp/lock-during-run" 2>/dev/null || true)"

    unset ONEOFF_TEST_TMP ONEOFF_TEST_RUNNING ONEOFF_TEST_UID KLAI_MEASUREMENT_LOCK
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

check "the output dir is chowned to the uid the image's process actually runs as" \
    bash -c 'echo "$0" | grep -q -- "exec klai-core-portal-api-1 id -u"' "$LAST_CALLS"

check "without --measurement no lock is taken" \
    bash -c 'test -z "$0" && test "$1" = "no lock file"' "$LAST_FLOCK" "$LAST_LOCK_DURING_RUN"

run_oneoff true abs --measurement

check "--measurement takes a shared lock, so rounds can run side by side" \
    bash -c 'echo "$0" | grep -Eq -- "(^| )-s( |$)"' "$LAST_FLOCK"

# A round runs as root, a deploy as klai: a 0600 file would lock the deploy out.
check "--measurement creates the lock file when it is missing, readable by a deploy of another user" \
    python3 -c 'import os, sys; sys.exit(os.stat(sys.argv[1]).st_mode & 0o777 != 0o644)' \
    "$LAST_TMP/.measurement.lock"

check "--measurement holds the lock against a deploy while the container runs" \
    test "$LAST_LOCK_DURING_RUN" = "held"

check "the lock is released once the container has exited" \
    python3 -c 'import fcntl, sys; fcntl.flock(open(sys.argv[1]), fcntl.LOCK_EX | fcntl.LOCK_NB)' \
    "$LAST_TMP/.measurement.lock"

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

echo "─────────────────────────────"
if [ "$FAIL" -eq 0 ]; then
    echo "portal-api one-off clone guard: OK"
else
    echo "portal-api one-off clone guard: FAILED" >&2
fi
exit "$FAIL"
