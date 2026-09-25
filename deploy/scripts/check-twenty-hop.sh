#!/usr/bin/env bash
# check-twenty-hop.sh <old-compose> <new-compose>
#
# Fails when a change moves twentycrm/twenty more than one minor at a time.
# Renovate automerges Twenty with one PR per minor, but nothing orders the
# merges: if v2.44 merged before v2.43 the CRM would skip a schema step. This
# check makes only the next hop green; the later PRs stay red until the hop
# lands and Renovate rebases them onto it. Patches within a minor are free:
# between v2.42.4 and v2.42.6 no upgrade-command or migration file changed.
# A major is one hop only to its .0 minor.
set -euo pipefail
tag() { grep -oE 'twentycrm/twenty:v[0-9]+\.[0-9]+\.[0-9]+' "$1" | sort -u | sed 's/.*:v//'; }
old=$(tag "$1"); new=$(tag "$2")
[ "$(printf '%s\n' "$new" | grep -c .)" -eq 1 ] || { echo "FAIL: expected one twentycrm/twenty pin in $2, found: $(echo $new)" >&2; exit 1; }
[ -n "$old" ] && [ "$old" != "$new" ] || { echo "OK: Twenty pin unchanged (${new})"; exit 0; }
IFS=. read -r omaj omin _ <<<"$old"; IFS=. read -r nmaj nmin _ <<<"$new"
if { [ "$nmaj" -eq "$omaj" ] && [ "$nmin" -ge "$omin" ] && [ $((nmin - omin)) -le 1 ]; } \
  || { [ "$nmaj" -eq $((omaj + 1)) ] && [ "$nmin" -eq 0 ]; }; then
  echo "OK: Twenty v$old -> v$new is one hop"
else
  echo "FAIL: Twenty v$old -> v$new skips a minor; merge the next minor first" >&2
  exit 1
fi
