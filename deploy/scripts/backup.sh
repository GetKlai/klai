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
readonly TOTAL_STEPS=16

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

record_file() {
  local file="$1"

  if [[ ! -s "${file}" ]]; then
    fail "expected artifact is missing or empty: ${file}"
  fi

  ARTIFACTS+=("${file}")
  log "      Size: $(artifact_size "${file}")"
}

record_optional_file() {
  local file="$1"
  local empty_msg="$2"

  if [[ -s "${file}" ]]; then
    ARTIFACTS+=("${file}")
    log "      Size: $(artifact_size "${file}")"
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

run_step() {
  local title="$1"
  shift

  STEP=$((STEP + 1))
  CURRENT_STEP="[${STEP}/${TOTAL_STEPS}] ${title}"
  printf '\n'
  log "${CURRENT_STEP}"
  "$@"
}

backup_postgres() {
  local output="${BACKUP_DIR}/postgres-all.sql"

  docker compose exec -T postgres pg_dumpall -U klai > "${output}"
  record_file "${output}"
}

backup_gitea() {
  local output="${BACKUP_DIR}/gitea-repos.tar.gz"

  docker run --rm \
    --volumes-from "$(ctr gitea):ro" \
    -v "${BACKUP_DIR}:/backup" \
    alpine tar -czf /backup/gitea-repos.tar.gz -C /data git/repositories gitea/conf
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
  fi

  nofile_limit="$(container_nofile_limit "${container}")"
  if [[ "${nofile_limit}" =~ ^[0-9]+$ && "${nofile_limit}" -lt "${MONGODB_MIN_NOFILE}" ]]; then
    fail "MongoDB nofile limit is ${nofile_limit}; set compose ulimits.nofile to at least ${MONGODB_MIN_NOFILE}"
  fi

  image="$(docker inspect -f '{{.Config.Image}}' "${container}")"
  before_restarts="$(container_restart_count "${container}")"
  before_started_at="$(container_started_at "${container}")"
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
  fi

  after_restarts="$(container_restart_count "${container}")"
  after_started_at="$(container_started_at "${container}")"
  if [[ "${before_restarts}" != "${after_restarts}" || "${before_started_at}" != "${after_started_at}" ]]; then
    rm -f "${tmp}" "${output}"
    fail "MongoDB restarted during mongodump (restarts ${before_restarts}->${after_restarts})"
  fi

  mv "${tmp}" "${output}"
  record_file "${output}"
}

backup_redis() {
  local output="${BACKUP_DIR}/redis-dump.rdb"

  docker compose exec -T redis redis-cli -a "${REDIS_PASSWORD}" --no-auth-warning BGSAVE
  sleep 3
  docker compose cp redis:/data/dump.rdb "${output}"
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

  password="$(docker inspect "${container}" \
    --format '{{range .Config.Env}}{{println .}}{{end}}' \
    | grep '^VEXA_REDIS_PASSWORD=' | head -1 | cut -d= -f2-)"

  if ! docker exec "${container}" \
       redis-cli ${password:+-a "${password}"} --no-auth-warning BGSAVE >/dev/null; then
    log "      FAILED (BGSAVE rejected — check VEXA_REDIS_PASSWORD)"
    return 1
  fi
  sleep 3
  docker cp "${container}:/data/dump.rdb" "${output}"
  record_file "${output}"
}

backup_meilisearch() {
  local key
  local response
  local container

  container="$(ctr meilisearch)"
  if ! container_running "${container}"; then
    log "      Skipped (container not running)"
    return 0
  fi

  key="${MEILI_MASTER_KEY:-$(secret_file_value "${SECRETS_DIR}/meili_master_key.txt")}"
  if [[ -z "${key}" ]]; then
    log "      Skipped (MEILI_MASTER_KEY is empty)"
    return 0
  fi

  if response="$(docker compose exec -T meilisearch \
      wget -qO- --post-data="" \
      --header="Authorization: Bearer ${key}" \
      http://127.0.0.1:7700/snapshots 2>/dev/null)"; then
    log "      Response: ${response}"
  else
    log "      Snapshot failed (non-fatal; Meilisearch can rebuild from MongoDB)"
  fi
}

qdrant_api() {
  local method="$1"
  local path="$2"
  local flags=()

  case "${method}" in
    GET) ;;
    POST) flags=(-X POST -d "") ;;
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
  fi

  collections_json="$(qdrant_api GET /collections)"
  collections="$(printf '%s' "${collections_json}" | json_qdrant_collections)"
  if [[ -z "${collections}" ]]; then
    log "      No collections found"
    return 0
  fi

  while IFS= read -r collection; do
    [[ -n "${collection}" ]] || continue

    response="$(qdrant_api POST "/collections/${collection}/snapshots")"
    snapshot="$(printf '%s' "${response}" | json_qdrant_snapshot_name)"
    if [[ -z "${snapshot}" ]]; then
      fail "Qdrant did not return a snapshot name for ${collection}: ${response}"
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
    fi

    if [[ -s "${output}" ]]; then
      ARTIFACTS+=("${output}")
      log "      Size: $(artifact_size "${output}")"
    else
      qdrant_api DELETE "/collections/${collection}/snapshots/${snapshot}" >/dev/null || true
      rm -f "${output}"
      fail "Qdrant snapshot download was empty for ${collection}"
    fi
    qdrant_api DELETE "/collections/${collection}/snapshots/${snapshot}" >/dev/null || true
  done <<< "${collections}"
}

backup_falkordb() {
  local container
  local output

  container="$(ctr falkordb)"
  output="${BACKUP_DIR}/falkordb-dump.rdb"

  if ! container_running "${container}"; then
    log "      Skipped (container not running)"
    return 0
  fi

  docker exec "${container}" redis-cli BGSAVE >/dev/null
  sleep 3
  docker cp "${container}:/var/lib/falkordb/data/dump.rdb" "${output}"
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

  snapshot_output="$(docker exec "${container}" /garage meta snapshot 2>&1)"
  log "      ${snapshot_output}"

  docker run --rm \
    --volumes-from "${container}:ro" \
    -v "${BACKUP_DIR}:/backup" \
    alpine sh -c '
      latest=$(ls -1td /var/lib/garage/meta/snapshots/*/ 2>/dev/null | head -1)
      test -n "${latest}"
      tar -czf /backup/garage-meta.tar.gz -C "${latest}" .
    '
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
    alpine tar -czf /backup/garage-data.tar.gz -C /data .
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

  docker exec "${container}" pg_dumpall -U firecrawl > "${output}"
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

  docker exec "${container}" pg_dumpall -U listmonk > "${output}"
  record_file "${output}"
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

backup_research_uploads() {
  local output="${BACKUP_DIR}/research-uploads"

  if [[ ! -d /opt/klai/research-uploads ]]; then
    log "      Skipped (no /opt/klai/research-uploads)"
    return 0
  fi

  rsync -a --delete /opt/klai/research-uploads/ "${output}/"
  log "      Size: $(artifact_size "${output}")"
}

encrypt_artifacts() {
  local artifact

  ENCRYPT_DIR="$(mktemp -d)"

  for artifact in "${ARTIFACTS[@]}"; do
    age -r "${AGE_RECIPIENTS[0]}" -r "${AGE_RECIPIENTS[1]}" "${artifact}" \
      > "${ENCRYPT_DIR}/$(basename "${artifact}").age"
  done

  if [[ -d "${BACKUP_DIR}/research-uploads" ]]; then
    tar -czf - -C "${BACKUP_DIR}" research-uploads \
      | age -r "${AGE_RECIPIENTS[0]}" -r "${AGE_RECIPIENTS[1]}" \
      > "${ENCRYPT_DIR}/research-uploads.tar.gz.age"
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
    "${STORAGEBOX_USER}@${STORAGEBOX_HOST}:${remote_path}/"

  log "      Uploaded to: ${STORAGEBOX_USER}@${STORAGEBOX_HOST}:${remote_path}"
}

encrypt_and_upload() {
  encrypt_artifacts
  upload_encrypted_artifacts
}

local_retention() {
  local remaining

  printf '\n'
  log "Local cleanup: removing backups older than the newest 30 days..."
  find "${BACKUP_ROOT}/" -maxdepth 1 -type d -name '20*' | sort | head -n -30 | xargs -r rm -rf
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
  run_step "listmonk uploads: tar campaign media" backup_listmonk_uploads
  run_step "Scribe audio: tar failed retry recordings" backup_scribe_audio
  run_step "Research uploads: rsync user uploads" backup_research_uploads
  run_step "Encrypt and upload to Storage Box" encrypt_and_upload

  kuma_push up "OK - $(artifact_size "${BACKUP_DIR}")"

  CURRENT_STEP="local retention"
  if ! local_retention; then
    log "Local retention failed (non-fatal; backup artifacts were already produced)"
  fi

  CURRENT_STEP="summary"
  if ! print_summary; then
    log "Summary output failed (non-fatal; backup artifacts were already produced)"
  fi
}

main "$@"
