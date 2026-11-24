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
check "ledger line rolled_back with gate in the report" grep -q "${T}compose${T}compose${T}bbb222${T}ccc333${T}ccc333${T}rolled_back${T}" "$d/deploy-ledger.log"
check "and the moment the restore began as its serving time" grep -qE "${T}rolled_back${T}[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:]{8}Z$" "$d/deploy-ledger.log"
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

# --red-after: the E2E run an earlier round started after its rollback was red
# too (e2e-rollback-evidence.sh decides which run that is; its tests cover
# manual runs (e), cancelled runs (c) and runs before a green one (f)). Last
# green run at SINCE (04:00) in every case. X was deployed at 05:00 and rolled
# back by a round whose replay started at 06:00; its E2E run completed 06:10.
x_line="$(line 2026-10-28T05:00:00Z image retrieval-api aaaaaaaaaaa1 ghcr.io/getklai/retrieval-api:s1 s1 deployed)"
x_back="$(line 2026-10-28T06:01:00Z image retrieval-api ghcr.io/getklai/retrieval-api:s1 aaaaaaaaaaa1 s1 rolled_back)"
y_at() { line "$1" image klai-mailer mmmmmmmmmmm1 ghcr.io/getklai/klai-mailer:s2 s2 deployed; }
after_x() { new_case "$1" "$x_line" "$x_back" "${@:2}"; echo 2026-10-28T06:00:00Z > "$d/.e2e-rollback-at"; }

echo "# (a) green, deploy X at 05:00, red at 05:45, confirmed: X is rolled back"
new_case seq-a "$x_line"
run "$SINCE" --until 2026-10-28T05:45:00Z
check "exit 3" test "$rc" = 3
check "X back to the image of the last green run" test "$(deploys)" = "retrieval-api aaaaaaaaaaa1 --rollback e2e sha=s1 profiles="
check "report outcome rolled_back" test "$(report outcome)" = rolled_back

echo "# (b) X rolled back, its E2E run red at 06:10, Y at 07:00, red at 07:30: blocked"
after_x seq-b "$(y_at 2026-10-28T07:00:00Z)" "$(line 2026-10-28T07:05:00Z compose compose bbb222 ccc333 ccc333 deployed)"
run "$SINCE" --until 2026-10-28T07:30:00Z --red-after 2026-10-28T06:10:00Z
check "exit 5" test "$rc" = 5
check "nothing rolled back" test ! -s "$d/fixtures/deploys"
check "compose untouched" grep -qx "new caddyfile" "$d/caddy/Caddyfile"
check "report outcome e2e_already_red, gate e2e-already-red" test "$(report outcome) $(report gate)" = "e2e_already_red e2e-already-red"
check "report names the deploys it does not blame, not X" test "$(report service)" = "klai-mailer compose"
check "report carries the evidence time" test "$(report red_after)" = 2026-10-28T06:10:00Z
check "said in the run log" grep -q '::error::E2E prod-tenant was still red after the last rollback (run completed 2026-10-28T06:10:00Z)' <<< "$out"
check "no replay time recorded, so the next round decides the same way" test "$(cat "$d/.e2e-rollback-at")" = 2026-10-28T06:00:00Z

echo "# (c) X rolled back, its E2E run cancelled or absent (no --red-after), Y at 07:00: Y is rolled back"
after_x seq-c "$(y_at 2026-10-28T07:00:00Z)"
run "$SINCE" --until 2026-10-28T07:30:00Z
check "exit 3" test "$rc" = 3
check "only Y rolled back" test "$(deploys | cut -d' ' -f1)" = klai-mailer

echo "# (d) X rolled back, its E2E run red at 06:10, Y at 06:24:59, inside the 15-minute margin: Y is rolled back"
after_x seq-d "$(y_at 2026-10-28T06:24:59Z)"
run "$SINCE" --until 2026-10-28T07:30:00Z --red-after 2026-10-28T06:10:00Z
check "exit 3" test "$rc" = 3
check "Y rolled back" test "$(deploys | cut -d' ' -f1)" = klai-mailer

echo "# (d') Y at 06:20, Z at 07:00: one deploy inside the margin rolls back the whole window"
after_x seq-d2 "$(y_at 2026-10-28T06:20:00Z)" "$(line 2026-10-28T07:00:00Z image scribe-api sssssssssss1 ghcr.io/getklai/scribe-api:s5 s5 deployed)"
run "$SINCE" --until 2026-10-28T07:30:00Z --red-after 2026-10-28T06:10:00Z
check "exit 3" test "$rc" = 3
check "both rolled back" test "$(deploys | cut -d' ' -f1 | paste -sd' ' -)" = "scribe-api klai-mailer"

echo "# evidence older than the last replay says nothing about what that replay left"
after_x seq-old "$(y_at 2026-10-28T07:00:00Z)"
run "$SINCE" --until 2026-10-28T07:30:00Z --red-after 2026-10-28T05:50:00Z
check "exit 3" test "$rc" = 3

echo "# (e) a manual red run is no evidence: auto-rollback.yml passes no --red-after, Y is rolled back"
new_case seq-e "$(y_at 2026-10-28T07:00:00Z)"
run "$SINCE" --until 2026-10-28T07:30:00Z
check "exit 3" test "$rc" = 3

echo "# (f) after a blocked round a green run completes (new SINCE 08:00), Z at 09:00 breaks it: Z is rolled back"
after_x seq-f "$(y_at 2026-10-28T07:00:00Z)" "$(line 2026-10-28T09:00:00Z image scribe-api sssssssssss1 ghcr.io/getklai/scribe-api:s5 s5 deployed)"
run 2026-10-28T08:00:00Z --until 2026-10-28T09:30:00Z
check "exit 3" test "$rc" = 3
check "only Z rolled back" test "$(deploys | cut -d' ' -f1)" = scribe-api

echo "# (g) replay shaped like 2026-11-19..21: first round rolls back, every later round is blocked"
new_case seq-g \
    "$(line 2026-11-19T03:10:00Z image zitadel zzzzzzzzzzz1 ghcr.io/getklai/zitadel:s1 s1 deployed)" \
    "$(line 2026-11-19T21:00:00Z image rabbitmq rrrrrrrrrrr1 rabbitmq:4.2.1 s2 deployed)" \
    "$(line 2026-11-20T11:30:00Z image portal-api ppppppppppp1 ghcr.io/getklai/portal-api:s3 s3 deployed)" \
    "$(line 2026-11-20T11:40:00Z image klai-slack kkkkkkkkkkk1 ghcr.io/getklai/klai-slack:s4 s4 deployed)" \
    "$(line 2026-11-20T20:00:00Z compose compose bbb222 ccc333 ccc333 deployed)" \
    "$(line 2026-11-21T17:20:00Z image knowledge-ingest iiiiiiiiiii1 ghcr.io/getklai/knowledge-ingest:s5 s5 deployed)"
G_SINCE=2026-11-18T12:40:00Z
run "$G_SINCE" --until 2026-11-19T03:18:00Z
check "round 1 (red 03:18, no evidence yet): zitadel rolled back, exit 3" test "$rc $(deploys | cut -d' ' -f1)" = "3 zitadel"
# Round 1's replay started at 03:25 and its E2E run completed red at 03:36.
echo 2026-11-19T03:25:00Z > "$d/.e2e-rollback-at"
: > "$d/fixtures/deploys"
for until in 2026-11-19T21:05:00Z 2026-11-20T11:45:00Z 2026-11-20T20:05:00Z 2026-11-21T17:25:00Z; do
    run "$G_SINCE" --until "$until" --red-after 2026-11-19T03:36:00Z
    check "round red at $until: blocked, exit 5" test "$rc" = 5
done
check "nothing after round 1 rolled back" test ! -s "$d/fixtures/deploys"
check "compose (the CRM restore) untouched" grep -qx "new caddyfile" "$d/caddy/Caddyfile"

# The fallback margin, for ledger lines without a serving time: X's evidence
# completed 06:10:00, so the margin ends at 06:25:00.
echo "# fallback margin boundary: a legacy line at exactly evidence + 900 s is a possible cause"
after_x seq-m0 "$(y_at 2026-10-28T06:25:00Z)"
run "$SINCE" --until 2026-10-28T07:30:00Z --red-after 2026-10-28T06:10:00Z
check "exit 3" test "$rc" = 3
echo "# fallback margin boundary: one second past it is not"
after_x seq-m1 "$(y_at 2026-10-28T06:25:01Z)"
run "$SINCE" --until 2026-10-28T07:30:00Z --red-after 2026-10-28T06:10:00Z
check "exit 5" test "$rc" = 5

# Serving time (column 8). y_srv <ledger time> <serving time>
y_srv() { line "$1" image klai-mailer mmmmmmmmmmm1 ghcr.io/getklai/klai-mailer:s2 s2 deployed "$2"; }
echo "# (h) Y served from 06:09:59, before X's evidence completed at 06:10, its line at 06:40 is past the margin: rolled back"
after_x seq-h "$(y_srv 2026-10-28T06:40:00Z 2026-10-28T06:09:59Z)"
run "$SINCE" --until 2026-10-28T07:30:00Z --red-after 2026-10-28T06:10:00Z
check "exit 3" test "$rc" = 3
check "Y rolled back" test "$(deploys | cut -d' ' -f1)" = klai-mailer
echo "# (h=) Y served from 06:10:00, the second the evidence completed: rolled back"
after_x seq-h0 "$(y_srv 2026-10-28T06:40:00Z 2026-10-28T06:10:00Z)"
run "$SINCE" --until 2026-10-28T07:30:00Z --red-after 2026-10-28T06:10:00Z
check "exit 3" test "$rc" = 3
echo "# (i) Y served from 06:10:01, its line at 06:12 is inside the old margin: blocked"
after_x seq-i "$(y_srv 2026-10-28T06:12:00Z 2026-10-28T06:10:01Z)"
run "$SINCE" --until 2026-10-28T07:30:00Z --red-after 2026-10-28T06:10:00Z
check "exit 5" test "$rc" = 5
check "nothing rolled back" test ! -s "$d/fixtures/deploys"
echo "# (j) a slow sync served from 06:05 and finished at 06:50, after Y (06:30, served 06:20): every line is weighed, rolled back"
after_x seq-j "$(y_srv 2026-10-28T06:30:00Z 2026-10-28T06:20:00Z)" \
    "$(line 2026-10-28T06:50:00Z compose compose bbb222 ccc333 ccc333 deployed 2026-10-28T06:05:00Z)"
run "$SINCE" --until 2026-10-28T07:30:00Z --red-after 2026-10-28T06:10:00Z
check "exit 3" test "$rc" = 3
check "Y rolled back and the sync undone" test "$(deploys | cut -d' ' -f1)" = klai-mailer -a "$(cat "$d/caddy/Caddyfile")" = "old caddyfile"
echo "# (k) Y with a serving time after the evidence, Z a legacy line inside the margin: rolled back"
after_x seq-k "$(y_srv 2026-10-28T06:30:00Z 2026-10-28T06:20:00Z)" \
    "$(line 2026-10-28T06:20:00Z image scribe-api sssssssssss1 ghcr.io/getklai/scribe-api:s5 s5 deployed)"
run "$SINCE" --until 2026-10-28T07:30:00Z --red-after 2026-10-28T06:10:00Z
check "exit 3" test "$rc" = 3
echo "# (k') a serving column that is not a timestamp falls back to the margin: line 06:20 is inside it, rolled back"
after_x seq-k2 "$(y_srv 2026-10-28T06:20:00Z soon)"
run "$SINCE" --until 2026-10-28T07:30:00Z --red-after 2026-10-28T06:10:00Z
check "exit 3" test "$rc" = 3
echo "# (k'') a real run never weighs the canary: it served before the evidence, Y after it, blocked"
after_x seq-k3 "$(line 2026-10-28T06:05:00Z image rollback-canary ccccccccccc1 busybox:1.37.0 none deployed 2026-10-28T06:04:00Z)" \
    "$(y_srv 2026-10-28T06:30:00Z 2026-10-28T06:20:00Z)"
run "$SINCE" --until 2026-10-28T07:30:00Z --red-after 2026-10-28T06:10:00Z
check "exit 5" test "$rc" = 5
check "report names Y, not the canary" test "$(report service)" = klai-mailer

# Confirmation window. Red run created 07:30; W landed at 07:40 while the
# confirmation ran; the replay starts at 07:50.
w_line="$(line 2026-10-28T07:40:00Z image scribe-api sssssssssss1 ghcr.io/getklai/scribe-api:s5 s5 deployed 2026-10-28T07:35:00Z)"
echo "# (l) auto-rollback.yml ends the window at the replay start: W is rolled back with Y"
new_case seq-l "$(y_at 2026-10-28T07:00:00Z)" "$w_line"
run "$SINCE" --until 2026-10-28T07:50:00Z
check "exit 3" test "$rc" = 3
check "Y and W rolled back" test "$(deploys | cut -d' ' -f1 | sort | paste -sd' ' -)" = "klai-mailer scribe-api"
echo "# (l') a window ending at the red run's creation (an older workflow): the next window starts there, so W is in it"
new_case seq-l2 "$(y_at 2026-10-28T07:00:00Z)" "$w_line"
run "$SINCE" --until 2026-10-28T07:30:00Z
check "round 1 rolls back Y, exit 3" test "$rc $(deploys | cut -d' ' -f1)" = "3 klai-mailer"
check "replay mark is the window's end, not the replay's start" test "$(cat "$d/.e2e-rollback-at")" = 2026-10-28T07:30:00Z
: > "$d/fixtures/deploys"
# Round 1's post-rollback run went red at 07:58 with W serving since 07:35.
run "$SINCE" --until 2026-10-28T08:30:00Z --red-after 2026-10-28T07:58:00Z
check "round 2: W served during that run, so it is rolled back, exit 3" test "$rc $(deploys | cut -d' ' -f1)" = "3 scribe-api"

# Rehearsal of the blocked path (rollback-rehearsal-assert.sh scenario 2): with
# --rehearse the decision weighs the canary only, as the replay does.
c_at() { line "$1" image rollback-canary ccccccccccc1 busybox:1.37.0 none deployed "$2"; }
echo "# (n) --rehearse: the canary served after the evidence, a real deploy inside the margin: blocked, nothing touched"
new_case seq-n "$(y_at 2026-10-28T06:15:00Z)" "$(c_at 2026-10-28T06:20:00Z 2026-10-28T06:19:00Z)"
run 2026-10-28T06:10:00Z --until 2026-10-28T07:30:00Z --red-after 2026-10-28T06:10:30Z --rehearse
check "exit 5" test "$rc" = 5
check "nothing rolled back" test ! -s "$d/fixtures/deploys"
check "report names the canary only" test "$(report service) $(report outcome)" = "rollback-canary e2e_already_red"
echo "# (n') --rehearse: the canary served before the evidence: rolled back, so the block is not unconditional"
new_case seq-n2 "$(c_at 2026-10-28T06:20:00Z 2026-10-28T06:10:00Z)"
run 2026-10-28T06:00:00Z --until 2026-10-28T07:30:00Z --red-after 2026-10-28T06:10:30Z --rehearse
check "exit 3" test "$rc" = 3
check "the canary rolled back" test "$(deploys | cut -d' ' -f1)" = rollback-canary

echo "# bad arguments"
new_case args
run "yesterday" --until "$UNTIL"
check "a since that is not the ledger format -> exit 1" test "$rc" = 1
run "$SINCE"
check "no --until -> exit 1" test "$rc" = 1
new_case badred "$(line 2026-10-28T05:00:00Z image retrieval-api aaaaaaaaaaa1 ghcr.io/getklai/retrieval-api:s1 s1 deployed)"
run "$SINCE" --until "$UNTIL" --red-after "last night"
check "a --red-after that is not the ledger format is ignored: rolled back, exit 3" test "$rc" = 3
check "and warned" grep -q "::warning::--red-after 'last night'" <<< "$out"

if (( failures > 0 )); then
    echo "$failures check(s) failed"
    exit 1
fi
echo "all checks passed"
