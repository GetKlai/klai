#!/usr/bin/env bash
# Tests for the portal-api deploy through the rollback contract
# (SPEC-INFRA-AUTO-ROLLBACK-001 S2, AT-a3): deploy-portal-api.sh runs the
# migrations and post-deploy SQL from the new image, then hands the container
# swap to deploy-with-rollback.sh. A red gate after the migrations brings the
# previous image back and leaves the schema where it is (rule R3).
#
# Both real scripts run; docker, compose-up.sh, df and curl are stubs, and every
# call lands in one events file so the order can be asserted.
#
# Run: bash deploy/scripts/tests/deploy-portal-api-rollback.test.sh

set -uo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
target="$script_dir/deploy-portal-api.sh"
IMAGE=ghcr.io/getklai/portal-api:abc123

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/bin"
failures=0

cat > "$work/bin/docker" <<'STUB'
#!/usr/bin/env bash
fx="$FIXTURES"
printf 'docker %s\n' "$*" >> "$fx/events"
case "$1" in
  compose)
    case "$2" in
      ps)  cat "$fx/running_cid"; exit 0 ;;
      run) exit 0 ;;
    esac ;;
  pull|tag|rm) exit 0 ;;
  image) exit 0 ;;
  inspect) echo "sha256:0123456789abcdef0123456789abcdef"; exit 0 ;;
  create) echo "extract1"; exit 0 ;;
  cp)
    mkdir -p "$3"
    echo "-- forward post-deploy" > "$3/post_deploy_aaa_forward.sql"
    echo "-- rollback script" > "$3/post_deploy_bbb_rollback_to_old.sql"
    exit 0 ;;
  exec) printf 'psql-stdin %s\n' "$(cat)" >> "$fx/events"; exit 0 ;;
  run)
    n=$(grep -c '^docker run ' "$fx/events")
    code=$(sed -n "${n}p" "$fx/probe_codes"); [[ -n "$code" ]] || code=$(tail -n1 "$fx/probe_codes")
    printf '%s' "$code"; exit 0 ;;
  ps) echo "newcontainer1"; exit 0 ;;
  logs) echo "container log line"; exit 0 ;;
esac
echo "stub: unexpected docker args: $*" >&2; exit 90
STUB
cat > "$work/compose-up.sh" <<'STUB'
#!/usr/bin/env bash
fx="$FIXTURES"
printf 'compose-up %s\n' "$*" >> "$fx/events"
n=$(grep -c '^compose-up ' "$fx/events")
rc=$(sed -n "${n}p" "$fx/compose_up_rcs"); [[ -n "$rc" ]] || rc=$(tail -n1 "$fx/compose_up_rcs")
exit "$rc"
STUB
# Room on every filesystem, so the disk guard steps aside.
cat > "$work/bin/df" <<'STUB'
#!/usr/bin/env bash
echo "Filesystem 1024-blocks Used Available Capacity Mounted on"
for _ in 1 2 3; do echo "/dev/md2 456519844 403000000 53519844 89% /"; done
STUB
cat > "$work/bin/curl" <<'STUB'
#!/usr/bin/env bash
printf 'curl %s\n' "$*" >> "$FIXTURES/events"
printf '302'
STUB
chmod +x "$work/bin/"* "$work/compose-up.sh"

# deploy-with-rollback.sh needs flock(1). Linux has it; elsewhere emulate
# `flock -w N 9` on the inherited descriptor.
if ! command -v flock >/dev/null 2>&1; then
    cat > "$work/bin/flock" <<'STUB'
#!/usr/bin/env python3
import fcntl, sys
fcntl.flock(int(sys.argv[-1]), fcntl.LOCK_EX)
STUB
    chmod +x "$work/bin/flock"
fi

# new_case <name> <compose-up rcs> <probe codes>
new_case() {
    fx="$work/$1"; mkdir -p "$fx/opt"
    export FIXTURES="$fx"
    printf '%s\n' $2 > "$fx/compose_up_rcs"
    printf '%s\n' $3 > "$fx/probe_codes"
    echo "oldcontainer1" > "$fx/running_cid"
    : > "$fx/events"
}

run_script() {
    (cd "$work" && PATH="$work/bin:$PATH" KLAI_COMPOSE_DIR="$fx/opt" KLAI_DEPLOY_LOCK_DIR="$fx" \
          KLAI_DOCKER_BIN="$work/bin/docker" KLAI_COMPOSE_UP="$work/compose-up.sh" \
          KLAI_PROBE_TIMEOUT=0 KLAI_PROBE_INTERVAL=0 GITHUB_SHA=abc123 \
          bash "$target" "$@" >"$fx/stdout" 2>"$fx/stderr")
    rc=$?
    ledger="$fx/opt/deploy-ledger.log"
}

check() {
    local desc="$1"; shift
    if "$@"; then echo "ok   - $desc"; else echo "FAIL - $desc"; failures=$((failures + 1)); fi
}
ledger_outcome() { awk -F'\t' 'END { print $7 }' "$ledger"; }
first_line() { grep -n -m1 -F -- "$1" "$fx/events" | cut -d: -f1; }
count() { grep -c -F -- "$1" "$fx/events"; }
before() { local a b; a=$(first_line "$1"); b=$(first_line "$2"); [[ -n "$a" && -n "$b" && "$a" -lt "$b" ]]; }

echo "# arguments: the exact sha is required (R1)"
new_case args "0" "200"
run_script
check "no image: exit 1" test "$rc" = 1
run_script ghcr.io/getklai/portal-api:latest
check ":latest: exit 1" test "$rc" = 1
check "nothing touched" test "$(wc -l < "$fx/events" | tr -d ' ')" = 0

echo "# green path"
new_case green "0" "200"
run_script "$IMAGE"
check "exit 0" test "$rc" = 0
check "ledger outcome deployed" test "$(ledger_outcome)" = deployed
check "exact sha pulled" grep -qxF "docker pull $IMAGE" "$fx/events"
check "migrations run from the new image, before the swap" before "docker tag $IMAGE ghcr.io/getklai/portal-api:latest" "docker compose run --rm --no-deps --entrypoint alembic portal-api upgrade head"
check "post-deploy SQL applied before the swap" before "psql-stdin -- forward post-deploy" "compose-up --no-pull portal-api"
check "rollback SQL skipped" test "$(count "psql-stdin -- rollback script")" = 0
check "probe on portal-api /health" grep -q '^docker run --rm --network klai-net .* http://portal-api:8010/health$' "$fx/events"
check "OIDC smoke check ran" test "$(count "curl ")" = 1

echo "# AT-a3: red gate after the migrations -> previous image, schema stays"
new_case a3 "1 0" "200"
run_script "$IMAGE"
check "exit 3" test "$rc" = 3
check "ledger outcome rolled_back" test "$(ledger_outcome)" = rolled_back
check "previous image retagged to the compose tag" grep -qxF "docker tag klai/portal-api:previous ghcr.io/getklai/portal-api:latest" "$fx/events"
check "previous recreated" grep -qxF "compose-up --no-pull --force-recreate portal-api" "$fx/events"
check "migrations ran once" test "$(count "alembic portal-api upgrade head")" = 1
check "no alembic downgrade" test "$(count "downgrade")" = 0
check "post-deploy SQL applied once, not reverted" test "$(count "psql-stdin")" = 1
check "no OIDC check after a rollback" test "$(count "curl ")" = 0

echo "# AT-a5: the rollback fails too -> exit 4, one attempt"
new_case a5 "1 1" "200"
run_script "$IMAGE"
check "exit 4" test "$rc" = 4
check "ledger outcome rollback_failed" test "$(ledger_outcome)" = rollback_failed
check "two compose-up calls, no loop" test "$(count "compose-up ")" = 2
check "no alembic downgrade" test "$(count "downgrade")" = 0

if (( failures > 0 )); then
    echo "$failures check(s) failed"
    exit 1
fi
echo "deploy-portal-api rollback: OK"
