#!/usr/bin/env bash
# /opt/klai/scripts/datastore-restore-rehearsal.sh
#
# The recurring proof that every data store's snapshot restores
# (SPEC-INFRA-AUTO-ROLLBACK-001 section 4.c, slice S8b, AT-c4).
# datastore-restore-rehearsal.yml runs it over ssh on core-01.
#
# Usage:
#   datastore-restore-rehearsal.sh [store...]     default: every store in the inventory
#
# For each store in datastore-rollback.sh's inventory (DS_STORES and
# DS_ENGINE_STORES) it restores the newest complete pre-<store>-* snapshot into
# a throwaway container of the image that took it (ds_restore_into: own volumes,
# --network none, label klai.adhoc=datastore-restore, limited memory and CPU,
# removed afterwards; never the live volume) and requires the restored facts to
# equal the snapshot's baseline exactly. A store without a snapshot gets a fresh
# one first, taken the way a deploy takes it (for Qdrant, Meilisearch, Mongo and
# Garage that stops the store for the seconds of a cold copy); a fresh snapshot
# is not a rollback point, so its ledger line goes to a scratch file and the
# snapshot is removed afterwards. redis-ephemeral keeps no state and is skipped.
#
# One event line per store in the auto-rollback log, which Alloy ships to
# VictoriaLogs:
#   {"event":"datastore_restore_rehearsal","outcome":"ok"|"failed","store":...}
# The Grafana rule datastore_restore_rehearsal_missing mails when no ok line
# arrived for 30 days. Any failed store ends the run with exit 1 after the
# others ran, and ci-failure-notice.yml opens its issue.
#
# Tests: deploy/scripts/tests/datastore-restore-rehearsal.test.sh

set -uo pipefail

# shellcheck source=deploy/scripts/compose-rollback.sh
source "$(dirname "${BASH_SOURCE[0]}")/compose-rollback.sh"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
RB_LEDGER="$WORK/ledger"
RB_TO="rehearsal"

# Newest snapshot of a store whose sync finished snapshotting: the epoch line is
# written only after every snapshot of that sync succeeded.
latest_snapshot() {
  local dir
  while read -r dir; do
    grep -q '^epoch=' "$dir/meta" 2>/dev/null && { echo "$dir"; return; }
  done < <(find "$DS_BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name "pre-$1-*" 2>/dev/null | sort -r)
}

event() {
  local line
  line="$(printf '{"service":"auto-rollback","level":"%s","event":"datastore_restore_rehearsal","outcome":"%s","store":"%s","engine":"%s","snapshot":"%s","_time":"%s"}' \
    "$([[ "$2" == ok ]] && echo info || echo error)" "$2" "$1" "$(ds_engine "$1")" "$(rb_json "$3")" "$(rb_now)")"
  echo "$line"
  { mkdir -p "$(dirname "$RB_EVENT_LOG")" && printf '%s\n' "$line" >> "$RB_EVENT_LOG"; } 2>/dev/null \
    || { echo "::error::could not append to $RB_EVENT_LOG; the dead-man's switch will fire"; return 1; }
}

stores=("$@")
(( ${#stores[@]} )) || mapfile -t stores < <(ds_store_names)
for store in "${stores[@]}"; do
  ds_is_store "$store" || { echo "::error::$store is not in the datastore inventory" >&2; exit 1; }
done

failed=()
for store in "${stores[@]}"; do
  if [[ "$(ds_kind "$(ds_engine "$store")")" == none ]]; then
    echo "==> $store keeps no state; nothing to rehearse"
    continue
  fi
  dir="$(latest_snapshot "$store")"
  fresh=0
  if [[ -z "$dir" ]]; then
    echo "==> $store has no snapshot; taking one"
    DS_SNAPSHOTS=()
    if mkdir -p "$DS_BACKUP_ROOT" && ds_disk_ok "$store" \
        && ds_snapshot "$store" "$RB_DIR/docker-compose.yml" "$RB_DIR/docker-compose.yml"; then
      dir="${DS_SNAPSHOTS[0]}"
      fresh=1
    else
      echo "::error::$store: could not take a snapshot to rehearse"
      failed+=("$store")
      event "$store" failed "" || failed+=("event-log")
      continue
    fi
  fi
  echo "==> $store: restoring $dir into a throwaway container"
  if ds_restore_into "$dir" "klai-datastore-rehearsal-$store"; then
    outcome=ok
  else
    outcome=failed
    failed+=("$store")
    echo "::error::$store: the restore of $dir failed"
  fi
  event "$store" "$outcome" "$dir" || failed+=("event-log")
  (( fresh == 0 )) || rm -rf "$dir"
done

if (( ${#failed[@]} )); then
  echo "::error::datastore restore rehearsal failed for: ${failed[*]}"
  exit 1
fi
echo "==> datastore restore rehearsal ok"
