#!/usr/bin/env bash
# Regression test: failed nights must not push complete backups out of the window.
#
# main() creates the dated directory before the first step runs, so every failed
# night leaves one behind and retention — which is skipped exactly when a step
# failed — never removes it. Counting directories therefore spends the retention
# budget on stubs: with the budget at 7, six bad nights would leave one
# restorable set. The Vexa Redis step failed three nights running in August 2026.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
SCRIPT="$REPO_ROOT/deploy/scripts/backup.sh"
MARKER=".backup-complete"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

BACKUP_ROOT="$TMP/backups"
COMPOSE_DIR="$TMP/compose"
mkdir -p "$BACKUP_ROOT" "$COMPOSE_DIR/secrets" "$TMP/bin"
printf 'test-only\n' >"$COMPOSE_DIR/secrets/mongo_root_password.txt"
touch "$COMPOSE_DIR/.env"

# Ten complete sets, then six failed nights that left bare directories behind.
for day in $(seq -w 1 10); do
    mkdir -p "$BACKUP_ROOT/2026-07-$day"
    touch "$BACKUP_ROOT/2026-07-$day/$MARKER"
done
for day in $(seq -w 11 16); do
    mkdir -p "$BACKUP_ROOT/2026-07-$day"
done

# Every step succeeds, so the run reaches local retention.
cat >"$TMP/bin/docker" <<'STUB'
#!/usr/bin/env bash
if [[ "$*" == *".Config.Env"* && "$*" == *"klai-core-redis-1"* ]]; then
    echo 'REDIS_PASSWORD=test-only'; exit 0
fi
if [[ "$*" == *".State.Running"* ]]; then echo 'true'; exit 0; fi
exit 0
STUB
for stub in sleep rsync age curl tar; do
    printf '#!/usr/bin/env bash\nexit 0\n' >"$TMP/bin/$stub"
done
chmod +x "$TMP/bin"/*

set +e
PATH="$TMP/bin:$PATH" \
BACKUP_DATE=2026-07-17 \
BACKUP_ROOT="$BACKUP_ROOT" \
COMPOSE_DIR="$COMPOSE_DIR" \
STORAGEBOX_HOST=invalid.test \
STORAGEBOX_USER=test-only \
bash "$SCRIPT" >"$TMP/out" 2>&1
set -e

# The run itself may report failed steps — the stubs do not implement every
# dump. What this test pins is the retention decision, so it only asserts on
# retention when retention actually ran.
if ! grep -q "Local cleanup" "$TMP/out"; then
    echo "SKIP-GUARD: retention did not run; this test cannot observe the window" >&2
    sed 's/^/  /' "$TMP/out" >&2
    exit 1
fi

# Seven completed sets must survive — 2026-07-05..10 plus today's 2026-07-17.
# Counting directories instead would have spent the whole budget on the six
# stubs and today's run, deleting every complete set from 2026-07-01 to -10.
complete_left="$(find "$BACKUP_ROOT" -mindepth 2 -maxdepth 2 -name "$MARKER" | wc -l | tr -d ' ')"
if [ "$complete_left" -ne 7 ]; then
    echo "FAIL: expected 7 complete sets retained, found $complete_left" >&2
    ls -1 "$BACKUP_ROOT" | sed 's/^/  /' >&2
    exit 1
fi

for gone in 2026-07-01 2026-07-04; do
    if [ -d "$BACKUP_ROOT/$gone" ]; then
        echo "FAIL: $gone is older than the window and was not removed" >&2
        exit 1
    fi
done

if [ ! -d "$BACKUP_ROOT/2026-07-05" ]; then
    echo "FAIL: oldest set inside the window was removed" >&2
    exit 1
fi

# The stubs from the failed nights fall inside the window here and are kept;
# what matters is that they did not cost a complete set. Once they age out the
# same cut removes them, which the count-based form never did.
echo "backup retention with partial sets: OK"
