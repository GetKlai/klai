#!/usr/bin/env bash
# /opt/klai/scripts/backup-restore.sh
#
# Restores the artifacts of one nightly backup set (backup.sh) into throwaway
# targets and proves each restore with a fact, so "the dump exists" becomes
# "the dump reads back". It never writes to a live volume or a live database:
# every target is a container or a volume of its own (the throwaway of
# datastore-rollback.sh: --network none, label klai.adhoc=datastore-restore,
# limited memory and CPU), removed afterwards unless --keep. Putting a restored
# artifact into production is an operator's decision and is described in
# docs/runbooks/backup-restore.md.
#
# Usage:
#   backup-restore.sh [--identity <age identity file>] [--keep] <set-dir> [artifact...]
#
#   <set-dir>   the local staging copy of a day-set (plain files), or a
#               directory of the .age files fetched from the off-site box; for
#               .age files --identity is required. The identity is only handed
#               to age by path: it is not read from anywhere by default, not
#               copied and not printed.
#   artifact    one of the names below; default all of them. A name given on
#               the command line whose file is missing is a failure; in the
#               default run an artifact backup.sh recorded nothing for (its
#               container was not running that night) is reported as skipped.
#
#   postgres gitea mongodb redis vexa_redis meilisearch qdrant falkordb
#   garage_meta garage_data firecrawl_postgres listmonk_postgres crm_postgres
#   fider_postgres umami_postgres crm_storage listmonk_uploads scribe_audio
#   research_uploads
#
# The fact an artifact must reproduce (backup-facts.txt, written by backup.sh,
# is part of every set):
#   *_postgres, postgres  the rows of every COPY block in the dump equal the
#                         rows counted in the restored tables, exactly
#   mongodb               the documents mongorestore reports per collection
#                         equal the documents counted afterwards, exactly
#   redis, vexa_redis,    at least the keys (FalkorDB: and graphs) read just
#   falkordb              before the save
#   meilisearch, qdrant   at least the documents per index and the points per
#                         collection read just before the snapshot
#   tar artifacts         the member count and the hash of the sorted member
#                         names recorded at backup time, and as many files in
#                         the restored volume
# and for every file its recorded checksum, so a truncated or damaged copy is
# named before an engine is started. A fact that cannot be compared (no
# recorded line, no table in a dump) is a failure.
#
# One line per artifact: "ok", "FAIL" or "SKIP", then the name. Exit 1 when any
# artifact failed; the others are still restored and reported.
#
# Tests: deploy/scripts/tests/backup-restore.test.sh

set -uo pipefail

# shellcheck source=deploy/scripts/compose-rollback.sh
source "$(dirname "${BASH_SOURCE[0]}")/compose-rollback.sh"

readonly RS_FACTS="backup-facts.txt"
# name, kind, file, compose service of the engine's image
readonly RS_ARTIFACTS='
postgres            pg      postgres-all.sql             postgres
gitea               tar     gitea-repos.tar.gz           -
mongodb             mongo   mongodb-all.archive          mongodb
redis               rdb     redis-dump.rdb               redis
vexa_redis          rdb     vexa12-redis-dump.rdb        vexa12-redis
meilisearch         meili   meilisearch.snapshot         meilisearch
qdrant              qdrant  qdrant-*.snapshot            qdrant
falkordb            rdb     falkordb-dump.rdb            falkordb
garage_meta         tar     garage-meta.tar.gz           -
garage_data         tar     garage-data.tar.gz           -
firecrawl_postgres  pg      firecrawl-postgres-all.sql   firecrawl-postgres
listmonk_postgres   pg      listmonk-postgres-all.sql    listmonk-db
crm_postgres        pg      crm-postgres-all.sql         crm-postgres
fider_postgres      pg      fider-postgres-all.sql       fider-postgres
umami_postgres      pg      umami-postgres-all.sql       umami-postgres
crm_storage         tar     crm-storage.tar.gz           -
listmonk_uploads    tar     listmonk-uploads.tar.gz      -
scribe_audio        tar     scribe-audio.tar.gz          -
research_uploads    tar     research-uploads.tar.gz      -
'

IDENTITY=""
KEEP=0
ENCRYPTED=0
SET_DIR=""
WORK=""
AW=""
FACTS_TEXT=""
RS_CTRS=()
RS_VOLS=()

usage() {
  echo "usage: ${0##*/} [--identity <age identity file>] [--keep] <set-dir> [artifact...]" >&2
  exit 2
}

rs_cleanup() {
  (( KEEP )) && return 0
  # -v: the engine images declare their data directory as a volume. Without it
  # the first production rehearsal (2026-11-12) left eight unnamed volumes
  # behind, 7.3 GiB of them the restored main database.
  (( ${#RS_CTRS[@]} == 0 )) || docker rm -f -v "${RS_CTRS[@]}" > /dev/null 2>&1 || true
  (( ${#RS_VOLS[@]} == 0 )) || docker volume rm "${RS_VOLS[@]}" > /dev/null 2>&1 || true
  RS_CTRS=(); RS_VOLS=()
}
rs_exit() { rs_cleanup; [[ -z "$WORK" ]] || rm -rf "$WORK"; }
trap rs_exit EXIT

rs_row() { awk -v n="$1" '$1 == n' <<< "$RS_ARTIFACTS"; }

file_sha256() {
  python3 -c '
import hashlib
import sys

h = hashlib.sha256()
with open(sys.argv[1], "rb") as f:
    for chunk in iter(lambda: f.read(1 << 20), b""):
        h.update(chunk)
print(h.hexdigest())' "$1"
}

# "<entries> <sha256 of the sorted member names>", computed as backup.sh does.
tar_listing_facts() {
  python3 -c '
import hashlib
import sys

names = sorted(n for n in sys.stdin.buffer.read().split(b"\n") if n and not n.endswith(b"/"))
print(len(names), hashlib.sha256(b"\n".join(names)).hexdigest())'
}

# A gzip tar as it is, or the tar stream of a directory (the staging copy of
# research-uploads is a directory; its off-site copy is a tar stream).
rs_stream() {
  if [[ -d "$1" ]]; then tar -czf - -C "$(dirname "$1")" "$(basename "$1")"; else cat "$1"; fi
}

# rec_file <file name> <sha256|entries|listing>
rec_file() { awk -v f="$1" -v k="$2" '$1 == "file" && $2 == f && $3 == k { print $4; exit }' <<< "$FACTS_TEXT"; }
# rec_facts <artifact>: the recorded lines as ds_facts prints them, sorted.
rec_facts() { awk -v a="$1" '$1 == "facts" && $2 == a { $1 = ""; $2 = ""; sub(/^ +/, ""); print }' <<< "$FACTS_TEXT" | LC_ALL=C sort; }
# rs_recorded <file name pattern>: backup.sh recorded a file like that.
rs_recorded() {
  local k n
  # shellcheck disable=SC2053 # the pattern is a glob on purpose (qdrant-*.snapshot)
  while read -r k n _; do [[ "$k" == file && "$n" == $1 ]] && return 0; done <<< "$FACTS_TEXT"
  return 1
}

# rs_start <container> <image> [docker run options] -- [command...]
rs_start() {
  local ctr="$1" image="$2" opts=()
  shift 2
  while (( $# )) && [[ "$1" != "--" ]]; do opts+=("$1"); shift; done
  shift
  RS_CTRS+=("$ctr")
  docker run -d --rm --name "$ctr" --network none --label klai.adhoc=datastore-restore "${DS_THROWAWAY_LIMITS[@]}" \
    ${opts[@]+"${opts[@]}"} "$image" "$@" > /dev/null || { echo "could not start $ctr on $image"; return 1; }
}

rs_image() {
  local image
  image="$(ds_image_of "$RB_DIR/docker-compose.yml" "$1")"
  [[ -n "$image" ]] || { echo "no image for $1 in $RB_DIR/docker-compose.yml"; return 1; }
  echo "$image"
}

# rs_expect_sha <file name> <path>: the copy is the one backup.sh wrote.
rs_expect_sha() {
  local want got
  want="$(rec_file "$1" sha256)"
  [[ -n "$want" ]] || { echo "no recorded checksum for $1 in $RS_FACTS"; return 1; }
  got="$(file_sha256 "$2")" || { echo "cannot read $1"; return 1; }
  [[ "$got" == "$want" ]] || { echo "$1 differs from the checksum recorded at backup time (damaged or cut short)"; return 1; }
}

# rs_compare <recorded> <restored> <exact|atleast>: both files hold ds_facts lines.
rs_compare() {
  [[ -s "$1" ]] || { echo "no recorded fact to compare with"; return 1; }
  [[ -s "$2" ]] || { echo "the restore shows no facts"; return 1; }
  ds_compare_facts "$1" "$2" "$3" > "$AW/compare.out" 2>&1 \
    || { grep -v '^probe:' "$AW/compare.out" | sed -e 's/^::error:://' -e 's/the snapshot/the recorded facts/g' | head -n 3 | paste -sd';' -; return 1; }
}

rs_pg() {
  local file="$2" ctr="klai-backup-restore-$1-$$" image db i
  grep -q '^-- PostgreSQL database cluster dump complete$' "$file" \
    || { echo "the dump has no end marker, so it is cut short"; return 1; }
  # Rows of every COPY block per database: what the dump itself says it holds.
  awk '
    inb && /^\\\.$/ { inb = 0; print db, t, n; next }
    inb { n++; next }
    /^\\connect / { db = $2; gsub(/"/, "", db); next }
    /^COPY / { inb = 1; t = $2; n = 0 }' "$file" | LC_ALL=C sort > "$AW/want"
  [[ -s "$AW/want" ]] || { echo "the dump holds no table data to compare"; return 1; }
  image="$(rs_image "$3")" || { echo "$image"; return 1; }
  # postgres, not the dump's own superuser: the dump creates that role.
  rs_start "$ctr" "$image" -e POSTGRES_USER=postgres -e POSTGRES_HOST_AUTH_METHOD=trust -- || return 1
  # Over TCP: the init server of the image listens on the socket only.
  for i in $(seq 1 60); do
    docker exec "$ctr" pg_isready -h 127.0.0.1 -U postgres > /dev/null 2>&1 && break
    (( i == 60 )) && { echo "throwaway $ctr did not start"; return 1; }
    sleep 2
  done
  docker exec -i "$ctr" psql -U postgres -v ON_ERROR_STOP=1 -q -f - < "$file" > /dev/null \
    || { echo "psql could not replay the dump"; return 1; }
  : > "$AW/got"
  # shellcheck disable=SC2013 # database names hold no whitespace
  for db in $(awk '{ print $1 }' "$AW/want" | sort -u); do
    # ONLY: an inheritance parent counts its children otherwise, the dump does not.
    awk -v d="$db" '$1 == d { printf "select '\''%s'\'', count(*) from only %s;\n", $2, $2 }' "$AW/want" \
      | docker exec -i "$ctr" psql -U postgres -d "$db" -v ON_ERROR_STOP=1 -At -F ' ' -f - \
      | awk -v d="$db" '{ print d, $1, $2 }' >> "$AW/got" \
      || { echo "could not count the rows of $db after the restore"; return 1; }
  done
  LC_ALL=C sort -o "$AW/got" "$AW/got"
  diff "$AW/want" "$AW/got" > "$AW/diff" \
    || { echo "rows differ (< in the dump, > restored): $(grep '^[<>]' "$AW/diff" | head -n 5 | paste -sd';' -)"; return 1; }
  echo "$(wc -l < "$AW/want" | tr -d ' ') tables with as many rows as the dump holds; restored into throwaway container $ctr"
}

rs_mongo() {
  local file="$2" ctr="klai-backup-restore-$1-$$" image out
  image="$(rs_image "$3")" || { echo "$image"; return 1; }
  rs_start "$ctr" "$image" -- || return 1
  ds_wait_ready "ctr:$ctr" mongo || { echo "throwaway $ctr did not become ready"; return 1; }
  out="$(docker exec -i "$ctr" mongorestore --archive < "$file" 2>&1)" \
    || { echo "mongorestore failed: $(tail -n 1 <<< "$out")"; return 1; }
  # mongorestore quotes the namespace in backticks.
  sed -n 's/.*finished restoring `\{0,1\}\([^ `]*\)`\{0,1\} (\([0-9]*\) documents\{0,1\}, \([0-9]*\) failures\{0,1\}).*/\1 \2 \3/p' <<< "$out" \
    | awk '$1 !~ /\.system\./' > "$AW/reported"
  [[ -s "$AW/reported" ]] || { echo "mongorestore reported no collection"; return 1; }
  ! awk '$3 != 0 { bad = 1 } END { exit !bad }' "$AW/reported" || { echo "mongorestore reported failed documents"; return 1; }
  awk '{ print "count", $1, $2 }' "$AW/reported" | LC_ALL=C sort > "$AW/want"
  ds_facts "ctr:$ctr" mongo | grep '^count ' > "$AW/got" || { echo "could not count the restored documents"; return 1; }
  rs_compare "$AW/want" "$AW/got" exact || return 1
  echo "$(wc -l < "$AW/want" | tr -d ' ') collections with as many documents as the archive holds; restored into throwaway container $ctr"
}

# rs_rdb <artifact> <file> <compose service> <engine>
rs_rdb() {
  local ctr="klai-backup-restore-$1-$$" image
  image="$(rs_image "$3")" || { echo "$image"; return 1; }
  mkdir -p "$AW/stage"
  ln "$2" "$AW/stage/dump.rdb" 2> /dev/null || cp "$2" "$AW/stage/dump.rdb" || return 1
  printf 'engine=%s\nstore_image=%s\n' "$4" "$image" > "$AW/stage/meta"
  ds_throwaway "$AW/stage" "$ctr" "$image" > "$AW/got" \
    || { echo "the engine did not start on the dump, or did not answer"; return 1; }
  rec_facts "$1" > "$AW/want"
  rs_compare "$AW/want" "$AW/got" atleast || return 1
  echo "$(wc -l < "$AW/want" | tr -d ' ') facts read before the save are all in the restore; restored into a throwaway container, removed afterwards"
}

# rs_snapshot <artifact> <engine> <compose service> <file...>: Meilisearch and
# Qdrant start from their snapshot files.
rs_snapshot() {
  local a="$1" engine="$2" ctr="klai-backup-restore-$1-$$" image dir f c cmd=() env=()
  image="$(rs_image "$3")" || { echo "$image"; return 1; }
  shift 3
  dir="$(dirname "$1")"
  for f in "$@"; do [[ "$(dirname "$f")" == "$dir" ]] || { echo "snapshot files in two directories"; return 1; }; done
  if [[ "$engine" == meilisearch ]]; then
    env=(-e MEILI_DB_PATH=/meili_data/data.ms -e MEILI_ENV=development -e MEILI_NO_ANALYTICS=true)
    cmd=(/bin/meilisearch --import-snapshot "/in/$(basename "$1")")
  else
    cmd=(./qdrant)
    for f in "$@"; do
      c="$(basename "$f")"; c="${c#qdrant-}"
      cmd+=(--snapshot "/in/$(basename "$f"):${c%.snapshot}")
    done
  fi
  rs_start "$ctr" "$image" -v "$dir:/in:ro" ${env[@]+"${env[@]}"} -- "${cmd[@]}" || return 1
  ds_wait_ready "ctr:$ctr" "$engine" || { echo "throwaway $ctr did not start on the snapshot"; return 1; }
  ds_facts "ctr:$ctr" "$engine" > "$AW/got" || { echo "could not read the facts of the restore"; return 1; }
  rec_facts "$a" > "$AW/want"
  rs_compare "$AW/want" "$AW/got" atleast || return 1
  echo "$(wc -l < "$AW/want" | tr -d ' ') facts read before the snapshot are all in the restore; restored into throwaway container $ctr"
}

# rs_tar <artifact> <file or directory> <file name backup.sh recorded>
rs_tar() {
  local vol="klai-backup-restore-$1-$$" listing want_n want_h got_n
  want_n="$(rec_file "$3" entries)"; want_h="$(rec_file "$3" listing)"
  [[ -n "$want_n" && -n "$want_h" ]] || { echo "no recorded member count or listing for $3 in $RS_FACTS"; return 1; }
  listing="$(rs_stream "$2" | tar -tzf - | tar_listing_facts)" || { echo "the tar cannot be listed: cut short or damaged"; return 1; }
  [[ "$listing" == "$want_n $want_h" ]] || { echo "the tar lists ${listing%% *} members, backup recorded $want_n (or the names differ)"; return 1; }
  docker volume create --label klai.adhoc=datastore-restore "$vol" > /dev/null || return 1
  RS_VOLS+=("$vol")
  rs_stream "$2" | docker run --rm -i --network none -v "$vol:/restore" "$DS_TOOL_IMAGE" tar -xzf - -C /restore \
    || { echo "tar could not extract into the throwaway volume"; return 1; }
  got_n="$(docker run --rm --network none -v "$vol:/restore:ro" "$DS_TOOL_IMAGE" sh -c 'find /restore ! -type d | wc -l')" || return 1
  got_n="${got_n//[[:space:]]/}"
  [[ "$got_n" == "$want_n" ]] || { echo "the volume holds $got_n files after extracting, the listing has $want_n"; return 1; }
  echo "$want_n files and the member listing as recorded; restored into throwaway volume $vol"
}

# rs_path <file name>: where the plain file is, decrypting an .age copy into
# the artifact's work directory. The staging copy of research-uploads is a directory.
rs_path() {
  local f="$1" err
  if (( ENCRYPTED )); then
    [[ -e "$SET_DIR/$f.age" ]] || { echo "$f.age is missing from the set"; return 1; }
    err="$(age -d -i "$IDENTITY" -o "$AW/$f" "$SET_DIR/$f.age" 2>&1)" \
      || { rm -f "$AW/$f"; echo "decrypting $f.age failed: $(tail -n 1 <<< "$err")"; return 1; }
    echo "$AW/$f"
  elif [[ -e "$SET_DIR/$f" ]]; then
    echo "$SET_DIR/$f"
  elif [[ -d "$SET_DIR/${f%.tar.gz}" ]]; then
    echo "$SET_DIR/${f%.tar.gz}"
  else
    echo "$f is missing from the set"; return 1
  fi
}

# rs_present <file name pattern>: the set holds it, in either form.
rs_present() {
  compgen -G "$SET_DIR/$1" > /dev/null || compgen -G "$SET_DIR/$1.age" > /dev/null || [[ -d "$SET_DIR/${1%.tar.gz}" ]]
}

rs_artifact() {
  local a="$1" kind file svc base path bases=() paths=()
  read -r _ kind file svc <<< "$(rs_row "$a")"
  if [[ "$kind" == qdrant ]]; then
    while read -r base; do [[ -z "$base" ]] || bases+=("$base"); done < <(cd "$SET_DIR" && { compgen -G "$file"; compgen -G "$file.age"; } | sed 's/\.age$//' | sort -u)
    (( ${#bases[@]} )) || { echo "no $file in the set"; return 1; }
  else
    bases=("$file")
  fi
  for base in "${bases[@]}"; do
    path="$(rs_path "$base")" || { echo "$path"; return 1; }
    # research-uploads is a directory in staging and a tar stream made at
    # encryption time off-site: no checksum exists for either, its listing is the fact.
    [[ -d "$path" || "$base" == research-uploads.tar.gz ]] || rs_expect_sha "$base" "$path" || return 1
    paths+=("$path")
  done
  case "$kind" in
    pg) rs_pg "$a" "${paths[0]}" "$svc" ;;
    mongo) rs_mongo "$a" "${paths[0]}" "$svc" ;;
    rdb) rs_rdb "$a" "${paths[0]}" "$svc" "$([[ "$a" == falkordb ]] && echo falkordb || echo redis)" ;;
    meili) rs_snapshot "$a" meilisearch "$svc" "${paths[@]}" ;;
    qdrant) rs_snapshot "$a" qdrant "$svc" "${paths[@]}" ;;
    tar) rs_tar "$a" "${paths[0]}" "$file" ;;
  esac
}

main() {
  local explicit=0 names=() a file log rc ok=0 failed=0 skipped=0

  while (( $# )); do
    case "$1" in
      --identity) (( $# >= 2 )) || usage; IDENTITY="$2"; shift 2 ;;
      --keep) KEEP=1; shift ;;
      -*) usage ;;
      *) break ;;
    esac
  done
  (( $# >= 1 )) || usage
  SET_DIR="${1%/}"; shift
  [[ -d "$SET_DIR" ]] || { echo "FAIL set: $SET_DIR is not a directory"; return 2; }
  if (( $# )); then explicit=1; names=("$@"); else mapfile -t names < <(awk 'NF { print $1 }' <<< "$RS_ARTIFACTS"); fi
  for a in "${names[@]}"; do
    [[ -n "$(rs_row "$a")" ]] || { echo "FAIL $a: not an artifact of the nightly set"; return 2; }
  done

  if compgen -G "$SET_DIR/*.age" > /dev/null; then
    ENCRYPTED=1
    [[ -n "$IDENTITY" && -r "$IDENTITY" ]] || { echo "FAIL set: $SET_DIR holds .age files, so --identity <file> is required and must be readable"; return 2; }
    command -v age > /dev/null || { echo "FAIL set: age is not installed"; return 2; }
  elif [[ -n "$IDENTITY" ]]; then
    echo "FAIL set: --identity given but $SET_DIR holds no .age files"; return 2
  fi
  # Under the backup root: the decrypted copy of the largest artifact needs the
  # room of the disk the sets live on, not a small /tmp.
  mkdir -p "$DS_BACKUP_ROOT"
  WORK="$(mktemp -d "$DS_BACKUP_ROOT/.backup-restore.XXXXXX")" || { echo "FAIL set: no work directory under $DS_BACKUP_ROOT"; return 2; }

  AW="$WORK/facts"; mkdir -p "$AW"
  if log="$(rs_path "$RS_FACTS")"; then FACTS_TEXT="$(cat "$log")"; else echo "FAIL $RS_FACTS: $log; nothing can be compared"; failed=1; fi
  rm -rf "$AW"

  for a in "${names[@]}"; do
    read -r _ _ file _ <<< "$(rs_row "$a")"
    # An artifact backup.sh left out (its container was not running) is not a
    # missing file; with no facts to tell, or when named, it is.
    if [[ -n "$FACTS_TEXT" ]] && (( ! explicit )) && ! rs_present "$file" && ! rs_recorded "$file"; then
      echo "SKIP $a: backup.sh recorded nothing for it in this set"
      skipped=$((skipped + 1))
      continue
    fi
    AW="$WORK/$a"; mkdir -p "$AW"
    rs_artifact "$a" > "$AW/out" 2>&1; rc=$?
    if (( rc == 0 )); then
      echo "ok   $a: $(tail -n 1 "$AW/out")"
      ok=$((ok + 1))
    else
      echo "FAIL $a: $(tail -n 1 "$AW/out")"
      failed=$((failed + 1))
    fi
    rs_cleanup
    rm -rf "$AW"
  done
  echo "backup restore: $ok ok, $failed failed, $skipped skipped"
  (( failed == 0 ))
}

main "$@"
