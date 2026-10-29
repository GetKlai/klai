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
#   rb_replay <gate>                      after the run, undo the last green sync (rollback-since.sh)
#
# Exit codes of a red run: 3 rolled back, 4 rollback failed. The ledger line,
# the report (<reports>/compose.json, also printed as "ROLLBACK_REPORT <json>")
# and the auto_rollback event follow the contract of deploy-with-rollback.sh.
#
# A rollback_failed run leaves $RB_DIR/.compose-rollback-failed (commit and
# gate). While it exists the files on the host are a state nobody verified, so
# the next red gate restores nothing (a .prev made now would be the failing
# state) and reports rollback_failed with gate previous-state-unverified. A
# green run (rb_deployed) removes it.
#
# A green run also leaves $RB_DIR/.compose-prev-state: the commits and the paths
# and services it kept and touched. rb_replay reads it to undo that sync when a
# later check goes red (E2E prod-tenant, slice S6). The next sync removes it in
# rb_init, because from then on the .prev copies belong to that sync, and a
# replay removes it so the same sync is never undone twice.
#
# Data stores (slices S8a and S8b, datastore-rollback.sh, sourced below): an
# image change of a data store or of an app that migrates its own database is
# snapshotted before the sync changes anything, and a red gate within 30
# minutes restores the snapshots together with the files. Past the window
# nothing is restored (gate restore-window); an inventory entry this sync does
# not snapshot (LiteLLM) restores nothing either (gate self-migrating-image),
# and neither does a Mongo whose FCV was raised since its snapshot (gate
# mongo-fcv).
#
# Known ceilings
#   - Failures before the first recreate (the file copies, bash -n) are outside
#     the gates: containers are untouched and the next sync copies the files again.
#   - LiteLLM is recreated by litellm-hook-deploy.yml and left out of the
#     env-drift sweep, so a compose-file restore does not recreate it. Its image
#     changes block any restore (see above).
#   - rb_keep copies before the content comparison, so an unchanged file is
#     restored to identical content; that only matters if another deploy wrote
#     the same file in between.
#
# Tests: deploy/scripts/tests/compose-rollback.test.sh

# Seams for the test-suite; production keeps every default.
RB_DIR="${KLAI_COMPOSE_DIR:-/opt/klai}"
RB_LEDGER="${KLAI_DEPLOY_LEDGER:-$RB_DIR/deploy-ledger.log}"
RB_REPORT_DIR="${KLAI_DEPLOY_REPORTS:-$RB_DIR/deploy-reports}"
RB_EVENT_LOG="${KLAI_AUTO_ROLLBACK_LOG:-$RB_DIR/logs/auto-rollback.log}"
# The commit the compose state on the host was last synced from; the "from" of the ledger.
RB_MARKER="$RB_DIR/.compose-commit"
RB_FAILED_FLAG="$RB_DIR/.compose-rollback-failed"
RB_STATE="$RB_DIR/.compose-prev-state"

RB_KEPT=()
RB_NEW=()
RB_TOUCHED=()
RB_FROM="none"
RB_TO=""
RB_LOG=""

# shellcheck source=deploy/scripts/datastore-rollback.sh
source "$(dirname "${BASH_SOURCE[0]}")/datastore-rollback.sh"

rb_now() { date -u +%Y-%m-%dT%H:%M:%SZ; }
rb_json() { printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g'; }

rb_init() {
  RB_TO="$1"
  RB_KEPT=()
  RB_NEW=()
  RB_TOUCHED=()
  RB_FROM="$(cat "$RB_MARKER" 2>/dev/null || true)"
  RB_FROM="${RB_FROM:-none}"
  rm -f "$RB_STATE"
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
    # A path this run creates has nothing to copy; a restore removes it again.
    if [[ ! -e "$f" ]]; then RB_NEW+=("$f"); continue; fi
    rm -rf "$f.prev"
    cp -a "$f" "$f.prev" || return 1
    RB_KEPT+=("$f")
  done
}

rb_touched() { RB_TOUCHED+=("$@"); }

rb_restore() {
  local f rc=0
  for f in "${RB_NEW[@]}"; do rm -rf "$f" || rc=1; done
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

# 0 when a data store or self-migrating app changed in this sync without a
# snapshot of this run: LiteLLM (its own workflow migrates it), or a path that
# skipped ds_snapshot_changed. Putting the old image back on a newer schema can
# leave it running against tables it does not understand.
rb_unsnapshotted_change() {
  local name dir
  while read -r name; do
    [[ -n "$name" ]] || continue
    for dir in "${DS_SNAPSHOTS[@]}"; do
      [[ "$(ds_meta "$dir" name)" == "$name" ]] && continue 2
    done
    return 0
  done < <(ds_changed "$RB_DIR/docker-compose.yml.prev" "$RB_DIR/docker-compose.yml")
  return 1
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

# rb_finish <service> <gate> <deployed|rolled_back|rollback_failed|not_started>
# not_started: the run refused before the first file was replaced (disk
# headroom, a snapshot that failed or did not read back). The host is in its
# verified state, nothing was rolled back and nothing needs a person; it is
# still exit 4 so the run is red and the notice opens its issue, but the
# event, the report and the ledger say what happened instead of crying
# rollback_failed (which Grafana mails as "production may be broken", as it
# did on 2026-10-29 16:04 UTC for a mongodb snapshot that did not read back).
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
    deployed)
      printf '%s\n' "$RB_TO" > "$RB_MARKER"
      rm -f "$RB_FAILED_FLAG"
      local f
      {
        printf 'from=%s\nto=%s\n' "$RB_FROM" "$RB_TO"
        for f in "${RB_KEPT[@]}"; do printf 'kept=%s\n' "$f"; done
        for f in "${RB_NEW[@]}"; do printf 'new=%s\n' "$f"; done
        for f in "${RB_TOUCHED[@]}"; do printf 'touched=%s\n' "$f"; done
        # The data snapshots of this sync, so a replay restores them within R4's window.
        for f in "${DS_SNAPSHOTS[@]}"; do printf 'snapshot=%s\n' "$f"; done
      } > "$RB_STATE" ;;
    rolled_back)
      code=3
      rm -f "$RB_STATE"
      # During a sync the marker already names RB_FROM; after a replay the host
      # is back on RB_FROM and the marker has to follow.
      [[ "$RB_FROM" == none ]] || printf '%s\n' "$RB_FROM" > "$RB_MARKER" ;;
    *)
      code=4
      rm -f "$RB_STATE"
      # Keep the first failure's commit and gate; later runs only report.
      # not_started ends the run before any file is replaced, so the host is
      # still in its verified state and gets no flag.
      [[ -e "$RB_FAILED_FLAG" || "$outcome" == not_started ]] || printf 'commit=%s gate=%s\n' "$RB_TO" "$gate" > "$RB_FAILED_FLAG" ;;
  esac
  if (( code != 0 )); then
    event="$(printf '{"service":"auto-rollback","level":"%s","event":"auto_rollback","outcome":"%s","deploy_service":"%s","gate":"%s","commit":"%s","_time":"%s"}' \
      "$([[ "$outcome" == not_started ]] && echo warning || echo error)" "$outcome" "$(rb_json "$service")" "$(rb_json "$gate")" "$(rb_json "$RB_TO")" "$ts")"
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

# The red gate here is the one that already failed after the sync (E2E): the
# state the sync recorded is loaded and rb_gate runs its usual rollback, flag
# and self-migrating checks included, and exits 3 or 4 the same way.
rb_replay() {
  local key val
  if [[ ! -s "$RB_STATE" ]]; then
    echo "::error::no $RB_STATE: the last compose sync left nothing to undo" >&2
    return 1
  fi
  RB_KEPT=()
  RB_NEW=()
  RB_TOUCHED=()
  DS_SNAPSHOTS=()
  while IFS='=' read -r key val; do
    case "$key" in
      from) RB_FROM="$val" ;;
      to) RB_TO="$val" ;;
      kept) RB_KEPT+=("$val") ;;
      new) RB_NEW+=("$val") ;;
      touched) RB_TOUCHED+=("$val") ;;
      snapshot) DS_SNAPSHOTS+=("$val") ;;
    esac
  done < "$RB_STATE"
  mkdir -p "$RB_REPORT_DIR"
  RB_LOG="$RB_REPORT_DIR/compose.log"
  : > "$RB_LOG"
  rb_gate compose "$1" false
}

rb_gate() {
  local service="$1" gate="$2" reverify=0
  shift 2
  if [[ "$1" == --reverify ]]; then reverify=1; shift; fi
  # tee keeps the gate output in the Actions log and on disk for the report.
  if "$@" > >(tee -a "$RB_LOG") 2>&1; then return 0; fi

  echo "::error::gate '$gate' is red for $service; rolling back the compose sync"
  if [[ -e "$RB_FAILED_FLAG" ]]; then
    echo "::error::an earlier sync ended rollback_failed ($(cat "$RB_FAILED_FLAG")); the files on the host were never verified, so nothing is restored"
    rb_finish "$service" previous-state-unverified rollback_failed
  fi
  if rb_unsnapshotted_change; then
    echo "::error::an image that migrates its own database changed in this sync without a snapshot; restoring files alone could leave it on a schema it does not understand. Nothing restored."
    rb_finish "$service" self-migrating-image rollback_failed
  fi
  # R4: data only within 30 minutes of its snapshot; files without their data
  # would put the old image on the new schema, so past the window nothing.
  if ! ds_in_window; then
    rb_finish "$service" restore-window rollback_failed
  fi
  # A raised Mongo FCV is a human's step; a restore would undo it (S8b).
  if ! ds_fcv_unchanged; then
    rb_finish "$service" mongo-fcv rollback_failed
  fi
  if {
    rb_restore \
      && ds_restore_all \
      && rb_recreate \
      && { (( reverify == 0 )) || "$@"; }
  } > >(tee -a "$RB_LOG") 2>&1; then
    echo "::error::$service rolled back: gate '$gate' failed on this sync and the previous files verify green"
    rb_finish "$service" "$gate" rolled_back
  fi
  echo "::error::$service rollback failed: the restored state is red as well; stopping after one attempt"
  rb_finish "$service" "$gate" rollback_failed
}
