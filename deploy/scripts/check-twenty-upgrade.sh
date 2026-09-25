#!/usr/bin/env bash
# check-twenty-upgrade.sh — after a deploy, prove the CRM migrated to the image it runs.
#
# The Twenty entrypoint runs `upgrade` on start and prints "Successfully
# migrated DB!" even when the upgrade failed (seen on 2026-09-24), so a green
# container proves nothing about the schema. The upgrade command itself logs
# `[upgrade] event=summary ... totalFailures=N` and, per workspace,
# `executedByVersion=vX.Y.Z`. This reads those lines from the current
# container's own log and fails unless the summary has no failures and the
# executed version equals the image tag.
#
# Only a container started in the last 30 minutes is checked: an older one was
# not restarted by this deploy, and its start-up lines may have rotated away.
set -euo pipefail
ctr="${TWENTY_CONTAINER:-klai-core-crm-1}"
started=$(docker inspect "$ctr" --format '{{.State.StartedAt}}')
age=$(( $(date +%s) - $(date -d "$started" +%s) ))
if [ "$age" -gt 1800 ]; then
  echo "OK: $ctr not restarted by this deploy (up ${age}s), nothing to check"
  exit 0
fi
tag=$(docker inspect "$ctr" --format '{{.Config.Image}}'); tag="${tag##*:}"
summary=""
for _ in $(seq 1 60); do
  logs=$(docker logs --since "$started" "$ctr" 2>&1 | sed 's/\x1b\[[0-9;]*m//g')
  summary=$(printf '%s\n' "$logs" | grep -F '[upgrade] event=summary' | tail -1 || true)
  [ -n "$summary" ] && break
  sleep 10
done
[ -n "$summary" ] || { echo "FAIL: $ctr logged no upgrade summary within 10 minutes of starting $tag" >&2; exit 1; }
case "$summary" in
  *totalFailures=0*) ;;
  *) echo "FAIL: Twenty upgrade to $tag reported failures: $summary" >&2; exit 1 ;;
esac
printf '%s\n' "$logs" | grep -F '[upgrade] event=workspace.success' | grep -qF "executedByVersion=$tag" \
  || { echo "FAIL: no workspace reports executedByVersion=$tag" >&2; exit 1; }
echo "OK: Twenty upgraded to $tag without failures"
