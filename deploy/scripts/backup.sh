#!/usr/bin/env bash
# backup.sh - Daily backup for all stateful services on core-01.
#
# Inventory authority: deploy/volume-mounts.yaml (SPEC-INFRA-005).
# Every `category: data` entry with `backup: <non-skip>` is represented here.
#
# Production cron, as the `klai` user:
#   0 2 * * * /opt/klai/scripts/backup.sh >> /opt/klai/logs/backup.log 2>&1
#
# Manual production run:
#   sudo -u klai /opt/klai/scripts/backup.sh
#
# Do not run as root for the real job: only `klai` has the SSH key registered
# with the Hetzner Storage Box.

set -Eeuo pipefail

readonly BACKUP_DATE="${BACKUP_DATE:-$(date +%Y-%m-%d)}"
readonly BACKUP_ROOT="${BACKUP_ROOT:-/opt/klai/backups}"
readonly BACKUP_DIR="${BACKUP_DIR:-${BACKUP_ROOT}/${BACKUP_DATE}}"
readonly COMPOSE_DIR="${COMPOSE_DIR:-/opt/klai}"
readonly SECRETS_DIR="${SECRETS_DIR:-${COMPOSE_DIR}/secrets}"
readonly MONGODB_NETWORK="${MONGODB_NETWORK:-klai-net-mongodb}"
readonly MONGODB_MIN_NOFILE="${MONGODB_MIN_NOFILE:-64000}"
readonly QDRANT_NETWORK="${QDRANT_NETWORK:-klai-net}"
readonly TOTAL_STEPS=26

# How many day-directories stay on core-01 after a successful run.
#
# The local copy is the staging area this script dumps into before it encrypts
# and uploads, so the newest set has to exist. Keeping a tail of older sets on
# top of that only buys one thing: restoring yesterday without downloading and
# decrypting from the Storage Box first.
#
# It buys nothing for durability. /opt/klai/backups sits on the same /dev/md2
# as the data it backs up, so any failure that costs us the databases costs us
# these copies in the same moment. The retention that answers for durability is
# the Storage Box, which holds every day-set since 2026-03-27 and is pruned by
# nobody.
#
# This was 30 between 2026-03-27 and 2026-09-18, raised at the time for NEN 7510.
# The offsite copy satisfies that retention on its own and satisfies it better,
# being off-host; the local tail was paying 85 GiB for a week-and-a-bit of
# convenience. At 5.1 GiB per day-set and growing, that tail was the second
# largest consumer on a disk that reached 100% and broke a production deploy on
# 2026-09-18. Seven days keeps "something broke and we noticed late" a local
# restore, and hands the rest to the Storage Box.
readonly LOCAL_RETENTION_DAYS=7
readonly COMPLETE_MARKER=".backup-complete"
# What a restore has to reproduce, one line per fact, written while the set is
# made and encrypted with it. backup-restore.sh compares a restore against it;
# the dumps that describe themselves (Postgres, Mongo) need no line here.
readonly FACTS_FILE="backup-facts.txt"

readonly AGE_RECIPIENTS=(
  "age1lyd243tsj8j7rn2wy4hdmnya99wsf2p87fpphys9k65kammerqsqnzpsur"
  "age15ztzw9vnngkdnw0pg5tn8upplglvhzkep23sm5zu86res5lcmv7syw5m4v"
)

declare -a ARTIFACTS=()
CURRENT_STEP="startup"
ENCRYPT_DIR=""
STEP=0

log() {
  printf '[backup %s] %s\n' "$(date '+%H:%M:%S')" "$*"
}

fail() {
  log "ERROR: $*"
  return 1
}

cleanup() {
  if [[ -n "${ENCRYPT_DIR}" && -d "${ENCRYPT_DIR}" ]]; then
    rm -rf "${ENCRYPT_DIR}" || true
  fi
}

env_get() {
  local key="$1"
  grep "^${key}=" "${COMPOSE_DIR}/.env" 2>/dev/null | head -1 | cut -d= -f2- || true
}

secret_file_value() {
  local path="$1"

  if [[ -r "${path}" ]]; then
    <"${path}"
  fi
}

urlencode() {
  python3 -c 'import urllib.parse, sys; print(urllib.parse.quote(sys.argv[1]))' "$1"
}

kuma_push() {
  local status="$1"
  local msg="$2"

  [[ -z "${KUMA_TOKEN_BACKUP:-}" ]] && return 0

  curl -fsS --max-time 10 \
    "https://status.getklai.com/api/push/${KUMA_TOKEN_BACKUP}?status=${status}&msg=$(urlencode "${msg}")&ping=" \
    >/dev/null 2>&1 || true
}

on_error() {
  local code=$?
  trap - ERR
  cleanup
  kuma_push down "Backup failed during ${CURRENT_STEP} (exit ${code})"
  exit "${code}"
}

trap on_error ERR
trap cleanup EXIT

ctr() {
  printf 'klai-core-%s-1' "$1"
}

container_running() {
  local container="$1"
  [[ "$(docker inspect -f '{{.State.Running}}' "${container}" 2>/dev/null || true)" == "true" ]]
}

container_restart_count() {
  docker inspect -f '{{.RestartCount}}' "$1"
}

container_started_at() {
  docker inspect -f '{{.State.StartedAt}}' "$1"
}

container_nofile_limit() {
  docker exec "$1" sh -lc 'ulimit -n'
}

container_env_value() {
  local container="$1"
  local key="$2"

  docker inspect \
    --format '{{range .Config.Env}}{{println .}}{{end}}' \
    "${container}" 2>/dev/null \
    | awk -F= -v key="${key}" '$1 == key { sub("^[^=]+=", ""); print; exit }' \
    || true
}

artifact_size() {
  du -sh "$1" 2>/dev/null | cut -f1
}

facts_add() {
  printf '%s\n' "$*" >> "${BACKUP_DIR}/${FACTS_FILE}"
}

# "<entries> <sha256 of the sorted member names>" of a tar listing on stdin,
# directories left out. backup-restore.sh computes the same two numbers from
# the restored copy, so a tar that lists differently than it did is a failure.
tar_listing_facts() {
  python3 -c '
import hashlib
import sys

names = sorted(n for n in sys.stdin.buffer.read().split(b"\n") if n and not n.endswith(b"/"))
print(len(names), hashlib.sha256(b"\n".join(names)).hexdigest())'
}

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

# record_facts <artifact file>: its checksum, and for a tar its listing.
record_facts() {
  local file="$1" name listing
  name="$(basename "${file}")"
  facts_add "file ${name} sha256 $(file_sha256 "${file}")" || return 1
  if [[ "${name}" == *.tar.gz ]]; then
    listing="$(tar -tzf "${file}" | tar_listing_facts)" || return 1
    facts_add "file ${name} entries ${listing%% *}" || return 1
    facts_add "file ${name} listing ${listing##* }" || return 1
  fi
}

# record_redis_facts <artifact> <container> [auth args...]: the keys without a
# TTL (an expiring key can lapse between the read and the save), read just
# before the save, so a restore must hold at least that many.
record_redis_facts() {
  local artifact="$1" container="$2" info
  shift 2
  info="$(redis_q "${container}" "$@" -- INFO keyspace)" && [[ "${info}" == *Keyspace* ]] \
    || { fail "${container}: INFO keyspace failed"; return 1; }
  facts_add "facts ${artifact} count keys $(tr -d '\r' <<< "${info}" \
    | awk -F'[:=,]' '/^db[0-9]+:/ { k += $3 - $5 } END { print k + 0 }')"
}

record_file() {
  local file="$1"

  # run_step calls steps inside `if !`, where errexit does not apply, so a
  # missing artifact has to be returned, not just logged: every step ends in
  # this call and its status is the step's status.
  [[ -s "${file}" ]] || { fail "expected artifact is missing or empty: ${file}"; return 1; }

  ARTIFACTS+=("${file}")
  log "      Size: $(artifact_size "${file}")"
  record_facts "${file}"
}

record_optional_file() {
  local file="$1"
  local empty_msg="$2"

  if [[ -s "${file}" ]]; then
    ARTIFACTS+=("${file}")
    log "      Size: $(artifact_size "${file}")"
    record_facts "${file}"
  else
    rm -f "${file}"
    log "      ${empty_msg}"
  fi
}

load_config() {
  MONGO_ROOT_PASSWORD="$(<"${SECRETS_DIR}/mongo_root_password.txt")"
  REDIS_PASSWORD="$(container_env_value "$(ctr redis)" REDIS_PASSWORD)"
  STORAGEBOX_HOST="${STORAGEBOX_HOST:-$(env_get STORAGEBOX_HOST)}"
  STORAGEBOX_USER="${STORAGEBOX_USER:-$(env_get STORAGEBOX_USER)}"
  KUMA_TOKEN_BACKUP="${KUMA_TOKEN_BACKUP:-$(env_get KUMA_TOKEN_BACKUP)}"

  if [[ -z "${MONGO_ROOT_PASSWORD}" ]]; then
    fail "MONGO_ROOT_PASSWORD is empty"
  fi
  if [[ -z "${REDIS_PASSWORD}" ]]; then
    fail "REDIS_PASSWORD is empty"
  fi
}

FAILED_STEPS=()

# A failing data source must not cost us the offsite copy of everything else.
# Under `set -e` a step returning non-zero aborted the whole run, so when the
# Vexa Redis step started failing on 2026-08-14 the script never reached step
# 16 ("Encrypt and upload to Storage Box") — three nights of backups stayed on
# a single local disk while the log still showed steps 1-5 succeeding. Collect
# failures instead, keep going, and report them at the end so the run is still
# visibly red to cron and Uptime Kuma.
run_step() {
  local title="$1"
  shift

  STEP=$((STEP + 1))
  CURRENT_STEP="[${STEP}/${TOTAL_STEPS}] ${title}"
  printf '\n'
  log "${CURRENT_STEP}"
  if ! "$@"; then
    log "      STEP FAILED — continuing so later steps (incl. upload) still run"
    FAILED_STEPS+=("${title}")
    return 0
  fi
}

backup_postgres() {
  local output="${BACKUP_DIR}/postgres-all.sql"

  docker compose exec -T postgres pg_dumpall -U klai > "${output}" || return 1
  record_file "${output}"
}

backup_gitea() {
  local output="${BACKUP_DIR}/gitea-repos.tar.gz"

  docker run --rm \
    --volumes-from "$(ctr gitea):ro" \
    -v "${BACKUP_DIR}:/backup" \
    alpine tar -czf /backup/gitea-repos.tar.gz -C /data git/repositories gitea/conf || return 1
  record_file "${output}"
}

backup_mongodb() {
  local container
  local image
  local tmp
  local output
  local before_restarts
  local before_started_at
  local after_restarts
  local after_started_at
  local nofile_limit

  container="$(ctr mongodb)"
  output="${BACKUP_DIR}/mongodb-all.archive"
  tmp="${output}.partial"

  if ! container_running "${container}"; then
    fail "MongoDB container is not running"
    return 1
  fi

  nofile_limit="$(container_nofile_limit "${container}")" || return 1
  if [[ "${nofile_limit}" =~ ^[0-9]+$ && "${nofile_limit}" -lt "${MONGODB_MIN_NOFILE}" ]]; then
    fail "MongoDB nofile limit is ${nofile_limit}; set compose ulimits.nofile to at least ${MONGODB_MIN_NOFILE}"
    return 1
  fi

  # Two empty restart counters compare equal, so a failed inspect here would
  # let the restart check below pass without looking at anything.
  image="$(docker inspect -f '{{.Config.Image}}' "${container}")" || return 1
  before_restarts="$(container_restart_count "${container}")" || return 1
  before_started_at="$(container_started_at "${container}")" || return 1
  rm -f "${tmp}" "${output}"

  if ! docker run --rm \
      --network "${MONGODB_NETWORK}" \
      --user "$(id -u):$(id -g)" \
      -v "${BACKUP_DIR}:/backup" \
      "${image}" mongodump \
      --host "${container}:27017" \
      --numParallelCollections=1 \
      --username klai \
      --password "${MONGO_ROOT_PASSWORD}" \
      --authenticationDatabase admin \
      --archive="/backup/$(basename "${tmp}")"; then
    rm -f "${tmp}" "${output}"
    fail "mongodump failed"
    return 1
  fi

  after_restarts="$(container_restart_count "${container}")" || return 1
  after_started_at="$(container_started_at "${container}")" || return 1
  if [[ "${before_restarts}" != "${after_restarts}" || "${before_started_at}" != "${after_started_at}" ]]; then
    rm -f "${tmp}" "${output}"
    fail "MongoDB restarted during mongodump (restarts ${before_restarts}->${after_restarts})"
    return 1
  fi

  mv "${tmp}" "${output}" || return 1
  record_file "${output}"
}

# bgsave_and_wait <container> [redis-cli auth args...]
# Starts a background save and returns once it has finished. Every Redis-
# protocol dump here used to BGSAVE, sleep 3 s and copy: on 2026-10-22 the
# nightly falkordb-dump.rdb was three hours older than the run, because a
# 1 GB graph takes far longer than 3 s to save and the copy took the previous
# dump. LASTSAVE moves only when a save completes, so poll that instead, and
# fail loudly when the save does not finish or reports an error.
BGSAVE_TIMEOUT="${BGSAVE_TIMEOUT:-900}"
redis_q() { # redis_q <container> <auth args...> -- <command...>; fails on any error reply
  local container="$1"; shift
  local auth=()
  while [[ "$1" != "--" ]]; do auth+=("$1"); shift; done
  shift
  docker exec "${container}" redis-cli ${auth[@]+"${auth[@]}"} --no-auth-warning -e "$@"
}
bgsave_in_progress() {
  local info
  info="$(redis_q "$@" INFO persistence)" || return 2
  [[ "${info}" == *"rdb_bgsave_in_progress:1"* ]]
}
bgsave_and_wait() {
  local container="$1"
  shift
  local before now reply i rc

  # Let a save that is already running finish first, so its completion cannot
  # be mistaken for ours.
  for (( i = 0; i < BGSAVE_TIMEOUT; i++ )); do
    bgsave_in_progress "${container}" "$@" --; rc=$?
    (( rc == 2 )) && { fail "${container}: INFO persistence failed"; return 1; }
    (( rc == 1 )) && break
    sleep 1
  done
  before="$(redis_q "${container}" "$@" -- LASTSAVE)" && [[ "${before}" =~ ^[0-9]+$ ]] \
    || { fail "${container}: LASTSAVE failed"; return 1; }
  # LASTSAVE has one-second resolution: start our save in a later second than
  # the one recorded, so its completion always reads as a newer value.
  sleep 1
  reply="$(redis_q "${container}" "$@" -- BGSAVE)" && [[ "${reply}" == *"Background saving started"* ]] \
    || { fail "${container}: BGSAVE not started (${reply:-no reply})"; return 1; }
  for (( i = 0; i < BGSAVE_TIMEOUT; i++ )); do
    now="$(redis_q "${container}" "$@" -- LASTSAVE)" && [[ "${now}" =~ ^[0-9]+$ ]] \
      || { fail "${container}: LASTSAVE failed"; return 1; }
    (( now > before )) && break
    sleep 1
  done
  (( now > before )) || { fail "${container}: BGSAVE did not finish within ${BGSAVE_TIMEOUT}s"; return 1; }
  redis_q "${container}" "$@" -- INFO persistence | grep -q '^rdb_last_bgsave_status:ok' \
    || { fail "${container}: last BGSAVE reported an error"; return 1; }
}

backup_redis() {
  local output="${BACKUP_DIR}/redis-dump.rdb"

  record_redis_facts redis "$(ctr redis)" -a "${REDIS_PASSWORD}" || return 1
  bgsave_and_wait "$(ctr redis)" -a "${REDIS_PASSWORD}" || return 1
  docker compose cp redis:/data/dump.rdb "${output}" || return 1
  record_file "${output}"
}

backup_vexa_redis() {
  local container
  local output
  local password

  # SPEC-VEXA-004: vexa-redis -> vexa12-redis. The instance runs with
  # --requirepass, so an unauthenticated BGSAVE returns NOAUTH; the previous
  # version discarded that on stdout and copied whatever dump.rdb happened to be
  # on disk, so a silent no-op looked like a successful backup.
  container="$(ctr vexa12-redis)"
  output="${BACKUP_DIR}/vexa12-redis-dump.rdb"

  if ! container_running "${container}"; then
    log "      Skipped (container not running)"
    return 0
  fi

  # The password is on the COMMAND LINE (`redis-server --requirepass <x>`),
  # not in the environment — compose passes it as an argument. Reading only
  # .Config.Env yielded an empty password, so BGSAVE ran unauthenticated,
  # returned NOAUTH, and this step failed every night from 2026-08-14 on.
  # Env is still checked first so a future move to an env var keeps working.
  password="$(docker inspect "${container}" \
    --format '{{range .Config.Env}}{{println .}}{{end}}' \
    | grep '^VEXA_REDIS_PASSWORD=' | head -1 | cut -d= -f2-)"
  if [ -z "${password}" ]; then
    password="$(docker inspect "${container}" \
      --format '{{range $i, $a := .Config.Cmd}}{{println $a}}{{end}}' \
      | grep -A1 '^--requirepass$' | tail -1)"
  fi

  record_redis_facts vexa_redis "${container}" ${password:+-a "${password}"} || return 1
  bgsave_and_wait "${container}" ${password:+-a "${password}"} || return 1
  docker cp "${container}:/data/dump.rdb" "${output}" || return 1
  record_file "${output}"
}

backup_meilisearch() {
  local container
  local stats
  local output="${BACKUP_DIR}/meilisearch.snapshot"

  container="$(ctr meilisearch)"
  if ! container_running "${container}"; then
    log "      Skipped (container not running)"
    return 0
  fi

  # The master key exists only in the container's environment, so this script
  # never had it: every night logged "Skipped (MEILI_MASTER_KEY is empty)" and
  # no Meilisearch backup existed (found 2026-10-22). Even a successful call
  # left the snapshot inside the container volume, outside the backup set. The
  # snapshot now runs inside the container with its own key, waits for the
  # task and is copied out like every other artifact. The documents per index
  # are read first: the snapshot holds at least that many.
  stats="$(docker exec "${container}" sh -c \
    'wget -qO- --header="Authorization: Bearer ${MEILI_MASTER_KEY:?}" http://127.0.0.1:7700/stats')" || return 1
  printf '%s' "${stats}" | python3 -c '
import json
import sys

for uid, s in json.load(sys.stdin)["indexes"].items():
    print("facts meilisearch index", uid)
    print("facts meilisearch count", uid, s["numberOfDocuments"])' >> "${BACKUP_DIR}/${FACTS_FILE}" || return 1
  if ! docker exec "${container}" sh -c '
      auth="Authorization: Bearer ${MEILI_MASTER_KEY:?}"
      uid=$(wget -qO- --post-data="" --header="$auth" http://127.0.0.1:7700/snapshots \
        | sed -n "s/.*\"taskUid\":\([0-9]*\).*/\1/p")
      [ -n "$uid" ] || exit 1
      i=0
      while [ $i -lt 600 ]; do
        status=$(wget -qO- --header="$auth" "http://127.0.0.1:7700/tasks/$uid" \
          | sed -n "s/.*\"status\":\"\([a-z]*\)\".*/\1/p")
        [ "$status" = succeeded ] && exit 0
        [ "$status" = failed ] || [ "$status" = canceled ] && exit 1
        i=$((i + 1)); sleep 1
      done
      exit 1'; then
    fail "Meilisearch snapshot did not complete"
    return 1
  fi
  docker cp "${container}:/meili_data/snapshots/meili_data.snapshot" "${output}" || return 1
  record_file "${output}"
}

qdrant_api() {
  local method="$1"
  local path="$2"
  local flags=()

  case "${method}" in
    GET) ;;
    POST)
      flags=(-X POST -d "${3:-}")
      [[ -z "${3:-}" ]] || flags+=(-H "Content-Type: application/json")
      ;;
    DELETE) flags=(-X DELETE) ;;
    *) fail "unknown Qdrant API method: ${method}" ;;
  esac

  docker run --rm --network "${QDRANT_NETWORK}" curlimages/curl:8.11.1 \
    -sSf --max-time 30 \
    "${flags[@]}" \
    -H "api-key: ${QDRANT_API_KEY}" \
    "http://$(ctr qdrant):6333${path}"
}

json_qdrant_collections() {
  python3 -c '
import json
import sys

data = json.load(sys.stdin)
for collection in data.get("result", {}).get("collections", []):
    name = collection.get("name")
    if name:
        print(name)
'
}

json_qdrant_snapshot_name() {
  python3 -c '
import json
import sys

print(json.load(sys.stdin).get("result", {}).get("name", ""))
'
}

backup_qdrant() {
  local container
  local collections_json
  local collections
  local collection
  local response
  local points
  local snapshot
  local output

  container="$(ctr qdrant)"
  if ! container_running "${container}"; then
    log "      Skipped (container not running)"
    return 0
  fi

  QDRANT_API_KEY="${QDRANT_API_KEY:-$(container_env_value "${container}" QDRANT__SERVICE__API_KEY)}"
  if [[ -z "${QDRANT_API_KEY}" ]]; then
    fail "Qdrant is running but QDRANT__SERVICE__API_KEY is empty"
    return 1
  fi

  # A failed listing would otherwise read as "No collections found" below.
  collections_json="$(qdrant_api GET /collections)" || return 1
  collections="$(printf '%s' "${collections_json}" | json_qdrant_collections)" || return 1
  if [[ -z "${collections}" ]]; then
    log "      No collections found"
    return 0
  fi

  while IFS= read -r collection; do
    [[ -n "${collection}" ]] || continue

    # The points per collection are read first: the snapshot holds at least
    # that many.
    points="$(qdrant_api POST "/collections/${collection}/points/count" '{"exact": true}' \
      | python3 -c 'import json, sys; print(json.load(sys.stdin)["result"]["count"])')" || return 1
    facts_add "facts qdrant collection ${collection}"
    facts_add "facts qdrant count ${collection} ${points}"

    response="$(qdrant_api POST "/collections/${collection}/snapshots")" || return 1
    snapshot="$(printf '%s' "${response}" | json_qdrant_snapshot_name)" || return 1
    if [[ -z "${snapshot}" ]]; then
      fail "Qdrant did not return a snapshot name for ${collection}: ${response}"
      return 1
    fi

    output="${BACKUP_DIR}/qdrant-${collection}.snapshot"
    if ! docker run --rm --network "${QDRANT_NETWORK}" \
        --user 0:0 \
        -v "${BACKUP_DIR}:/out" \
        curlimages/curl:8.11.1 \
        -sSfL --max-time 300 \
        -H "api-key: ${QDRANT_API_KEY}" \
        "http://${container}:6333/collections/${collection}/snapshots/${snapshot}" \
        -o "/out/$(basename "${output}")"; then
      qdrant_api DELETE "/collections/${collection}/snapshots/${snapshot}" >/dev/null || true
      rm -f "${output}"
      fail "Qdrant snapshot download failed for ${collection}"
      return 1
    fi

    if [[ -s "${output}" ]]; then
      ARTIFACTS+=("${output}")
      log "      Size: $(artifact_size "${output}")"
      record_facts "${output}" || return 1
    else
      qdrant_api DELETE "/collections/${collection}/snapshots/${snapshot}" >/dev/null || true
      rm -f "${output}"
      fail "Qdrant snapshot download was empty for ${collection}"
      return 1
    fi
    qdrant_api DELETE "/collections/${collection}/snapshots/${snapshot}" >/dev/null || true
  done <<< "${collections}"
}

backup_falkordb() {
  local container
  local output
  local graphs
  local graph

  container="$(ctr falkordb)"
  output="${BACKUP_DIR}/falkordb-dump.rdb"

  if ! container_running "${container}"; then
    log "      Skipped (container not running)"
    return 0
  fi

  record_redis_facts falkordb "${container}" || return 1
  graphs="$(redis_q "${container}" -- GRAPH.LIST)" || return 1
  while IFS= read -r graph; do
    [[ -z "${graph}" ]] || facts_add "facts falkordb graph ${graph}"
  done <<< "${graphs}"
  bgsave_and_wait "${container}" || return 1
  docker cp "${container}:/var/lib/falkordb/data/dump.rdb" "${output}" || return 1
  record_file "${output}"
}

backup_garage_meta() {
  local container
  local output
  local snapshot_output

  container="$(ctr garage)"
  output="${BACKUP_DIR}/garage-meta.tar.gz"

  if ! container_running "${container}"; then
    log "      Skipped (container not running)"
    return 0
  fi

  # A failed snapshot must stop here: the tar below would otherwise pack the
  # newest older snapshot and report it as tonight's.
  snapshot_output="$(docker exec "${container}" /garage meta snapshot 2>&1)" \
    || { fail "Garage metadata snapshot failed: ${snapshot_output}"; return 1; }
  log "      ${snapshot_output}"

  docker run --rm \
    --volumes-from "${container}:ro" \
    -v "${BACKUP_DIR}:/backup" \
    alpine sh -c '
      latest=$(ls -1td /var/lib/garage/meta/snapshots/*/ 2>/dev/null | head -1)
      test -n "${latest}"
      tar -czf /backup/garage-meta.tar.gz -C "${latest}" .
    ' || return 1
  record_file "${output}"
}

backup_garage_data() {
  local output="${BACKUP_DIR}/garage-data.tar.gz"

  if [[ ! -d /opt/klai/garage-data ]]; then
    log "      Skipped (no /opt/klai/garage-data)"
    return 0
  fi

  docker run --rm \
    -v /opt/klai/garage-data:/data:ro \
    -v "${BACKUP_DIR}:/backup" \
    alpine tar -czf /backup/garage-data.tar.gz -C /data . || return 1
  record_file "${output}"
}

backup_firecrawl_postgres() {
  local container
  local output

  container="$(ctr firecrawl-postgres)"
  output="${BACKUP_DIR}/firecrawl-postgres-all.sql"

  if ! container_running "${container}"; then
    log "      Skipped (container not running)"
    return 0
  fi

  docker exec "${container}" pg_dumpall -U firecrawl > "${output}" || return 1
  record_file "${output}"
}

backup_listmonk_postgres() {
  local container
  local output

  container="$(ctr listmonk-db)"
  output="${BACKUP_DIR}/listmonk-postgres-all.sql"

  if ! container_running "${container}"; then
    log "      Skipped (container not running)"
    return 0
  fi

  docker exec "${container}" pg_dumpall -U listmonk > "${output}" || return 1
  record_file "${output}"
}

backup_crm_postgres() {
  local container
  local output

  container="$(ctr crm-postgres)"
  output="${BACKUP_DIR}/crm-postgres-all.sql"

  if ! container_running "${container}"; then
    log "      Skipped (container not running)"
    return 0
  fi

  docker exec "${container}" pg_dumpall -U twenty > "${output}" || return 1
  record_file "${output}"
}

backup_fider_postgres() {
  local container
  local output

  container="$(ctr fider-postgres)"
  output="${BACKUP_DIR}/fider-postgres-all.sql"

  if ! container_running "${container}"; then
    log "      Skipped (container not running)"
    return 0
  fi

  docker exec "${container}" pg_dumpall -U fider > "${output}" || return 1
  record_file "${output}"
}

backup_umami_postgres() {
  local container
  local output

  container="$(ctr umami-postgres)"
  output="${BACKUP_DIR}/umami-postgres-all.sql"

  if ! container_running "${container}"; then
    log "      Skipped (container not running)"
    return 0
  fi

  docker exec "${container}" pg_dumpall -U umami > "${output}" || return 1
  record_file "${output}"
}

backup_crm_storage() {
  local container
  local output

  container="$(ctr crm)"
  output="${BACKUP_DIR}/crm-storage.tar.gz"

  if ! container_running "${container}"; then
    log "      Skipped (container not running)"
    return 0
  fi

  if docker run --rm \
      --volumes-from "${container}:ro" \
      -v "${BACKUP_DIR}:/backup" \
      alpine tar -czf /backup/crm-storage.tar.gz -C /app/packages/twenty-server/.local-storage . 2>/dev/null; then
    record_optional_file "${output}" "Empty (no CRM uploads)"
  else
    rm -f "${output}"
    log "      Tar failed; optional CRM uploads artifact skipped"
  fi
}

backup_listmonk_uploads() {
  local container
  local output

  container="$(ctr listmonk)"
  output="${BACKUP_DIR}/listmonk-uploads.tar.gz"

  if ! container_running "${container}"; then
    log "      Skipped (container not running)"
    return 0
  fi

  if docker run --rm \
      --volumes-from "${container}:ro" \
      -v "${BACKUP_DIR}:/backup" \
      alpine tar -czf /backup/listmonk-uploads.tar.gz -C /listmonk/uploads . 2>/dev/null; then
    record_optional_file "${output}" "Empty (no uploaded campaign media)"
  else
    rm -f "${output}"
    log "      Tar failed; optional listmonk uploads artifact skipped"
  fi
}

backup_scribe_audio() {
  local container
  local output

  container="$(ctr scribe-api)"
  output="${BACKUP_DIR}/scribe-audio.tar.gz"

  if ! container_running "${container}"; then
    log "      Skipped (container not running)"
    return 0
  fi

  if docker run --rm \
      --volumes-from "${container}:ro" \
      -v "${BACKUP_DIR}:/backup" \
      alpine tar -czf /backup/scribe-audio.tar.gz -C /data/audio . 2>/dev/null; then
    record_optional_file "${output}" "Empty (no retained recordings)"
  else
    rm -f "${output}"
    log "      Tar failed; optional scribe audio artifact skipped"
  fi
}

# The team password manager. Its SQLite database is copied with Vaultwarden's
# own `/vaultwarden backup` (upstream src/db/mod.rs backup_sqlite, since
# 1.32.1): VACUUM INTO on a read-only connection, a transactionally consistent
# copy taken while the server keeps running, which a file copy of db.sqlite3
# is not while part of the data still sits in db.sqlite3-wal. It runs inside
# the container because the image ships no sqlite3 (upstream wiki, "Backing up
# your vault") and the host's sqlite3 cannot open the root-owned volume as
# klai; public-01's backup-kuma.sh takes Uptime Kuma's copy the same way. The
# rest of what the wiki says to keep (rsa_key*, attachments, sends,
# config.json) goes into a tar; icon_cache is refetched and left out.
#
# Both artifacts are made under .partial names and recorded only when both
# exist and the copy passes integrity_check, so a failure ships neither and
# the run ends red. Unlike the optional services above, a stopped container is
# a failure too: a night without this copy must not look green.
backup_vaultwarden() {
  local container db files out copy check

  container="$(ctr vaultwarden)"
  db="${BACKUP_DIR}/vaultwarden-db.sqlite3"
  files="${BACKUP_DIR}/vaultwarden-files.tar.gz"
  rm -f "${db}" "${files}" "${db}.partial" "${files}.partial"

  if ! container_running "${container}"; then
    fail "Vaultwarden container is not running"
    return 1
  fi

  out="$(docker exec "${container}" /vaultwarden backup 2>&1)" \
    || { fail "vaultwarden backup failed: ${out}"; return 1; }
  copy="$(sed -n "s/^Backup to '\(\/data\/db_[0-9_]*\.sqlite3\)' was successful$/\1/p" <<< "${out}")"
  [[ -n "${copy}" ]] || { fail "vaultwarden backup named no copy: ${out}"; return 1; }
  # Created 0600 before any data lands in it: the backup directory is readable
  # by other local users, and docker cp would otherwise write it 0644.
  if ! { install -m 600 /dev/null "${db}.partial" && docker cp "${container}:${copy}" - | tar -xOf - > "${db}.partial"; }; then
    docker exec "${container}" rm -f "${copy}" || true
    rm -f "${db}.partial"
    fail "copying ${copy} out of the vaultwarden container failed"
    return 1
  fi
  # The copy holds the same data as the live database in the same volume, so
  # one left behind is no exposure, but it must not pile up night after night.
  docker exec "${container}" rm -f "${copy}" \
    || log "      WARNING: ${copy} stays in the vaultwarden volume; remove it by hand"
  check="$(sqlite3 "${db}.partial" 'PRAGMA integrity_check;' 2>&1)"
  if [[ "${check}" != ok ]]; then
    rm -f "${db}.partial"
    fail "the vaultwarden copy fails integrity_check: ${check}"
    return 1
  fi

  # rsa_key.pem exists from the server's first start on, so its absence means
  # the wrong directory, not an empty vault.
  if ! docker run --rm \
      --volumes-from "${container}:ro" \
      -v "${BACKUP_DIR}:/backup" \
      -e OWNER="$(id -u):$(id -g)" \
      alpine sh -c '
        set -e
        umask 077
        cd /data
        test -s rsa_key.pem
        set --
        for f in rsa_key* attachments sends config.json; do
          if [ -e "$f" ]; then set -- "$@" "$f"; fi
        done
        tar -czf /backup/vaultwarden-files.tar.gz.partial "$@"
        chown "$OWNER" /backup/vaultwarden-files.tar.gz.partial
      '; then
    rm -f "${db}.partial" "${files}.partial"
    fail "the vaultwarden files tar failed"
    return 1
  fi

  if ! { mv "${db}.partial" "${db}" && mv "${files}.partial" "${files}"; }; then
    rm -f "${db}" "${files}" "${db}.partial" "${files}.partial"
    return 1
  fi
  record_file "${db}" && record_file "${files}"
}

# The SQLite online backup API (sqlite.org/backup.html) through Python's
# sqlite3.Connection.backup, in one step: a read transaction, so the copy is a
# consistent snapshot of the database including what still sits in its WAL,
# taken while the service keeps writing. A file copy of the database and its
# -wal is not: a checkpoint between the two reads tears the pair. The copy is
# made in the helper's own /tmp and switched to a rollback journal, so it is
# one self-contained file, and streamed out.
readonly SQLITE_COPY_PY='
import os, shutil, sqlite3, sys, tempfile

src = sqlite3.connect("file:" + os.environ["DB"] + "?mode=ro", uri=True)
with tempfile.TemporaryDirectory() as tmp:
    dst = sqlite3.connect(os.path.join(tmp, "copy.sqlite3"))
    src.backup(dst)
    dst.execute("PRAGMA journal_mode=DELETE")
    dst.close()
    with open(os.path.join(tmp, "copy.sqlite3"), "rb") as f:
        shutil.copyfileobj(f, sys.stdout.buffer)
'

# sqlite_online_copy <container> <database path inside it> <output>
#
# Neither the Grafana nor the Calnode image ships sqlite3, a stock alpine has
# none without a network to install it from, and the host's sqlite3 cannot open
# a root-owned volume as klai (all checked on core-01, 2026-11-25). The
# portal-api image has Python with the sqlite3 module and is on the host by
# construction: its container's own image ID is used, so nothing is pulled at
# 02:00 and the tool is exactly the bytes already running. The helper runs on
# --network none with the service's volumes mounted read-write: a WAL reader
# takes locks and writes its read mark into the -shm file, which a read-only
# mount refuses. mode=ro keeps the database itself untouched, and nothing is
# written into the volume. The output is created 0600 before data lands, made
# under .partial and recorded only after integrity_check passes, so a failure
# ships nothing and fails the step. A stopped service is a failure: a night
# without this copy must not look green.
sqlite_online_copy() {
  local container="$1" db="$2" out="$3" tool check

  rm -f "${out}" "${out}.partial"
  container_running "${container}" || { fail "${container} is not running"; return 1; }
  tool="$(docker inspect -f '{{.Image}}' "$(ctr portal-api)")" && [[ -n "${tool}" ]] \
    || { fail "no portal-api container to take the SQLite tool image from"; return 1; }
  if ! { install -m 600 /dev/null "${out}.partial" \
      && docker run --rm --network none --user 0:0 \
        --volumes-from "${container}" \
        -e DB="${db}" \
        --entrypoint python3 "${tool}" -c "${SQLITE_COPY_PY}" > "${out}.partial"; }; then
    rm -f "${out}.partial"
    fail "the online copy of ${db} in ${container} failed"
    return 1
  fi
  check="$(sqlite3 "${out}.partial" 'PRAGMA integrity_check;' 2>&1)"
  if [[ "${check}" != ok ]]; then
    rm -f "${out}.partial"
    fail "the copy of ${db} fails integrity_check: ${check}"
    return 1
  fi
  mv "${out}.partial" "${out}" || { rm -f "${out}" "${out}.partial"; return 1; }
  record_file "${out}"
}

# Dashboards, alert rules and data sources are provisioned from the repository,
# but grafana.db also holds what is made in the UI: service accounts and their
# tokens, users and preferences, silences, annotations, unprovisioned
# dashboards. It runs in rollback-journal mode (Grafana's [database] wal
# defaults to false), so the copy holds a read lock for the moment it takes and
# Grafana retries a locked write (transaction_retries, default 5). plugins/ is
# left out: Grafana's background installer puts back the pinned
# GF_INSTALL_PLUGINS entry and its default preinstall list at every start, and
# the image bundles the core data sources. unified-search is an index Grafana
# rebuilds; png, pdf and csv are render output.
backup_grafana() {
  sqlite_online_copy "$(ctr grafana)" /var/lib/grafana/grafana.db "${BACKUP_DIR}/grafana-db.sqlite3"
}

# Bookings, event types and the encrypted calendar and SMTP credentials. The
# database is in WAL mode, with recent bookings in calnode.db-wal. Calnode's
# only documented backup is its Litestream replica, which is not configured
# here. The key that decrypts the credentials is CALNODE_ENCRYPTION_KEY in the
# deploy secrets, not in this volume.
backup_calnode() {
  sqlite_online_copy "$(ctr calnode)" /data/calnode.db "${BACKUP_DIR}/calnode-db.sqlite3"
}

# tar_running_dir <container> <directory inside it> <output>: a gzip tar of a
# directory of a service that must be running, made through a read-only mount,
# owner-only before data lands and under .partial until it is complete.
tar_running_dir() {
  local container="$1" dir="$2" out="$3"

  rm -f "${out}" "${out}.partial"
  container_running "${container}" || { fail "${container} is not running"; return 1; }
  if ! docker run --rm --network none \
      --volumes-from "${container}:ro" \
      -v "${BACKUP_DIR}:/backup" \
      -e OWNER="$(id -u):$(id -g)" \
      -e DIR="${dir}" \
      -e OUT="/backup/$(basename "${out}").partial" \
      alpine sh -c 'set -e; umask 077; tar -czf "$OUT" -C "$DIR" .; chown "$OWNER" "$OUT"'; then
    rm -f "${out}.partial"
    fail "the tar of ${dir} in ${container} failed"
    return 1
  fi
  mv "${out}.partial" "${out}" || { rm -f "${out}" "${out}.partial"; return 1; }
  record_file "${out}"
}

# Certificates, their keys and the ACME account. Caddy would ask for them all
# again, but at once and for every host name, against Let's Encrypt's 300 new
# orders per account per 3 hours and 50 certificates per registered domain per
# week; past that, HTTPS stays down for up to a week.
backup_caddy_data() {
  tar_running_dir "$(ctr caddy)" /data "${BACKUP_DIR}/caddy-data.tar.gz"
}

# The per-tenant site blocks. portal-api and the docs app write each one once,
# when a tenant or a custom domain is provisioned, and nothing writes them
# again, so a lost file is a tenant without a route until someone re-provisions it.
backup_caddy_tenants() {
  tar_running_dir "$(ctr caddy)" /etc/caddy/tenants "${BACKUP_DIR}/caddy-tenants.tar.gz"
}

# Attachments on error reports. The events they belong to are in Postgres.
backup_glitchtip_uploads() {
  tar_running_dir "$(ctr glitchtip-web)" /code/uploads "${BACKUP_DIR}/glitchtip-uploads.tar.gz"
}

backup_research_uploads() {
  local output="${BACKUP_DIR}/research-uploads"
  local listing

  if [[ ! -d /opt/klai/research-uploads ]]; then
    log "      Skipped (no /opt/klai/research-uploads)"
    return 0
  fi

  rsync -a --delete /opt/klai/research-uploads/ "${output}/" || return 1
  log "      Size: $(artifact_size "${output}")"
  listing="$(tar -cf - -C "${BACKUP_DIR}" research-uploads | tar -tf - | tar_listing_facts)" || return 1
  facts_add "file research-uploads.tar.gz entries ${listing%% *}"
  facts_add "file research-uploads.tar.gz listing ${listing##* }"
}

encrypt_artifacts() {
  local artifact

  ENCRYPT_DIR="$(mktemp -d)" || return 1

  # The facts travel with the set they describe.
  [[ ! -s "${BACKUP_DIR}/${FACTS_FILE}" ]] || ARTIFACTS+=("${BACKUP_DIR}/${FACTS_FILE}")
  for artifact in "${ARTIFACTS[@]}"; do
    age -r "${AGE_RECIPIENTS[0]}" -r "${AGE_RECIPIENTS[1]}" "${artifact}" \
      > "${ENCRYPT_DIR}/$(basename "${artifact}").age" || return 1
  done

  if [[ -d "${BACKUP_DIR}/research-uploads" ]]; then
    tar -czf - -C "${BACKUP_DIR}" research-uploads \
      | age -r "${AGE_RECIPIENTS[0]}" -r "${AGE_RECIPIENTS[1]}" \
      > "${ENCRYPT_DIR}/research-uploads.tar.gz.age" || return 1
  fi
}

upload_encrypted_artifacts() {
  local remote_path

  if [[ -z "${STORAGEBOX_HOST:-}" || -z "${STORAGEBOX_USER:-}" ]]; then
    log "      Storage Box is not configured; upload skipped."
    log "      Set STORAGEBOX_HOST and STORAGEBOX_USER in ${COMPOSE_DIR}/.env."
    return 0
  fi

  remote_path="backups/core-01/${BACKUP_DATE}"
  rsync \
    -e "ssh -p 23 -o StrictHostKeyChecking=accept-new -o ConnectTimeout=30" \
    -az --mkpath --stats \
    "${ENCRYPT_DIR}/" \
    "${STORAGEBOX_USER}@${STORAGEBOX_HOST}:${remote_path}/" || return 1

  log "      Uploaded to: ${STORAGEBOX_USER}@${STORAGEBOX_HOST}:${remote_path}"
}

encrypt_and_upload() {
  encrypt_artifacts || return 1
  upload_encrypted_artifacts
}

local_retention() {
  local remaining oldest_kept

  printf '\n'

  # main() creates the dated directory before the first step runs, so a failed
  # night leaves one behind and nothing ever removes it — retention is skipped
  # precisely when steps failed. Counting directories would therefore let a run
  # of bad nights evict good sets: at seven days, six consecutive failures would
  # leave a single restorable set, and the Vexa Redis step already failed three
  # nights running in August 2026. So count completed sets, not directories.
  #
  # This function is only reached when no step failed, which is what makes the
  # marker true by construction.
  : >"${BACKUP_DIR}/${COMPLETE_MARKER}"

  oldest_kept="$(find "${BACKUP_ROOT}" -mindepth 2 -maxdepth 2 -name "${COMPLETE_MARKER}" \
    | sed "s|/${COMPLETE_MARKER}\$||" | sort | tail -n "${LOCAL_RETENTION_DAYS}" | head -n 1)"

  # Only reachable if the marker write above failed, since that write happens
  # first. Keep everything rather than delete on an unreadable inventory.
  if [ -z "${oldest_kept}" ]; then
    log "Local cleanup skipped: no completed backup set found under ${BACKUP_ROOT}"
    return 1
  fi

  log "Local cleanup: keeping the newest ${LOCAL_RETENTION_DAYS} completed sets (from $(basename "${oldest_kept}"))..."
  # Lexical comparison is date comparison for YYYY-MM-DD directory names. This
  # also reaps the partial directories left by failed nights once they fall
  # outside the window, which the count-based form never did.
  find "${BACKUP_ROOT}/" -maxdepth 1 -type d -name '20*' \
    | sort | awk -v cut="${oldest_kept}" '$0 < cut' | xargs -r rm -rf
  remaining="$(find "${BACKUP_ROOT}/" -maxdepth 1 -type d -name '20*' | wc -l)"
  log "Local backups retained: ${remaining}"
}

print_summary() {
  printf '\n'
  log "============================================"
  log "Backup completed: ${BACKUP_DIR}"
  log "============================================"
  ls -lh "${BACKUP_DIR}"
}

main() {
  printf '\n'
  log "============================================"
  log "Starting backup: ${BACKUP_DATE}"
  log "============================================"

  mkdir -p "${BACKUP_DIR}"
  # Two runs at once (the 02:00 cron and a manual run) interleave on the same
  # dated directory: one clears the marker, the other writes it back after it
  # succeeds, and the first then overwrites artifacts and fails, leaving a
  # complete-looking broken set. Refuse instead of waiting, so the losing run
  # touches nothing. It does not push to Kuma: the running backup reports its
  # own result, and may already have pushed "up" before its retention step, so
  # a "down" from here would leave a good night red. Without flock (only the
  # macOS test runs) the run proceeds unlocked: no backup at all is worse than
  # an unlikely race. The lock is on BACKUP_ROOT itself, opened read-only: a
  # lock file created by one root run would be unwritable for klai and fail
  # every later night.
  if command -v flock >/dev/null 2>&1; then
    exec 9<"${BACKUP_ROOT}"
    if ! flock -n 9; then
      log "ERROR: another backup is running (lock on ${BACKUP_ROOT} is held); not starting"
      exit 1
    fi
  else
    log "WARNING: flock not found; running without the single-run lock"
  fi
  # A second run on the same date overwrites this directory's artifacts. A
  # marker left by an earlier successful run would then mark a failed rerun's
  # half-overwritten set as complete, and retention could drop an older good
  # set in its favour. Only this run's own success may write it again.
  rm -f "${BACKUP_DIR}/${COMPLETE_MARKER}" "${BACKUP_DIR}/${FACTS_FILE}"
  cd "${COMPOSE_DIR}"
  load_config

  run_step "PostgreSQL: dump all databases" backup_postgres
  run_step "Gitea: repositories and config" backup_gitea
  run_step "MongoDB: dump all databases" backup_mongodb
  run_step "Redis: BGSAVE and copy dump" backup_redis
  run_step "Vexa Redis: BGSAVE and copy dump" backup_vexa_redis
  run_step "Meilisearch: create snapshot" backup_meilisearch
  run_step "Qdrant: snapshot collections" backup_qdrant
  run_step "FalkorDB: BGSAVE and copy dump" backup_falkordb
  run_step "Garage: metadata snapshot" backup_garage_meta
  run_step "Garage: blob data tar" backup_garage_data
  run_step "Firecrawl Postgres: dump all databases" backup_firecrawl_postgres
  run_step "listmonk Postgres: dump all databases" backup_listmonk_postgres
  run_step "Twenty CRM Postgres: dump all databases" backup_crm_postgres
  run_step "listmonk uploads: tar campaign media" backup_listmonk_uploads
  run_step "Twenty CRM uploads: tar avatars and attachments" backup_crm_storage
  run_step "Umami Postgres: dump all databases" backup_umami_postgres
  run_step "Fider Postgres: dump all databases" backup_fider_postgres
  run_step "Scribe audio: tar failed retry recordings" backup_scribe_audio
  run_step "Vaultwarden: SQLite copy and key files" backup_vaultwarden
  run_step "Grafana: SQLite online copy" backup_grafana
  run_step "Calnode: SQLite online copy" backup_calnode
  run_step "Caddy: tar certificates and ACME account" backup_caddy_data
  run_step "Caddy: tar per-tenant site blocks" backup_caddy_tenants
  run_step "GlitchTip: tar error-report attachments" backup_glitchtip_uploads
  run_step "Research uploads: rsync user uploads" backup_research_uploads
  run_step "Encrypt and upload to Storage Box" encrypt_and_upload

  if [ ${#FAILED_STEPS[@]} -gt 0 ]; then
    kuma_push down "${#FAILED_STEPS[@]} step(s) failed: ${FAILED_STEPS[*]}"
  else
    kuma_push up "OK - $(artifact_size "${BACKUP_DIR}")"
  fi

  if [ ${#FAILED_STEPS[@]} -gt 0 ]; then
    log "Local retention skipped because this backup has failed steps"
  else
    CURRENT_STEP="local retention"
    if ! local_retention; then
      log "Local retention failed (non-fatal; backup artifacts were already produced)"
    fi
  fi

  CURRENT_STEP="summary"
  if ! print_summary; then
    log "Summary output failed (non-fatal; backup artifacts were already produced)"
  fi

  # Exit non-zero AFTER the upload so cron mail and Kuma see the failure while
  # the artifacts we did manage to collect are already safely offsite.
  if [ ${#FAILED_STEPS[@]} -gt 0 ]; then
    log "Backup finished with ${#FAILED_STEPS[@]} failed step(s): ${FAILED_STEPS[*]}"
    return 1
  fi
}

# Sourced by deploy/scripts/tests/backup-bgsave-wait.test.sh to reach the helpers.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
