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

# Every step succeeds, so the run reaches local retention. record_file rejects
# a missing or empty artifact, so the stub produces each one the way the real
# command does: a stdout redirect, a file named inside a directory mounted at
# /backup or /out, or the destination of a `cp`.
cat >"$TMP/bin/docker" <<'STUB'
#!/usr/bin/env bash
all="$*"
if [[ "$all" == *".Config.Env"* && "$all" == *"klai-core-redis-1"* ]]; then
    echo 'REDIS_PASSWORD=test-only'; exit 0
fi
if [[ "$all" == *".Config.Env"* && "$all" == *"klai-core-qdrant-1"* ]]; then
    echo 'QDRANT__SERVICE__API_KEY=test-only'; exit 0
fi
if [[ "$all" == *".State.Running"* ]]; then echo 'true'; exit 0; fi
# backup.sh waits for each BGSAVE to finish: LASTSAVE must move, and the save
# must report ok, or the step fails before retention can run.
if [[ "$all" == *" LASTSAVE" ]]; then date +%s%N; exit 0; fi
if [[ "$all" == *" BGSAVE" ]]; then echo 'Background saving started'; exit 0; fi
if [[ "$all" == *" INFO persistence" ]]; then echo 'rdb_last_bgsave_status:ok'; exit 0; fi
# pg_dumpall writes to stdout; STUB_EMPTY_DUMP_USER makes one dump come out empty.
if [[ "$all" == *pg_dumpall* ]]; then
    [[ -n "${STUB_EMPTY_DUMP_USER:-}" && "$all" == *"-U $STUB_EMPTY_DUMP_USER"* ]] || echo '-- dump'
    exit 0
fi
# `docker cp` and `docker compose cp`: the destination is the last argument.
if [[ " $all " == *" cp "* ]]; then echo stub >"${@: -1}"; exit 0; fi
if [[ "$1" == "run" ]]; then
    # tar, mongodump, the Garage snapshot tar and the Qdrant snapshot download
    # run in a throwaway container with the backup directory mounted: create
    # the file they were told to write there.
    for arg in "$@"; do
        [[ "$arg" == *:/backup || "$arg" == *:/out ]] && dir="${arg%:*}"
    done
    if [[ -n "${dir:-}" && "$all" =~ /(backup|out)/([A-Za-z0-9._-]+) ]]; then
        echo stub >"$dir/${BASH_REMATCH[2]}"
    fi
    # The Qdrant API calls answer in JSON.
    case "$all" in
        *curlimages/curl*" -o "*) ;;
        *"-X POST"*) echo '{"result":{"name":"snap-1"}}' ;;
        *"-X DELETE"*) echo '{}' ;;
        *curlimages/curl*) echo '{"result":{"collections":[{"name":"docs"}]}}' ;;
    esac
fi
exit 0
STUB
for stub in sleep rsync age curl tar; do
    printf '#!/usr/bin/env bash\nexit 0\n' >"$TMP/bin/$stub"
done
chmod +x "$TMP/bin"/*

run_backup() { # run_backup <date>: exit code in $rc, log in $TMP/out
    set +e
    PATH="$TMP/bin:$PATH" \
    BACKUP_DATE="$1" \
    BACKUP_ROOT="$BACKUP_ROOT" \
    COMPOSE_DIR="$COMPOSE_DIR" \
    STORAGEBOX_HOST=invalid.test \
    STORAGEBOX_USER=test-only \
    bash "$SCRIPT" >"$TMP/out" 2>&1
    rc=$?
    set -e
}

run_backup 2026-07-17

# Every stub step succeeds, so a failure here means a stub lost an artifact and
# the retention assertions below could not be observed.
if [ "$rc" -ne 0 ] || ! grep -q "Local cleanup" "$TMP/out"; then
    echo "SKIP-GUARD: the run did not complete cleanly; this test cannot observe the window" >&2
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

# A dump that produces an empty file is a failed step. run_step calls steps
# inside `if !`, where errexit does not apply, so record_file used to log the
# empty artifact and return 0: the night was reported green and marked complete.
STUB_EMPTY_DUMP_USER=listmonk run_backup 2026-07-18
if [ "$rc" -eq 0 ] || ! grep -q "Backup finished with 1 failed step(s): listmonk Postgres" "$TMP/out"; then
    echo "FAIL: an empty pg_dumpall artifact was not reported as a failed step" >&2
    sed 's/^/  /' "$TMP/out" >&2
    exit 1
fi
if [ -e "$BACKUP_ROOT/2026-07-18/$MARKER" ]; then
    echo "FAIL: a set with an empty dump was marked complete" >&2
    exit 1
fi

echo "backup retention with partial sets: OK"
