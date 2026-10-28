#!/usr/bin/env bash
# compose-rollback.sh, sourced by deploy-compose.yml on core-01 (SPEC-INFRA-AUTO-ROLLBACK-001
# slice S3, section 4.b). Not an entry point: it defines functions and runs nothing.
#
# The sync keeps a <path>.prev copy of every file it is about to overwrite
# (rb_keep). When a gate goes red (rb_gate) the copies made by this run are put
# back, the services whose config changed are recreated again, the same
# recreate sweep runs and verifies, and the run ends 3 (rolled_back) or 4
# (rollback_failed). One attempt only (R6): the restored state failing is the
# end, never a second round.
#
# Functions
#   rb_init <commit>                      once, after the checkout: the commit being deployed
#   rb_keep <path>...                     before overwriting a file or directory
#   rb_touched <compose-service>...       a service this sync recreates for its config
#   rb_gate <service> <gate> [--reverify] <cmd...>
#                                         run a gate; --reverify runs <cmd> again after a rollback
#   rb_deployed                           last line of a green run: ledger + report
#
# Exit codes of a red run: 3 rolled back, 4 rollback failed. The ledger line,
# the report (<reports>/compose.json, also printed as "ROLLBACK_REPORT <json>")
# and the auto_rollback event follow the contract of deploy-with-rollback.sh.
#
# Tests: deploy/scripts/tests/compose-rollback.test.sh

# Seams for the test-suite; production keeps every default.
RB_DIR="${KLAI_COMPOSE_DIR:-/opt/klai}"
RB_LEDGER="${KLAI_DEPLOY_LEDGER:-$RB_DIR/deploy-ledger.log}"
RB_REPORT_DIR="${KLAI_DEPLOY_REPORTS:-$RB_DIR/deploy-reports}"
RB_EVENT_LOG="${KLAI_AUTO_ROLLBACK_LOG:-$RB_DIR/logs/auto-rollback.log}"
# The commit the compose state on the host was last synced from; the "from" of the ledger.
RB_MARKER="$RB_DIR/.compose-commit"

# Images that migrate their own database when they start: Twenty (`upgrade` on
# start), LiteLLM (USE_PRISMA_MIGRATE), GlitchTip (`manage.py migrate`), Zitadel
# (start-from-init). Putting the old image back on the newer schema can leave it
# running against tables it does not understand, and restoring the compose file
# would also point a later `up -d` at the old tag. So when one of these changed
# in this sync nothing is restored; the run reports rollback_failed and a human
# decides (until S8 snapshots their databases). Matched on the image repository.
RB_SELF_MIGRATING_IMAGES='twentycrm/twenty|berriai/litellm|glitchtip/glitchtip|zitadel/zitadel'

RB_KEPT=()
RB_TOUCHED=()
RB_FROM="none"
RB_TO=""
RB_LOG=""

rb_now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
rb_json() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }

rb_init() {
  RB_TO="$1"
  RB_KEPT=()
  RB_TOUCHED=()
  RB_FROM="$(cat "$RB_MARKER" 2>/dev/null || true)"
  RB_FROM="${RB_FROM:-none}"
  mkdir -p "$RB_REPORT_DIR"
  RB_LOG="$RB_REPORT_DIR/compose.log"
  : > "$RB_LOG"
}

# Only the copies made by this run are restored: a .prev left by an earlier
# deploy, for a file this run never reached, would otherwise overwrite a file
# that is still current.
rb_keep() {
  local f
  for f in "$@"; do
    [[ -e "$f" ]] || continue
    rm -rf "$f.prev"
    cp -a "$f" "$f.prev" || return 1
    RB_KEPT+=("$f")
  done
}

rb_touched() { RB_TOUCHED+=("$@"); }

rb_restore() {
  local f rc=0
  for f in "${RB_KEPT[@]}"; do
    if [[ -d "$f.prev" ]]; then
      rsync -ac --delete "$f.prev/" "$f/" || rc=1
    else
      # Write-then-rename, as the sync does, so a reader never sees half a file.
      { cp -a "$f.prev" "$f.rbtmp" && mv -f "$f.rbtmp" "$f"; } || rc=1
    fi
  done
  return "$rc"
}

# 0 when an image of RB_SELF_MIGRATING_IMAGES differs between the compose file
# this sync replaced and the one it installed.
rb_self_migrating_changed() {
  local old="$RB_DIR/docker-compose.yml.prev" new="$RB_DIR/docker-compose.yml"
  [[ -f "$old" ]] || return 1
  ! diff -q <(rb_self_migrating_images "$old") <(rb_self_migrating_images "$new") >/dev/null
}
rb_self_migrating_images() {
  grep -E '^[[:space:]]+image:' "$1" | grep -E "$RB_SELF_MIGRATING_IMAGES" | sort || true
}

# The recreate path of the sync, over the restored files: services recreated
# for their config first (compose-up.sh verifies each), then the sweep, which
# recreates whatever the restored compose file declares differently.
rb_recreate() {
  if (( ${#RB_TOUCHED[@]} > 0 )); then
    "$RB_DIR/scripts/compose-up.sh" --no-pull --force-recreate "${RB_TOUCHED[@]}" || return 1
  fi
  "$RB_DIR/scripts/compose-env-drift-recreate.sh"
}

# rb_finish <service> <gate> <deployed|rolled_back|rollback_failed>
rb_finish() {
  local service="$1" gate="$2" outcome="$3" ts report event code=0
  ts="$(rb_now)"
  mkdir -p "$RB_REPORT_DIR"
  if [[ ! -e "$RB_LEDGER" ]]; then
    : >> "$RB_LEDGER" && chmod 0644 "$RB_LEDGER"
  fi
  printf '%s\tcompose\t%s\t%s\t%s\t%s\t%s\n' \
    "$ts" "$service" "$RB_FROM" "$RB_TO" "$RB_TO" "$outcome" >> "$RB_LEDGER"
  report="$(printf '{"service":"%s","kind":"compose","from":"%s","to":"%s","commit":"%s","gate":"%s","outcome":"%s","ts":"%s","log":"%s"}' \
    "$(rb_json "$service")" "$(rb_json "$RB_FROM")" "$(rb_json "$RB_TO")" "$(rb_json "$RB_TO")" \
    "$(rb_json "$gate")" "$outcome" "$ts" "$(rb_json "$RB_LOG")")"
  printf '%s\n' "$report" > "$RB_REPORT_DIR/compose.json"
  case "$outcome" in
    deployed) printf '%s\n' "$RB_TO" > "$RB_MARKER" ;;
    rolled_back) code=3 ;;
    *) code=4 ;;
  esac
  if (( code != 0 )); then
    event="$(printf '{"service":"auto-rollback","level":"error","event":"auto_rollback","outcome":"%s","deploy_service":"%s","gate":"%s","commit":"%s","_time":"%s"}' \
      "$outcome" "$(rb_json "$service")" "$(rb_json "$gate")" "$(rb_json "$RB_TO")" "$ts")"
    printf '%s\n' "$event"
    if ! { mkdir -p "$(dirname "$RB_EVENT_LOG")" && printf '%s\n' "$event" >> "$RB_EVENT_LOG"; } 2>/dev/null; then
      echo "WARN: could not append to $RB_EVENT_LOG; the auto_rollback event will not reach VictoriaLogs" >&2
    fi
  fi
  echo "ROLLBACK_REPORT $report"
  # exit, not return: the run must end red here, after the restore ran.
  if (( code != 0 )); then exit "$code"; fi
}

rb_deployed() { rb_finish compose none deployed; }

rb_gate() {
  local service="$1" gate="$2" reverify=0
  shift 2
  if [[ "$1" == --reverify ]]; then reverify=1; shift; fi
  # tee keeps the gate output in the Actions log and on disk for the report.
  if "$@" > >(tee -a "$RB_LOG") 2>&1; then return 0; fi

  echo "::error::gate '$gate' is red for $service; rolling back the compose sync"
  if rb_self_migrating_changed; then
    echo "::error::an image that migrates its own database changed in this sync; restoring files alone could leave it on a schema it does not understand. Nothing restored."
    rb_finish "$service" self-migrating-image rollback_failed
  fi
  if {
    rb_restore \
      && rb_recreate \
      && { (( reverify == 0 )) || "$@"; }
  } > >(tee -a "$RB_LOG") 2>&1; then
    echo "::error::$service rolled back: gate '$gate' failed on this sync and the previous files verify green"
    rb_finish "$service" "$gate" rolled_back
  fi
  echo "::error::$service rollback failed: the restored state is red as well; stopping after one attempt"
  rb_finish "$service" "$gate" rollback_failed
}
