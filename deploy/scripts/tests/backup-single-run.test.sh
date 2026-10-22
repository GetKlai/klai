#!/usr/bin/env bash
# Regression test: a second backup run refuses to start while one is running.
#
# Two runs on the same date interleave: one clears the .backup-complete marker,
# the other writes it back after succeeding, and the first then overwrites
# artifacts and fails, leaving a complete-looking broken set that local
# retention may trust over an older good one. Needs flock (CI is Ubuntu).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SCRIPT="$REPO_ROOT/deploy/scripts/backup.sh"
MARKER=".backup-complete"
TMP="$(mktemp -d)"
holder=""
trap '[ -n "$holder" ] && kill "$holder" 2>/dev/null; rm -rf "$TMP"' EXIT

BACKUP_ROOT="$TMP/backups"
COMPOSE_DIR="$TMP/compose"
mkdir -p "$BACKUP_ROOT/2026-07-17" "$COMPOSE_DIR/secrets" "$TMP/bin"
printf 'test-only\n' >"$COMPOSE_DIR/secrets/mongo_root_password.txt"
touch "$COMPOSE_DIR/.env" "$BACKUP_ROOT/2026-07-17/$MARKER"

# A run that got past the lock must not reach a real Docker daemon.
printf '#!/usr/bin/env bash\nexit 1\n' >"$TMP/bin/docker"
chmod +x "$TMP/bin/docker"

# The other run: holds the lock until this test exits.
# --no-fork: the lock dies with $holder instead of with a forked sleep.
flock --no-fork "$BACKUP_ROOT" sleep 60 &
holder=$!
for _ in $(seq 50); do
    flock -n "$BACKUP_ROOT" true || break
    sleep 0.1
done

set +e
PATH="$TMP/bin:$PATH" \
BACKUP_DATE=2026-07-17 \
BACKUP_ROOT="$BACKUP_ROOT" \
COMPOSE_DIR="$COMPOSE_DIR" \
bash "$SCRIPT" >"$TMP/out" 2>&1
rc=$?
set -e

if [ "$rc" -eq 0 ] || ! grep -q "another backup is running" "$TMP/out"; then
    echo "FAIL: a second run did not refuse while the lock was held (exit $rc)" >&2
    sed 's/^/  /' "$TMP/out" >&2
    exit 1
fi
if [ ! -e "$BACKUP_ROOT/2026-07-17/$MARKER" ]; then
    echo "FAIL: the refused run removed the marker of the running backup's directory" >&2
    exit 1
fi

echo "backup single run: OK"
