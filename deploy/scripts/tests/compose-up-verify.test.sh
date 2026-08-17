#!/usr/bin/env bash
# Tests for the post-recreate verification in compose-up.sh.
#
# No Docker daemon needed: compose-up.sh takes its docker binary from
# KLAI_DOCKER_BIN and its compose directory from KLAI_COMPOSE_DIR, so each case
# drives a stub docker that answers from fixture files.
#
# Run: bash deploy/scripts/tests/compose-up-verify.test.sh

set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
target="$script_dir/compose-up.sh"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

failures=0
case_dir=""

# The stub answers only what compose-up.sh actually asks. Anything else is a
# hard error rather than a silent 0, so a future call site cannot pass these
# tests by accident.
write_stub() {
    cat > "$work/docker" <<'STUB'
#!/usr/bin/env bash
set -u
fx="$FIXTURES"

read_fx() { [[ -f "$fx/$1" ]] && cat "$fx/$1" || echo ""; }

case "$1" in
  compose)
    shift
    case "$1" in
      pull) exit 0 ;;
      ps)
        # After `up` the service may be serving a different container; a case
        # expresses that by providing cid_post.
        if [[ -f "$fx/up_called" && -s "$fx/cid_post" ]]; then ids="$(read_fx cid_post)"
        else ids="$(read_fx cid)"; fi
        # Real `compose ps -q` lists RUNNING containers only; -a lifts that.
        # Without this distinction the stub would happily hand back a
        # created-or-exited container to a -q call and hide the whole reason
        # this code uses -aq.
        if [[ " $* " == *" -a"* ]]; then
            printf '%s\n' "$ids"
        else
            while IFS= read -r one; do
                [[ -n "$one" ]] || continue
                [[ "$(read_fx "status_$one")" == "running" ]] && printf '%s\n' "$one"
            done <<< "$ids"
        fi
        exit 0 ;;
      up)
        # Record argv so a dropped --force-recreate / --no-deps is a test
        # failure rather than an invisible regression.
        printf '%s\n' "$*" >> "$fx/up_argv"
        : > "$fx/up_called"
        echo "$(read_fx up_stderr)" >&2; exit "$(read_fx up_rc)" ;;
    esac
    echo "stub: unexpected compose args: $*" >&2; exit 90
    ;;
  inspect)
    cid="$2"; tmpl="$4"
    [[ -n "$(read_fx cid)" ]] || exit 1
    case "$tmpl" in
      '{{.Created}}')       read_fx "created_$cid" ;;
      '{{.Name}}')          read_fx "name_$cid" ;;
      '{{.State.Status}}')  read_fx "status_$cid" ;;
      '{{.RestartCount}}')
        # One line per poll, so a crash loop can be expressed as 0,1.
        n=$(cat "$fx/restart_calls" 2>/dev/null || echo 0)
        echo $((n + 1)) > "$fx/restart_calls"
        sed -n "$((n + 1))p" "$fx/restarts" 2>/dev/null || read_fx restarts
        ;;
      *Health*)             read_fx health ;;
      *) echo "stub: unexpected inspect template: $tmpl" >&2; exit 90 ;;
    esac
    exit 0
    ;;
  ps)     read_fx name_taken; exit 0 ;;
  rename) printf '%s -> %s\n' "$2" "$3" >> "$fx/renames"; exit 0 ;;
  logs)   echo "stub container log line"; exit 0 ;;
  pull)   exit 0 ;;
esac
echo "stub: unexpected docker args: $*" >&2
exit 90
STUB
    chmod +x "$work/docker"
}

new_case() {
    case_dir="$work/$1"
    rm -rf "$case_dir"
    mkdir -p "$case_dir/fixtures"
    # Minimal compose file: no vexaai/ refs (skips the runtime pre-pull) and no
    # USE_PRISMA_MIGRATE (skips the litellm baseline check).
    printf 'services:\n  demo:\n    image: demo:latest\n' > "$case_dir/docker-compose.yml"
    : > "$case_dir/fixtures/up_rc"; echo 0 > "$case_dir/fixtures/up_rc"
    echo "cid123" > "$case_dir/fixtures/cid"
    echo "0" > "$case_dir/fixtures/restarts"
    printf '/%s\n' "klai-core-demo-1" > "$case_dir/fixtures/name_cid123"
    echo "running" > "$case_dir/fixtures/status_cid123"
    echo "2026-08-14T10:00:00Z" > "$case_dir/fixtures/created_cid123"
    : > "$case_dir/fixtures/health"
    : > "$case_dir/fixtures/name_taken"
    : > "$case_dir/fixtures/cid_post"
}

fx() { printf '%s\n' "$2" > "$case_dir/fixtures/$1"; }

run_target() {
    set +e
    KLAI_COMPOSE_DIR="$case_dir" \
    KLAI_DOCKER_BIN="$work/docker" \
    FIXTURES="$case_dir/fixtures" \
    KLAI_VERIFY_POLLS=2 \
    KLAI_VERIFY_INTERVAL=0 \
        bash "$target" "$@" > "$case_dir/out" 2>&1
    rc=$?
    set -e
}

check() {
    if [[ "$2" == "$3" ]]; then
        echo "  ok   $1"
    else
        echo "  FAIL $1 (expected $2, got $3)"
        echo "       --- output ---"
        sed 's/^/       /' "$case_dir/out"
        failures=$((failures + 1))
    fi
}

check_output() {
    if grep -qF "$2" "$case_dir/out"; then
        echo "  ok   $1"
    else
        echo "  FAIL $1 (output did not contain: $2)"
        sed 's/^/       /' "$case_dir/out"
        failures=$((failures + 1))
    fi
}

write_stub
echo "compose-up.sh post-recreate verification"

new_case happy
run_target demo
check "green compose + running container -> 0" 0 "$rc"
check_output "  reports no healthcheck" "no healthcheck defined"

# The knowledge-ingest case: stop_grace_period 90s + a busy procrastinate queue
# makes compose abort on the removal race, but the replacement IS there.
new_case compose_fails_container_replaced
fx up_rc 1
fx up_stderr "Error response from daemon: removal of container 3b375ef8 is already in progress"
fx cid_post "new456"
printf '/%s\n' "klai-core-demo-1" > "$case_dir/fixtures/name_new456"
echo "running"              > "$case_dir/fixtures/status_new456"
echo "2026-08-14T11:00:00Z" > "$case_dir/fixtures/created_new456"
run_target demo
check "compose exit 1 + container REPLACED -> 0" 0 "$rc"
check_output "  names both container ids" "cid123 -> new456"
# The run is GREEN in this case, so the only thing that makes shipping through
# a compose failure visible is the annotation on the run summary. Drop the
# prefix and it becomes a line in a log nobody opens.
check_output "  surfaces as a workflow annotation" "::warning::"

# The dangerous twin, and the reason the override is narrow. An unresolvable
# image tag or a dependency that will not start makes compose abort BEFORE
# replacing anything, so the old container is still happily running. Calling
# that green would ship nothing and say it shipped.
new_case compose_fails_container_unchanged
fx up_rc 1
fx up_stderr "Error failed to resolve reference ghcr.io/getklai/nope:bad"
run_target demo
check "compose exit 1 + same container -> 1" 1 "$rc"
check_output "  says the deploy shipped nothing" "shipped nothing"
check_output "  surfaces as a workflow annotation" "::error::"

new_case compose_fails_no_container
fx up_rc 1
fx cid ""
run_target demo
check "compose exit 1 + no container -> 1" 1 "$rc"
check_output "  names both facts" "did not come up"

# The inverse: `up -d` returns 0 as soon as the container is created, so an
# image that dies on boot exits 0 here. Verification is what catches it.
new_case compose_green_container_exited
fx status_cid123 "exited"
run_target demo
check "compose exit 0 + container exited -> 1" 1 "$rc"
check_output "  dumps container logs" "stub container log line"
check_output "  puts the reason on the run summary" "::error::demo is 'exited'"

new_case crash_loop
printf '0\n1\n' > "$case_dir/fixtures/restarts"
run_target demo
check "restart count rising -> 1" 1 "$rc"
check_output "  calls it a crash loop" "crash loop"
check_output "  puts the reason on the run summary" "::error::"

new_case unhealthy
fx health "unhealthy"
run_target demo
check "healthcheck unhealthy -> 1" 1 "$rc"

new_case health_starting
fx health "starting"
run_target demo
check "healthcheck still starting -> 0" 0 "$rc"
check_output "  does not wait on it" "not waited on"

new_case mangled_name
fx name_cid123 "/3b375ef812c7_klai-core-demo-1"
run_target demo
check "half-finished recreate name -> 0" 0 "$rc"
check_output "  renames back to canonical" "Renamed 3b375ef812c7_klai-core-demo-1 back to klai-core-demo-1"

new_case mangled_name_taken
fx name_cid123 "/3b375ef812c7_klai-core-demo-1"
fx name_taken "otherid"
run_target demo
check "canonical name occupied -> 0" 0 "$rc"
check_output "  refuses to rename over it" "is taken"
if [[ -f "$case_dir/fixtures/renames" ]]; then
    echo "  FAIL renamed over an occupied name"
    failures=$((failures + 1))
else
    echo "  ok   no rename attempted"
fi

# A half-finished recreate can leave both containers labelled for the service,
# and `compose ps -q` lists both. Verifying the older one means reporting the
# corpse instead of the replacement.
new_case two_containers_picks_newest
printf 'old123\nnew456\n' > "$case_dir/fixtures/cid"
printf '/%s\n' "klai-core-demo-1" > "$case_dir/fixtures/name_old123"
echo "exited"               > "$case_dir/fixtures/status_old123"
echo "2026-08-14T10:00:00Z" > "$case_dir/fixtures/created_old123"
printf '/%s\n' "3b375ef812c7_klai-core-demo-1" > "$case_dir/fixtures/name_new456"
echo "running"              > "$case_dir/fixtures/status_new456"
echo "2026-08-14T11:00:00Z" > "$case_dir/fixtures/created_new456"
run_target demo
check "two containers -> verifies the newest" 0 "$rc"

# Docker emits RFC3339Nano with trailing zeros stripped, so ".5Z" and
# ".5000001Z" both occur and a plain string compare puts them in the wrong
# order ('Z' sorts above digits). Here the OLD container has the shorter
# fraction and is exited: get the ordering wrong and the run fails.
new_case fraction_length_ordering
printf 'old123\nnew456\n' > "$case_dir/fixtures/cid"
printf '/%s\n' "klai-core-demo-1" > "$case_dir/fixtures/name_old123"
echo "exited"                    > "$case_dir/fixtures/status_old123"
echo "2026-08-14T10:00:00.5Z"    > "$case_dir/fixtures/created_old123"
printf '/%s\n' "klai-core-demo-1" > "$case_dir/fixtures/name_new456"
echo "running"                     > "$case_dir/fixtures/status_new456"
echo "2026-08-14T10:00:00.5000001Z" > "$case_dir/fixtures/created_new456"
run_target demo
check "ragged nanosecond fractions -> still picks the newest" 0 "$rc"

# The flags are the whole point of two call sites: litellm needs
# --force-recreate to drop its Python module cache, docs-app needs --no-deps.
# Dropping one is silent in production, so it has to be loud here.
new_case flag_force_recreate
run_target --force-recreate demo
check "--force-recreate accepted" 0 "$rc"
if grep -q -- "--force-recreate" "$case_dir/fixtures/up_argv"; then
    echo "  ok     passed --force-recreate through to compose"
else
    echo "  FAIL   --force-recreate never reached compose: $(cat "$case_dir/fixtures/up_argv")"
    failures=$((failures + 1))
fi

new_case flag_no_deps
run_target --no-deps demo
check "--no-deps accepted" 0 "$rc"
if grep -q -- "--no-deps" "$case_dir/fixtures/up_argv"; then
    echo "  ok     passed --no-deps through to compose"
else
    echo "  FAIL   --no-deps never reached compose: $(cat "$case_dir/fixtures/up_argv")"
    failures=$((failures + 1))
fi

new_case flag_none
run_target demo
if grep -qE -- "--force-recreate|--no-deps" "$case_dir/fixtures/up_argv"; then
    echo "  FAIL   invented a flag nobody asked for: $(cat "$case_dir/fixtures/up_argv")"
    failures=$((failures + 1))
else
    echo "  ok   plain invocation passes neither flag"
fi

# Without a service argument there is no single container to verify, so the
# pre-existing contract (compose decides) has to survive untouched.
new_case bulk_compose_fails
fx up_rc 1
run_target
check "no service arg + compose failure -> non-zero" 1 "$rc"

echo
if [[ "$failures" -eq 0 ]]; then
    echo "compose-up-verify: all cases passed"
else
    echo "compose-up-verify: $failures case(s) failed"
    exit 1
fi
