#!/usr/bin/env bash
# Regression test: a partial failed backup must not evict a complete local one.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SCRIPT="$REPO_ROOT/deploy/scripts/backup.sh"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

BACKUP_ROOT="$TMP/backups"
COMPOSE_DIR="$TMP/compose"
mkdir -p "$BACKUP_ROOT" "$COMPOSE_DIR/secrets" "$TMP/bin"
printf 'test-only\n' >"$COMPOSE_DIR/secrets/mongo_root_password.txt"
touch "$COMPOSE_DIR/.env"

# Thirty-one known-good backups make retention observable: a failed current run
# must leave the oldest sentinel intact.
for day in $(seq -w 1 31); do
    mkdir -p "$BACKUP_ROOT/2026-07-$day"
    touch "$BACKUP_ROOT/2026-07-$day/complete"
done

cat >"$TMP/bin/docker" <<'STUB'
#!/usr/bin/env bash
if [[ "$*" == *".Config.Env"* && "$*" == *"klai-core-redis-1"* ]]; then
    echo 'REDIS_PASSWORD=test-only'
    exit 0
fi
if [[ "$*" == *".State.Running"* && "$*" == *"klai-core-vexa12-redis-1"* ]]; then
    echo 'true'
    exit 0
fi
exit 1
STUB
cat >"$TMP/bin/sleep" <<'STUB'
#!/usr/bin/env bash
exit 0
STUB
cat >"$TMP/bin/rsync" <<'STUB'
#!/usr/bin/env bash
exit 1
STUB
chmod +x "$TMP/bin/docker" "$TMP/bin/sleep" "$TMP/bin/rsync"

set +e
PATH="$TMP/bin:$PATH" \
BACKUP_DATE=2026-08-20 \
BACKUP_ROOT="$BACKUP_ROOT" \
COMPOSE_DIR="$COMPOSE_DIR" \
STORAGEBOX_HOST=invalid.test \
STORAGEBOX_USER=test-only \
bash "$SCRIPT" >"$TMP/out" 2>&1
rc=$?
set -e

if [ "$rc" -eq 0 ]; then
    echo "FAIL: deliberately failed backup reported success" >&2
    exit 1
fi

if [ ! -f "$BACKUP_ROOT/2026-07-01/complete" ]; then
    echo "FAIL: failed backup ran local retention and evicted the oldest complete backup" >&2
    sed 's/^/  /' "$TMP/out" >&2
    exit 1
fi

if ! grep -qi "retention.*skip\|skip.*retention" "$TMP/out"; then
    echo "FAIL: failed backup did not explain that local retention was skipped" >&2
    sed 's/^/  /' "$TMP/out" >&2
    exit 1
fi

echo "backup retention after failed steps: OK"
