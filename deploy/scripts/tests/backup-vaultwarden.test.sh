#!/usr/bin/env bash
# Tests for backup.sh's Vaultwarden step: the nightly run includes it, a good
# night records the database copy and the key-file tar, and any failure (the
# server's own backup command, the integrity check, the tar, a stopped
# container) fails the step and leaves nothing of it for the upload to ship.
#
# No Docker: a fake docker answers `exec /vaultwarden backup`, `cp`, `exec rm`
# and runs the step's own alpine script against a synthetic data directory;
# fakes for sqlite3, age and rsync stand in for the integrity check and the
# encrypt-and-ship path. All data is invented in this file.
#
# Run: bash deploy/scripts/tests/backup-vaultwarden.test.sh

set -euo pipefail

script_dir=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
backup="$script_dir/backup.sh"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
bin="$work/bin"
mkdir -p "$bin"

cat > "$bin/docker" <<'FAKE'
#!/usr/bin/env bash
F="$FAKE"
echo "docker $*" >> "$F/calls"
case "$1" in
  inspect) [[ -e "$F/stopped" ]] && echo false || echo true ;;
  exec)
    shift 2
    case "$1" in
      /vaultwarden)
        [[ -e "$F/backup_fails" ]] && { echo "Backup failed. DatabaseError(Unknown, \"disk I/O error\")"; exit 1; }
        cp "$F/vol/db.sqlite3" "$F/vol/db_20261125_020000.sqlite3"
        echo "Backup to '/data/db_20261125_020000.sqlite3' was successful" ;;
      rm) rm -f "$F/vol/${3#/data/}" ;;
    esac ;;
  cp) cp "$F/vol/${2#*:/data/}" "$3" ;;
  run)
    # The step's own script, run against the fake volume and backup directory.
    shift
    while [[ "$1" != alpine ]]; do
      [[ "$1" == -v && "$2" == *:/backup ]] && out="${2%:/backup}"
      [[ "$1" == -e ]] && export "${2?}"
      shift
    done
    [[ ! -e "$F/tar_fails" ]] || exit 1
    sed -e "s|/data|$F/vol|g" -e "s|/backup|$out|g" <<< "$4" | sh ;;
esac
FAKE
cat > "$bin/sqlite3" <<'FAKE'
#!/usr/bin/env bash
echo "sqlite3 $*" >> "$FAKE/calls"
if [[ -e "$FAKE/integrity_bad" ]]; then
  printf '*** in database main ***\nPage 3: btreeInitPage() returns error code 11\n'
else
  echo ok
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

fail() {
  echo "  FAIL $1"
  [[ ! -e "$case_dir/out" ]] || sed 's/^/       /' "$case_dir/out"
  failures=$((failures + 1))
}
check() { if [[ "$2" == "$3" ]]; then echo "  ok   $1"; else fail "$1 (expected '$2', got '$3')"; fi; }

# A case: a data directory shaped like Vaultwarden's, with a live WAL pair and
# an icon cache that must stay out of the tar.
new_case() {
  case_dir="$work/$1"
  local v="$case_dir/fake/vol"
  mkdir -p "$v/attachments/cipher-a" "$v/icon_cache" "$case_dir/backups/2026-11-25"
  : > "$case_dir/fake/calls"
  printf 'synthetic database\n' > "$v/db.sqlite3"
  printf 'synthetic wal\n' > "$v/db.sqlite3-wal"
  printf 'synthetic shm\n' > "$v/db.sqlite3-shm"
  printf 'synthetic signing key, not a key\n' > "$v/rsa_key.pem"
  printf 'synthetic attachment\n' > "$v/attachments/cipher-a/file-1"
  printf 'synthetic icon\n' > "$v/icon_cache/example.com.png"
}

# run_vault: the step through run_step, then encrypt and ship, as main runs
# them. A dump made earlier that night stands in for the other artifacts.
run_vault() {
  local d="$case_dir/backups/2026-11-25"
  printf 'synthetic dump\n' > "$d/postgres-all.sql"
  (
    export BACKUP_DIR="$d" FAKE="$case_dir/fake" STORAGEBOX_HOST=storage.example.com STORAGEBOX_USER=u1
    PATH="$bin:$PATH"
    # shellcheck source=deploy/scripts/backup.sh
    source "$backup"
    trap - ERR EXIT
    set +e
    ARTIFACTS=("$d/postgres-all.sql")
    run_step "Vaultwarden: SQLite copy and key files" backup_vaultwarden
    echo "failed steps: ${FAILED_STEPS[*]:-none}"
    encrypt_and_upload
    rm -rf "$ENCRYPT_DIR"
  ) > "$case_dir/out" 2>&1
}

in_set() { (cd "$case_dir/backups/2026-11-25" && find . -name 'vaultwarden*' | sed 's|^\./||' | sort | paste -sd' ' -); }
shipped() { grep vaultwarden "$case_dir/fake/shipped" | paste -sd' ' - || true; }

echo "backup.sh vaultwarden"

new_case included
check "the nightly run has the vaultwarden step" 1 "$(bash -c "source '$backup'; declare -f main" | grep -c 'run_step "Vaultwarden: .*" backup_vaultwarden;\{0,1\}$' || true)"

new_case ok
run_vault
check "a good night -> the step passes" "failed steps: none" "$(grep '^failed steps' "$case_dir/out")"
check "  the copy and the tar are in the set, no partial left" "vaultwarden-db.sqlite3 vaultwarden-files.tar.gz" "$(in_set)"
check "  both are shipped, encrypted" "vaultwarden-db.sqlite3.age vaultwarden-files.tar.gz.age" "$(shipped)"
check "  the copy is what the server's backup command wrote" "synthetic database" "$(cat "$case_dir/backups/2026-11-25/vaultwarden-db.sqlite3")"
check "  the integrity check ran on the copy" 1 "$(grep -c "sqlite3 .*vaultwarden-db.sqlite3.partial PRAGMA integrity_check;" "$case_dir/fake/calls")"
check "  the tar holds the key and the attachments, not the live database or the icon cache" \
  "attachments/cipher-a/file-1 rsa_key.pem" \
  "$(tar -tzf "$case_dir/backups/2026-11-25/vaultwarden-files.tar.gz" | grep -v '/$' | sort | paste -sd' ' -)"
check "  both are readable by their owner only" "600 600" \
  "$(cd "$case_dir/backups/2026-11-25" && python3 -c 'import os, sys; print(" ".join(oct(os.stat(f).st_mode)[-3:] for f in sys.argv[1:]))' vaultwarden-db.sqlite3 vaultwarden-files.tar.gz)"
check "  the server's copy is removed from the live volume" "" "$(find "$case_dir/fake/vol" -name 'db_*')"
check "  checksum and listing recorded for the restore" 4 "$(grep -c '^file vaultwarden-' "$case_dir/backups/2026-11-25/backup-facts.txt")"

for broken in backup_fails integrity_bad tar_fails stopped; do
  new_case "$broken"
  touch "$case_dir/fake/$broken"
  run_vault
  check "$broken -> the step fails, so the run ends red" "failed steps: Vaultwarden: SQLite copy and key files" "$(grep '^failed steps' "$case_dir/out")"
  check "  nothing of it stays in the set" "" "$(in_set)"
  check "  nothing of it is shipped, the rest still is" "postgres-all.sql.age" "$(grep -v '^$' "$case_dir/fake/shipped" | paste -sd' ' -)"
done

new_case no_key
rm "$case_dir/fake/vol/rsa_key.pem"
run_vault
check "a data directory without rsa_key.pem -> the step fails, nothing shipped" "1 " "$(grep -c '^failed steps: Vaultwarden' "$case_dir/out") $(shipped)"

echo
if [[ "$failures" -eq 0 ]]; then
  echo "backup-vaultwarden: all cases passed"
else
  echo "backup-vaultwarden: $failures case(s) failed"
  exit 1
fi
