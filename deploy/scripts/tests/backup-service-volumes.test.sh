#!/usr/bin/env bash
# Tests for backup.sh's steps for the Grafana and Calnode databases and the
# Caddy and GlitchTip volumes: the nightly run includes them, the SQLite copy
# is consistent with what still sits in the WAL, a tar holds the directory, and
# any failure (a stopped service, a failed copy or tar, a copy failing
# integrity_check) fails the step and leaves nothing of it for the upload.
#
# No Docker: a fake docker runs the step's own Python or shell script on the
# host against a synthetic volume; sqlite3 is the real one unless a case makes
# integrity_check fail, and fakes for age and rsync stand in for the
# encrypt-and-ship path. All data is invented in this file. Needs python3 and
# sqlite3.
#
# Run: bash deploy/scripts/tests/backup-service-volumes.test.sh

set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
backup="$script_dir/backup.sh"
real_sqlite3="$(command -v sqlite3)" || { echo "sqlite3 is required (apt-get install sqlite3); not skipping" >&2; exit 1; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
bin="$work/bin"
mkdir -p "$bin"

# Each container's volumes live under $FAKE/vol/<container>, at the paths the
# container sees.
cat > "$bin/docker" <<'FAKE'
#!/usr/bin/env bash
F="$FAKE"
echo "docker $*" >> "$F/calls"
case "$1" in
  inspect)
    case "$3" in
      '{{.State.Running}}') [[ -e "$F/stopped" ]] && echo false || echo true ;;
      '{{.Image}}') echo sha256:0000000000000000000000000000000000000000000000000000000000000001 ;;
    esac ;;
  run)
    shift
    entrypoint=""
    while [[ "$1" == -* ]]; do
      case "$1" in
        --volumes-from) ctr="${2%:ro}"; shift ;;
        -v) [[ "$2" == *:/backup ]] && out="${2%:/backup}"; shift ;;
        -e) export "${2?}"; shift ;;
        --entrypoint) entrypoint="$2"; shift ;;
        --user|--network) shift ;;
      esac
      shift
    done
    shift # the image
    # A failure after the tar has started writing its output.
    [[ ! -e "$F/run_fails" ]] || { [[ -z "${OUT:-}" ]] || : > "$out/${OUT#/backup/}"; exit 1; }
    vol="$F/vol/$ctr"
    if [[ "$entrypoint" == python3 ]]; then
      DB="$vol$DB" python3 "$@"
    else
      export DIR="$vol$DIR" OUT="$out/${OUT#/backup/}"
      "$@"
    fi ;;
esac
FAKE
cat > "$bin/sqlite3" <<FAKE
#!/usr/bin/env bash
if [[ -e "\$FAKE/integrity_bad" ]]; then
  printf '*** in database main ***\nPage 3: btreeInitPage() returns error code 11\n'
else
  exec "$real_sqlite3" "\$@"
fi
FAKE
cat > "$bin/age" <<'FAKE'
#!/usr/bin/env bash
cat "${!#}"
FAKE
cat > "$bin/rsync" <<'FAKE'
#!/usr/bin/env bash
src="${*: -2:1}"
ls "$src" > "$FAKE/shipped"
FAKE
chmod +x "$bin"/*

failures=0
case_dir=""
set_dir=""

fail() {
  echo "  FAIL $1"
  [[ ! -e "$case_dir/out" ]] || sed 's/^/       /' "$case_dir/out"
  failures=$((failures + 1))
}
check() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else fail "$1 (expected '$2', got '$3')"; fi; }

# A case: a Calnode volume whose database is in WAL mode with every booking
# still in calnode.db-wal (the writer exits without a checkpoint, as a running
# server leaves it), and a Caddy tenants directory of invented site blocks.
new_case() {
  case_dir="$work/$1"
  set_dir="$case_dir/backups/2026-11-25"
  local cal="$case_dir/fake/vol/klai-core-calnode-1/data" tenants="$case_dir/fake/vol/klai-core-caddy-1/etc/caddy/tenants"
  mkdir -p "$cal" "$tenants" "$set_dir"
  : > "$case_dir/fake/calls"
  python3 - "$cal/calnode.db" <<'PY'
import os, sqlite3, sys
db = sqlite3.connect(sys.argv[1])
db.execute("PRAGMA journal_mode=WAL")
db.execute("PRAGMA wal_autocheckpoint=0")
db.execute("CREATE TABLE bookings (id INTEGER PRIMARY KEY, attendee TEXT)")
db.executemany("INSERT INTO bookings (attendee) VALUES (?)", [(f"guest{i}@example.com",) for i in range(37)])
db.commit()
os._exit(0)
PY
  printf 'chat-example-a.example.com {\n  reverse_proxy example-a:3080\n}\n' > "$tenants/chat-example-a.caddyfile"
  printf 'docs-example-b.example.com {\n  reverse_proxy docs-app:3010\n}\n' > "$tenants/docs-example-b.caddyfile"
}

# run_one <title> <step>: the step through run_step, then encrypt and ship, as
# main runs them. A dump made earlier that night stands in for the rest.
run_one() {
  printf 'synthetic dump\n' > "$set_dir/postgres-all.sql"
  (
    export BACKUP_DIR="$set_dir" FAKE="$case_dir/fake" STORAGEBOX_HOST=storage.example.com STORAGEBOX_USER=u1
    PATH="$bin:$PATH"
    # shellcheck source=deploy/scripts/backup.sh
    source "$backup"
    trap - ERR EXIT
    set +e
    ARTIFACTS=("$set_dir/postgres-all.sql")
    run_step "$1" "$2"
    echo "failed steps: ${FAILED_STEPS[*]:-none}"
    encrypt_and_upload
    rm -rf "$ENCRYPT_DIR"
  ) > "$case_dir/out" 2>&1
}

in_set() { (cd "$set_dir" && find . -name "$1*" | sed 's|^\./||' | sort | paste -sd' ' -); }
shipped() { grep -v '^$' "$case_dir/fake/shipped" | paste -sd' ' - || true; }
mode() { python3 -c 'import os, sys; print(oct(os.stat(sys.argv[1]).st_mode)[-3:])' "$1"; }
volume() { (cd "$case_dir/fake/vol/klai-core-calnode-1/data" && ls | paste -sd' ' -); }

echo "backup.sh service volumes"

new_case included
check "the nightly run has the five steps" 5 "$(bash -c "source '$backup'; declare -f main" \
  | grep -cE 'run_step ".*" (backup_grafana|backup_calnode|backup_caddy_data|backup_caddy_tenants|backup_glitchtip_uploads);?$' || true)"

new_case sqlite_ok
check "the volume starts with the database and its WAL pair" "calnode.db calnode.db-shm calnode.db-wal" "$(volume)"
check "  and the bookings are only in the WAL" 0 "$( (cd "$case_dir" && cp fake/vol/klai-core-calnode-1/data/calnode.db alone.db \
  && "$real_sqlite3" alone.db "select count(*) from sqlite_master where name = 'bookings';") )"
run_one "Calnode: SQLite online copy" backup_calnode
check "a good night -> the step passes" "failed steps: none" "$(grep '^failed steps' "$case_dir/out")"
check "  the copy is in the set, no partial left" "calnode-db.sqlite3" "$(in_set calnode)"
check "  it is shipped, encrypted" "backup-facts.txt.age calnode-db.sqlite3.age postgres-all.sql.age" "$(shipped)"
check "  the copy alone holds every booking that was in the WAL" 37 "$("$real_sqlite3" "$set_dir/calnode-db.sqlite3" 'select count(*) from bookings;')"
check "  readable by its owner only" 600 "$(mode "$set_dir/calnode-db.sqlite3")"
check "  the helper has no network" 1 "$(grep -c '^docker run --rm --network none .*--entrypoint python3' "$case_dir/fake/calls")"
check "  nothing is left in or taken from the volume" "calnode.db calnode.db-shm calnode.db-wal" "$(volume)"
check "  checksum recorded for the restore" 1 "$(grep -c '^file calnode-db.sqlite3 sha256 ' "$set_dir/backup-facts.txt")"

for broken in stopped run_fails integrity_bad; do
  new_case "sqlite_$broken"
  touch "$case_dir/fake/$broken"
  run_one "Calnode: SQLite online copy" backup_calnode
  check "$broken -> the SQLite step fails, so the run ends red" "failed steps: Calnode: SQLite online copy" "$(grep '^failed steps' "$case_dir/out")"
  check "  nothing of it stays in the set" "" "$(in_set calnode)"
  check "  nothing of it is shipped, the rest still is" "postgres-all.sql.age" "$(shipped)"
done

new_case tar_ok
run_one "Caddy: tar per-tenant site blocks" backup_caddy_tenants
check "a good night -> the tar step passes" "failed steps: none" "$(grep '^failed steps' "$case_dir/out")"
check "  the tar is in the set, no partial left" "caddy-tenants.tar.gz" "$(in_set caddy)"
check "  it holds every site block" "./chat-example-a.caddyfile ./docs-example-b.caddyfile" \
  "$(tar -tzf "$set_dir/caddy-tenants.tar.gz" | grep -v '/$' | sort | paste -sd' ' -)"
check "  it is shipped, encrypted" "backup-facts.txt.age caddy-tenants.tar.gz.age postgres-all.sql.age" "$(shipped)"
check "  readable by its owner only" 600 "$(mode "$set_dir/caddy-tenants.tar.gz")"
check "  the volume is mounted read-only" 1 "$(grep -c -- '--volumes-from klai-core-caddy-1:ro' "$case_dir/fake/calls")"
check "  listing recorded for the restore" 3 "$(grep -c '^file caddy-tenants.tar.gz ' "$set_dir/backup-facts.txt")"

for broken in stopped run_fails; do
  new_case "tar_$broken"
  touch "$case_dir/fake/$broken"
  run_one "Caddy: tar per-tenant site blocks" backup_caddy_tenants
  check "$broken -> the tar step fails, so the run ends red" "failed steps: Caddy: tar per-tenant site blocks" "$(grep '^failed steps' "$case_dir/out")"
  check "  nothing of it stays in the set" "" "$(in_set caddy)"
  check "  nothing of it is shipped, the rest still is" "postgres-all.sql.age" "$(shipped)"
done

echo
if [[ "$failures" -eq 0 ]]; then
  echo "backup-service-volumes: all cases passed"
else
  echo "backup-service-volumes: $failures case(s) failed"
  exit 1
fi
