#!/usr/bin/env bash
# The off-site box keeps the newest 30 complete day-sets and the first complete
# set of each of the 12 months before them; everything else is removed after a
# clean night. Until 2026-11-25 nothing was ever removed, and the 1 TB box
# reached 977 GB with 236 sets, three nights away from refusing the upload. ssh
# is stubbed: it serves the listing and the markers, and records what it is
# asked to remove.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin"
cat >"$TMP/bin/ssh" <<'STUB'
#!/usr/bin/env bash
command="${*: -1}"
case "$command" in
  "touch "*) echo "${command#touch }" >>"$STUB_DIR/marked" ;;
  "ls -1 "*"/.backup-complete") cat "$STUB_DIR/marked" ;;
  "ls -1 "*) cat "$STUB_DIR/listing" ;;
  "rm -r "*) printf '%s\n' "$command" >>"$STUB_DIR/removed"; exit "${STUB_RM_RC:-0}" ;;
  *) exit 1 ;;
esac
STUB
chmod +x "$TMP/bin/ssh"
export PATH="$TMP/bin:$PATH" STUB_DIR="$TMP"
export BACKUP_DATE=2027-01-10 STORAGEBOX_HOST=storagebox.example.com STORAGEBOX_USER=test-only

# shellcheck source=/dev/null
source "$REPO_ROOT/deploy/scripts/backup.sh"
trap - ERR EXIT
trap 'rm -rf "$TMP"' EXIT
set +e

fail_test() { echo "FAIL: $1" >&2; exit 1; }

# Sets from before 2026-11-26 carry no marker and count as complete. 2026-06-01
# is missing, the way the real box has no set before 2026-03-27: the month is
# then represented by its first set that exists, not by a set named -01.
{
  for month in 2025-08 2025-09 2025-10 2025-11 2025-12 2026-01 2026-02 2026-03 \
    2026-04 2026-05 2026-06 2026-07 2026-08 2026-09 2026-10 2026-11 2026-12; do
    for day in $(seq -w 1 28); do echo "${month}-${day}"; done
  done
  for day in $(seq -w 1 10); do echo "2027-01-${day}"; done
  echo "2026-03-27.old"
} | grep -v '^2026-06-01$' >"$TMP/listing"

# Three later nights failed and uploaded a partial set without a marker: the
# first of December and two inside the daily window. Tonight's marker is the
# one the run itself has to write.
grep -E '^(2026-11-2[678]|2026-12|2027-01)' "$TMP/listing" \
  | grep -vE '^(2026-12-01|2027-01-05|2027-01-06|2027-01-10)$' \
  | sed 's|.*|backups/core-01/&/.backup-complete|' >"$TMP/marked"

# The two failed nights in the window do not count, so it reaches back to
# 2026-12-07, and December is kept by its first complete set, 2026-12-02.
{
  for day in $(seq -w 7 28); do echo "2026-12-${day}"; done
  for day in $(seq -w 1 10); do echo "2027-01-${day}"; done
  for month in 2026-01 2026-02 2026-03 2026-04 2026-05 2026-07 2026-08 2026-09 \
    2026-10 2026-11; do echo "${month}-01"; done
  echo "2026-06-02"
  echo "2026-12-02"
  echo "2026-03-27.old"
} | sort >"$TMP/expected-kept"
sort "$TMP/listing" | comm -23 - "$TMP/expected-kept" >"$TMP/expected-removed"

removed_sets() {
  tr ' ' '\n' <"$TMP/removed" | sed -n 's|^backups/core-01/||p' | sort
}

offsite_retention >"$TMP/out" 2>&1 || fail_test "a clean prune was reported as failed: $(cat "$TMP/out")"
diff <(removed_sets) "$TMP/expected-removed" >"$TMP/diff" \
  || fail_test "removed sets differ from the retention rule (< removed, > expected): $(cat "$TMP/diff")"

# A listing without tonight's set is not the directory the upload just wrote
# to, so nothing in it may be removed.
rm -f "$TMP/removed"
grep -v '^2027-01-10$' "$TMP/listing" >"$TMP/listing.stale" && mv "$TMP/listing.stale" "$TMP/listing"
offsite_retention >"$TMP/out" 2>&1 && fail_test "a listing without tonight's set was pruned"
[[ ! -e "$TMP/removed" ]] || fail_test "sets were removed from a listing without tonight's set"

# A removal the box refuses must surface, or the box fills up unnoticed again.
echo "2027-01-10" >>"$TMP/listing"
STUB_RM_RC=1 offsite_retention >"$TMP/out" 2>&1 && fail_test "a refused removal was reported as success"

echo "backup off-site retention: OK"
