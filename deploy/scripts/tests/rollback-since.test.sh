#!/usr/bin/env bash
# Tests for rollback-since.sh (SPEC-INFRA-AUTO-ROLLBACK-001 section 6, AT-6).
#
# No Docker: docker is a stub that keeps the running image per service in
# fixtures/running.<svc>, deploy-with-rollback.sh is a stub that records its call
# and, when it "rolls back", makes the image it was given the running one. The
# compose replay runs the real compose-rollback.sh against stub compose-up.sh and
# compose-env-drift-recreate.sh in each case's scripts/ directory.
#
# Run: bash deploy/scripts/tests/rollback-since.test.sh

set -uo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
target="$script_dir/rollback-since.sh"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

failures=0
d=""
SINCE="2026-10-28T04:00:00Z"
UNTIL="2026-10-28T23:00:00Z"

# Records "<service> <image> <args> sha=<GITHUB_SHA> profiles=<COMPOSE_PROFILES>"
# and the path it was called as, and exits with the code in fixtures/rc.<service>
# (default 3, after which <image> is the running one).
cat > "$work/deploy" <<'STUB'
#!/usr/bin/env bash
fx="$KLAI_COMPOSE_DIR/fixtures"
echo "$1 $2 ${*:3} sha=${GITHUB_SHA:-} profiles=${COMPOSE_PROFILES:-}" >> "$fx/deploys"
echo "deploy $1" >> "$fx/calls"
echo "$0" >> "$fx/deploy_paths"
rc="$(cat "$fx/rc.$1" 2>/dev/null || echo 3)"
[[ "$rc" != 3 ]] || echo "$2" > "$fx/running.$1"
exit "$rc"
STUB
# compose ps -q <svc> | inspect --format {{.Image}} <cid> | image inspect --format {{.Id}} <ref>
cat > "$work/docker" <<'STUB'
#!/usr/bin/env bash
fx="$KLAI_COMPOSE_DIR/fixtures"
last="${*: -1}"
case "$1" in
  compose) [[ -f "$fx/running.$last" ]] && echo "cid-$last"; exit 0 ;;
  inspect) cat "$fx/running.${last#cid-}" 2>/dev/null; exit 0 ;;
  image) awk -v r="$last" '$1 == r { print $2 }' "$fx/images" 2>/dev/null; exit 0 ;;
esac
echo "stub: unexpected docker args: $*" >&2; exit 90
STUB
chmod +x "$work/deploy" "$work/docker"

# new_case <name> <ledger lines...>: a host with that ledger and a compose state
# in which the last sync (commit ccc333, from bbb222) kept the Caddyfile.
new_case() {
    d="$work/$1"
    shift
    mkdir -p "$d/fixtures" "$d/scripts" "$d/caddy"
    : > "$d/fixtures/deploys"
    : > "$d/fixtures/calls"
    printf '%s\n' "$@" > "$d/deploy-ledger.log"
    echo "new caddyfile" > "$d/caddy/Caddyfile"
    echo "old caddyfile" > "$d/caddy/Caddyfile.prev"
    echo "ccc333" > "$d/.compose-commit"
    printf 'from=bbb222\nto=ccc333\nkept=%s\ntouched=caddy\n' "$d/caddy/Caddyfile" > "$d/.compose-prev-state"
    printf '#!/usr/bin/env bash\necho "compose-up $*" >> "$KLAI_COMPOSE_DIR/fixtures/calls"\n' > "$d/scripts/compose-up.sh"
    printf '#!/usr/bin/env bash\necho sweep >> "$KLAI_COMPOSE_DIR/fixtures/calls"\n' > "$d/scripts/compose-env-drift-recreate.sh"
    chmod +x "$d"/scripts/*.sh
}

run() {
    out=$(KLAI_COMPOSE_DIR="$d" KLAI_DEPLOY_WITH_ROLLBACK="$work/deploy" KLAI_DOCKER_BIN="$work/docker" \
          KLAI_COMPOSE_ROLLBACK="$script_dir/compose-rollback.sh" bash "$target" "$@" 2>&1)
    rc=$?
}

check() {
    local desc="$1"; shift
    if "$@"; then echo "ok   - $desc"; else echo "FAIL - $desc"; echo "$out" | sed 's/^/       /'; failures=$((failures + 1)); fi
}
report() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]])' "$d/deploy-reports/e2e-rollback.json" "$1"; }
deploys() { cat "$d/fixtures/deploys"; }
T=$'\t'
line() { local IFS=$'\t'; echo "$*"; }

echo "# two services deployed after the last green run"
new_case two \
    "$(line 2026-10-28T03:00:00Z image retrieval-api aaaaaaaaaaa0 ghcr.io/getklai/retrieval-api:s0 s0 deployed)" \
    "$(line 2026-10-28T05:00:00Z image retrieval-api aaaaaaaaaaa1 ghcr.io/getklai/retrieval-api:s1 s1 deployed)" \
    "$(line 2026-10-28T05:10:00Z image klai-mailer mmmmmmmmmmm1 ghcr.io/getklai/klai-mailer:s2 s2 deployed)" \
    "$(line 2026-10-28T05:20:00Z image retrieval-api bbbbbbbbbbb2 ghcr.io/getklai/retrieval-api:s3 s3 deployed)" \
    "$(line 2026-10-28T05:30:00Z image klai-mailer xxxxxxxxxxx3 ghcr.io/getklai/klai-mailer:s4 s4 rolled_back)" \
    "$(line 2026-10-28T06:00:00Z image scribe-api sssssssssss1 ghcr.io/getklai/scribe-api:s5 s5 deployed)"
mkdir -p "$d/deploy-reports"
echo '{"service":"retrieval-api","outcome":"deployed","probe":"http://retrieval-api:8040/health","probe_network":"klai-net"}' > "$d/deploy-reports/retrieval-api.json"
echo "sha256:img-s3" > "$d/fixtures/running.retrieval-api"
echo "ghcr.io/getklai/retrieval-api:s3 sha256:img-s3" > "$d/fixtures/images"
run "$SINCE" --until 2026-10-28T05:45:00Z
check "exit 3" test "$rc" = 3
check "newest deploy first, each back to the image that ran at the last green run, with the recorded probe" \
    test "$(deploys)" = "retrieval-api aaaaaaaaaaa1 --rollback e2e --probe http://retrieval-api:8040/health --probe-network klai-net sha=s3 profiles=
klai-mailer mmmmmmmmmmm1 --rollback e2e sha=s2 profiles="
check "a deploy after the red run was created is left alone" bash -c "! grep -q scribe-api '$d/fixtures/deploys'"
check "running image: the newest deploy before, the last-green image after" test "$(cat "$d/fixtures/running.retrieval-api")" = aaaaaaaaaaa1
check "the scripts it calls are copies, not the files a compose restore rewrites" bash -c "! grep -qxF '$work/deploy' '$d/fixtures/deploy_paths'"
check "report outcome rolled_back" test "$(report outcome)" = rolled_back
check "report names both services" test "$(report service)" = "retrieval-api klai-mailer"
check "report gate e2e" test "$(report gate)" = e2e
check "report printed for the workflow" grep -q '^ROLLBACK_REPORT {.*"outcome":"rolled_back"' <<< "$out"
check "output kept on disk" grep -q 'back to aaaaaaaaaaa1' "$d/deploy-reports/e2e-rollback.log"
check "replay time recorded" test -s "$d/.e2e-rollback-at"

echo "# the same red state again: no second rollback (R6)"
: > "$d/fixtures/deploys"
rm "$d/deploy-reports/e2e-rollback.json"
run "$SINCE" --until 2026-10-28T05:45:00Z
check "exit 0" test "$rc" = 0
check "nothing rolled back" test ! -s "$d/fixtures/deploys"
check "no report" test ! -e "$d/deploy-reports/e2e-rollback.json"

echo "# AT-6: nothing deployed since the last green run"
new_case empty "$(line 2026-10-28T03:00:00Z image retrieval-api aaaaaaaaaaa0 ghcr.io/getklai/retrieval-api:s0 s0 deployed)"
run "$SINCE" --until "$UNTIL"
check "exit 0" test "$rc" = 0
check "nothing rolled back" test ! -s "$d/fixtures/deploys"
check "no report" test ! -e "$d/deploy-reports/e2e-rollback.json"
check "no replay time recorded" test ! -e "$d/.e2e-rollback-at"

echo "# no ledger: cannot tell what was deployed"
new_case noledger
rm "$d/deploy-ledger.log"
run "$SINCE" --until "$UNTIL"
check "exit 4" test "$rc" = 4
check "report rollback_failed, gate e2e-no-ledger" test "$(report outcome) $(report gate)" = "rollback_failed e2e-no-ledger"
check "event written" grep -q '"gate":"e2e-no-ledger"' "$d/logs/auto-rollback.log"

echo "# a later deploy is already live"
new_case newer "$(line 2026-10-28T05:00:00Z image retrieval-api aaaaaaaaaaa1 ghcr.io/getklai/retrieval-api:s1 s1 deployed)"
echo "sha256:img-s9" > "$d/fixtures/running.retrieval-api"
echo "ghcr.io/getklai/retrieval-api:s1 sha256:img-s1" > "$d/fixtures/images"
run "$SINCE" --until "$UNTIL"
check "skipped, not failed: exit 0" test "$rc" = 0
check "not rolled back" test ! -s "$d/fixtures/deploys"
check "reported" grep -q 'retrieval-api: it runs sha256:img-s9.*e2e-newer-deploy-running' <<< "$out"

echo "# the rehearsal canary"
new_case canary \
    "$(line 2026-10-28T05:00:00Z image rollback-canary ccccccccccc1 busybox:1.37.0 none deployed)" \
    "$(line 2026-10-28T05:01:00Z image rollback-canary ccccccccccc1 debian:12.15-slim none rolled_back)"
run "$SINCE" --until "$UNTIL"
check "skipped on a real run: exit 0" test "$rc" = 0
check "canary untouched" test ! -s "$d/fixtures/deploys"
new_case rehearse \
    "$(line 2026-10-28T05:00:00Z image rollback-canary ccccccccccc1 busybox:1.37.0 none deployed)" \
    "$(line 2026-10-28T05:05:00Z image retrieval-api aaaaaaaaaaa1 ghcr.io/getklai/retrieval-api:s1 s1 deployed)" \
    "$(line 2026-10-28T05:06:00Z compose compose bbb222 ccc333 ccc333 deployed)"
run "$SINCE" --until "$UNTIL" --rehearse
check "--rehearse: exit 3" test "$rc" = 3
check "--rehearse: only the canary, with its compose profile" \
    test "$(deploys)" = "rollback-canary ccccccccccc1 --rollback e2e sha=none profiles=rollback-rehearsal"
check "--rehearse: compose untouched" grep -qx "new caddyfile" "$d/caddy/Caddyfile"
check "--rehearse: no replay time recorded" test ! -e "$d/.e2e-rollback-at"

echo "# one rollback fails, the others still run"
new_case partial \
    "$(line 2026-10-28T05:00:00Z image retrieval-api aaaaaaaaaaa1 ghcr.io/getklai/retrieval-api:s1 s1 deployed)" \
    "$(line 2026-10-28T05:10:00Z image klai-mailer mmmmmmmmmmm1 ghcr.io/getklai/klai-mailer:s2 s2 deployed)" \
    "$(line 2026-10-28T05:20:00Z image scribe-api none ghcr.io/getklai/scribe-api:s3 s3 deployed)" \
    "$(line 2026-10-28T05:30:00Z image klai-connector kkkkkkkkkkk1 ghcr.io/getklai/klai-connector:s4 s4 deployed)"
echo 4 > "$d/fixtures/rc.klai-mailer"
echo 1 > "$d/fixtures/rc.klai-connector"
run "$SINCE" --until "$UNTIL"
check "exit 4" test "$rc" = 4
check "every service with a previous image was attempted" test "$(deploys | cut -d' ' -f1 | paste -sd' ' -)" = "klai-connector klai-mailer retrieval-api"
check "report outcome rollback_failed" test "$(report outcome)" = rollback_failed
check "a first deploy has nothing to go back to: event" grep -q '"deploy_service":"scribe-api","gate":"e2e-no-previous-image"' "$d/logs/auto-rollback.log"
check "a rollback that changed nothing: event" grep -q '"deploy_service":"klai-connector","gate":"e2e-exit-1"' "$d/logs/auto-rollback.log"

echo "# compose: the last sync is undone from its .prev copies"
new_case compose "$(line 2026-10-28T05:00:00Z compose compose bbb222 ccc333 ccc333 deployed)"
run "$SINCE" --until "$UNTIL"
check "exit 3" test "$rc" = 3
check "Caddyfile restored" grep -qx "old caddyfile" "$d/caddy/Caddyfile"
check "the touched service recreated, then the sweep" test "$(paste -sd'|' - < "$d/fixtures/calls")" = "compose-up --no-pull --force-recreate caddy|sweep"
check "marker back on the restored commit" grep -qx bbb222 "$d/.compose-commit"
check "state removed, so the sync is never undone twice" test ! -e "$d/.compose-prev-state"
check "ledger line rolled_back with gate in the report" grep -q "${T}compose${T}compose${T}bbb222${T}ccc333${T}ccc333${T}rolled_back$" "$d/deploy-ledger.log"
check "compose report gate e2e" grep -q '"gate":"e2e","outcome":"rolled_back"' "$d/deploy-reports/compose.json"

echo "# image entries before compose, even when compose is newer"
new_case order \
    "$(line 2026-10-28T05:00:00Z image retrieval-api aaaaaaaaaaa1 ghcr.io/getklai/retrieval-api:s1 s1 deployed)" \
    "$(line 2026-10-28T05:10:00Z compose compose bbb222 ccc333 ccc333 deployed)"
run "$SINCE" --until "$UNTIL"
check "exit 3" test "$rc" = 3
check "image replay ran before the compose restore" test "$(cut -d' ' -f1 "$d/fixtures/calls" | paste -sd' ' -)" = "deploy compose-up sweep"

echo "# compose: an earlier rollback failed, nothing is restored"
new_case flag "$(line 2026-10-28T05:00:00Z compose compose bbb222 ccc333 ccc333 deployed)"
echo "commit=ccc333 gate=x" > "$d/.compose-rollback-failed"
run "$SINCE" --until "$UNTIL"
check "exit 4" test "$rc" = 4
check "Caddyfile not restored" grep -qx "new caddyfile" "$d/caddy/Caddyfile"
check "nothing recreated" test ! -s "$d/fixtures/calls"
check "reported as previous-state-unverified" grep -q '"gate":"previous-state-unverified","outcome":"rollback_failed"' "$d/deploy-reports/compose.json"
check "aggregate report rollback_failed" test "$(report outcome)" = rollback_failed

echo "# compose: two syncs since the last green run"
new_case twosyncs \
    "$(line 2026-10-28T05:00:00Z compose compose aaa111 bbb222 bbb222 deployed)" \
    "$(line 2026-10-28T05:10:00Z compose compose bbb222 ccc333 ccc333 deployed)"
run "$SINCE" --until "$UNTIL"
check "exit 4" test "$rc" = 4
check "Caddyfile not restored" grep -qx "new caddyfile" "$d/caddy/Caddyfile"
check "reported" grep -q '"gate":"e2e-several-syncs"' "$d/logs/auto-rollback.log"

echo "# bad arguments"
new_case args
run "yesterday" --until "$UNTIL"
check "a since that is not the ledger format -> exit 1" test "$rc" = 1
run "$SINCE"
check "no --until -> exit 1" test "$rc" = 1

if (( failures > 0 )); then
    echo "$failures check(s) failed"
    exit 1
fi
echo "all checks passed"
