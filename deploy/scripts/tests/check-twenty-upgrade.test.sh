#!/usr/bin/env bash
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"
printf '#!/bin/sh\nexit 0\n' > "$tmp/bin/sleep"
cat > "$tmp/bin/docker" <<'STUB'
#!/usr/bin/env bash
case "$1 $4" in
  "inspect {{.State.StartedAt}}") echo "$STARTED" ;;
  "inspect {{.Config.Image}}")    echo "twentycrm/twenty:v2.43.1" ;;
  *) [ "$1" = logs ] && cat "$LOGFILE" ;;
esac
STUB
chmod +x "$tmp/bin/"*
run() { # run <ok|fail> <started> <log lines...>
  local want=$1 started=$2; shift 2
  printf '%s\n' "$@" > "$tmp/log"
  if STARTED="$started" LOGFILE="$tmp/log" PATH="$tmp/bin:$PATH" bash "$here/check-twenty-upgrade.sh" >/dev/null 2>&1; then got=ok; else got=fail; fi
  [ "$got" = "$want" ] || { echo "FAIL: case '$*' gave $got, expected $want" >&2; exit 1; }
}
now=$(date -u +%Y-%m-%dT%H:%M:%SZ); old=$(date -u -d '-2 hours' +%Y-%m-%dT%H:%M:%SZ)
ok_ws='[upgrade] event=workspace.success workspaceId=w executedByVersion=v2.43.1 dryRun=false'
run ok   "$now" "$ok_ws" '[upgrade] event=summary totalSuccesses=1 totalFailures=0 dryRun=false'
run fail "$now" "$ok_ws" '[upgrade] event=summary totalSuccesses=0 totalFailures=1 dryRun=false'
run fail "$now" 'Successfully migrated DB!'
run fail "$now" '[upgrade] event=workspace.success workspaceId=w executedByVersion=v2.42.6 dryRun=false' '[upgrade] event=summary totalSuccesses=1 totalFailures=0 dryRun=false'
run ok   "$old" 'nothing from the start of this container is left'
echo "check-twenty-upgrade: PASS"
