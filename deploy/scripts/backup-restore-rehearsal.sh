#!/usr/bin/env bash
# /opt/klai/scripts/backup-restore-rehearsal.sh
#
# The recurring proof that the nightly backup set restores: it runs
# backup-restore.sh on the newest complete day-set in the local staging area
# (the directory backup.sh dumps into, marked complete by .backup-complete) and
# writes one event line per artifact to the auto-rollback log, which Alloy ships
# to VictoriaLogs:
#   {"event":"backup_restore_rehearsal","outcome":"ok"|"failed"|"skipped","artifact":...}
# The Grafana rule backup_restore_rehearsal_missing mails when no ok line
# arrived for 30 days. Any failed artifact ends the run with exit 1 after the
# others ran, and ci-failure-notice.yml opens its issue. backup-restore-rehearsal.yml
# runs it over ssh on core-01.
#
# It works on the unencrypted staging copy, so it proves the dumps restore, not
# that last night's off-site .age files decrypt: that needs a decryption
# identity, which this host does not hold (docs/runbooks/backup-restore.md).
#
# Usage: backup-restore-rehearsal.sh
#
# Tests: deploy/scripts/tests/backup-restore.test.sh

set -uo pipefail

# shellcheck source=deploy/scripts/compose-rollback.sh
source "$(dirname "${BASH_SOURCE[0]}")/compose-rollback.sh"

# event <artifact> <ok|failed|skipped> <set> <detail>
event() {
  local line level=info
  [[ "$2" != failed ]] || level=error
  line="$(printf '{"service":"auto-rollback","level":"%s","event":"backup_restore_rehearsal","outcome":"%s","artifact":"%s","set":"%s","detail":"%s","_time":"%s"}' \
    "$level" "$2" "$1" "$(rb_json "$3")" "$(rb_json "$4")" "$(rb_now)")"
  echo "$line"
  { mkdir -p "$(dirname "$RB_EVENT_LOG")" && printf '%s\n' "$line" >> "$RB_EVENT_LOG"; } 2> /dev/null \
    || { echo "::error::could not append to $RB_EVENT_LOG; the dead-man's switch will fire"; return 1; }
}

marker="$(find "$DS_BACKUP_ROOT" -mindepth 2 -maxdepth 2 -name .backup-complete 2> /dev/null | sort | tail -n 1)"
if [[ -z "$marker" ]]; then
  echo "::error::no complete backup set under $DS_BACKUP_ROOT"
  event set failed "" "no complete backup set" || true
  exit 1
fi
set_dir="$(dirname "$marker")"
set_name="$(basename "$set_dir")"

# Restoring writes the set's data into new volumes on the same disk once more
# (the disk reached 100% on 2026-09-18).
need_kb=$(( $(du -sk "$set_dir" | cut -f1) * 2 ))
avail_kb="$(df -Pk "$DS_BACKUP_ROOT" | awk 'NR == 2 { print $4 }')"
if ! [[ "$avail_kb" =~ ^[0-9]+$ ]] || (( avail_kb < need_kb )); then
  echo "::error::not enough disk to rehearse $set_name: ${avail_kb:-unknown} KB free, ${need_kb} KB needed"
  event set failed "$set_name" "not enough disk" || true
  exit 1
fi

echo "==> restoring $set_name"
out="$(bash "$(dirname "${BASH_SOURCE[0]}")/backup-restore.sh" "$set_dir" 2>&1)"
rc=$?
echo "$out"

failed=0
seen=0
while read -r status artifact detail; do
  artifact="${artifact%:}"
  case "$status" in
    ok) outcome=ok ;;
    FAIL) outcome=failed; failed=1 ;;
    SKIP) outcome=skipped ;;
    *) continue ;;
  esac
  seen=$((seen + 1))
  event "$artifact" "$outcome" "$set_name" "$detail" || failed=1
done <<< "$out"

# A run that restored nothing, or a script that failed without naming an
# artifact, is not a green rehearsal.
if (( rc != 0 && failed == 0 )) || (( seen == 0 )); then
  event set failed "$set_name" "backup-restore.sh exit $rc without a result line" || true
  failed=1
fi
if (( failed )); then
  echo "::error::backup restore rehearsal failed for $set_name"
  exit 1
fi
echo "==> backup restore rehearsal ok for $set_name"
