#!/usr/bin/env bash
# backup.sh — Daily backup for all stateful services on core-01
#
# Inventory authority: deploy/volume-mounts.yaml (SPEC-INFRA-005).
# Every `category: data` entry with `backup: <non-skip>` is produced here.
#
# What gets backed up:
#   1. PostgreSQL       — pg_dumpall → postgres-all.sql
#                         (includes klai, zitadel, litellm, gitea, glitchtip)
#   2. Gitea repos      — git repos + config → gitea-repos.tar.gz
#                         (KB source of truth; repos are content, DB is metadata)
#   3. MongoDB          — mongodump archive
#   4. Redis            — BGSAVE + dump.rdb copy
#   5. Vexa Redis       — BGSAVE + dump.rdb copy
#   6. Meilisearch      — snapshot trigger (written to volume)
#   7. Qdrant           — snapshot per collection via API (zero downtime)
#   8. FalkorDB         — BGSAVE + dump.rdb copy (knowledge graph)
#   9. Garage meta      — `garage meta snapshot` (LMDB) + tar
#  10. Garage data      — rsync blobs (immutable once written, live-safe)
#  11. Firecrawl PG     — pg_dumpall → firecrawl-postgres-all.sql
#  12. Scribe audio     — tar of /data/audio (retention handled by app; backup
#                         of `failed` recordings only, successes auto-deleted)
#  13. Research uploads — rsync of /opt/klai/research-uploads
#  14. Encrypt + upload — age-encrypt then rsync to Hetzner Storage Box
#
# Usage:
#   ./scripts/backup.sh                 # manual run
#   Cron: 0 2 * * * /opt/klai/scripts/backup.sh >> /opt/klai/logs/backup.log 2>&1
#
# Required env vars (loaded from /opt/klai/.env):
#   MONGO_ROOT_PASSWORD   — MongoDB root password
#   REDIS_PASSWORD        — Redis password
#   MEILI_MASTER_KEY      — Meilisearch master key
#   QDRANT_API_KEY        — Qdrant API key (header x-api-key)
#
# Optional env vars (for Storage Box upload):
#   STORAGEBOX_HOST       — e.g. uXXXXXX.your-storagebox.de
#   STORAGEBOX_USER       — e.g. uXXXXXX
#
set -euo pipefail

BACKUP_DATE=$(date +%Y-%m-%d)
BACKUP_DIR="/opt/klai/backups/${BACKUP_DATE}"
COMPOSE_DIR="/opt/klai"
LOG_PREFIX="[backup $(date '+%H:%M:%S')]"

# Load secrets from their canonical locations
SECRETS_DIR="$COMPOSE_DIR/secrets"
MONGO_ROOT_PASSWORD=$(cat "$SECRETS_DIR/mongo_root_password.txt")
MEILI_MASTER_KEY=$(cat "$SECRETS_DIR/meili_master_key.txt")
# Redis password lives in the container environment (set at container creation from .env)
REDIS_PASSWORD=$(docker inspect klai-core-redis-1 \
  --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep '^REDIS_PASSWORD=' | head -1 | cut -d'=' -f2-)
# Qdrant API key from the same source (read from the running container — matches
# what retrieval-api/knowledge-ingest are actually using).
QDRANT_API_KEY=$(docker inspect klai-core-qdrant-1 \
  --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep '^QDRANT__SERVICE__API_KEY=' | head -1 | cut -d'=' -f2- || true)
# Storage Box config from .env
_env_get() { grep "^${1}=" "$COMPOSE_DIR/.env" 2>/dev/null | head -1 | cut -d'=' -f2- || true; }
STORAGEBOX_HOST=${STORAGEBOX_HOST:-$(_env_get STORAGEBOX_HOST)}
STORAGEBOX_USER=${STORAGEBOX_USER:-$(_env_get STORAGEBOX_USER)}
KUMA_TOKEN_BACKUP=${KUMA_TOKEN_BACKUP:-$(_env_get KUMA_TOKEN_BACKUP)}

# Container-name helper. Compose prefixes names with `klai-core-` + `-1`.
_ctr() { echo "klai-core-${1}-1"; }

# ─── Uptime Kuma heartbeat ────────────────────────────────────────────────────
# Push on success (at end of script). Push failure on EXIT with non-zero code.
_kuma_push() {
  local status="$1" msg="$2"
  [ -z "${KUMA_TOKEN_BACKUP:-}" ] && return 0
  curl -fsS --max-time 10 \
    "https://status.getklai.com/api/push/${KUMA_TOKEN_BACKUP}?status=${status}&msg=$(python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))" "${msg}")&ping=" \
    >/dev/null 2>&1 || true
}
# On any unhandled error: push failure before exiting
trap '_kuma_push down "Backup failed at $(date +%H:%M:%S)"' ERR

echo ""
echo "${LOG_PREFIX} ============================================"
echo "${LOG_PREFIX} Starting backup: ${BACKUP_DATE}"
echo "${LOG_PREFIX} ============================================"

mkdir -p "$BACKUP_DIR"
cd "$COMPOSE_DIR"

# ─── PostgreSQL ───────────────────────────────────────────────────────────────
echo ""
echo "${LOG_PREFIX} [1/13] PostgreSQL: dumping all databases..."
docker compose exec -T postgres pg_dumpall -U klai > "$BACKUP_DIR/postgres-all.sql"
echo "${LOG_PREFIX}       Size: $(du -sh "$BACKUP_DIR/postgres-all.sql" | cut -f1)"

# ─── Gitea ────────────────────────────────────────────────────────────────────
# PRIMARY DATA: git repos are the source of truth for all knowledge base content.
# DB is already in PostgreSQL (pg_dumpall above). We only need the repo files + config.
# Uses docker run with --volumes-from to read the volume as non-root alpine.
echo ""
echo "${LOG_PREFIX} [2/13] Gitea: backing up repositories and config..."
docker run --rm \
  --volumes-from klai-core-gitea-1:ro \
  -v "$BACKUP_DIR:/backup" \
  alpine tar -czf /backup/gitea-repos.tar.gz -C /data git/repositories gitea/conf
echo "${LOG_PREFIX}       Size: $(du -sh "$BACKUP_DIR/gitea-repos.tar.gz" | cut -f1)"

# ─── MongoDB ──────────────────────────────────────────────────────────────────
echo ""
echo "${LOG_PREFIX} [3/13] MongoDB: dumping all databases..."
docker compose exec -T mongodb mongodump \
  --username klai \
  --password "${MONGO_ROOT_PASSWORD}" \
  --authenticationDatabase admin \
  --archive \
  > "$BACKUP_DIR/mongodb-all.archive"
echo "${LOG_PREFIX}       Size: $(du -sh "$BACKUP_DIR/mongodb-all.archive" | cut -f1)"

# ─── Redis ────────────────────────────────────────────────────────────────────
echo ""
echo "${LOG_PREFIX} [4/13] Redis: triggering BGSAVE and copying dump..."
docker compose exec -T redis redis-cli -a "${REDIS_PASSWORD}" --no-auth-warning BGSAVE
sleep 3
docker compose cp redis:/data/dump.rdb "$BACKUP_DIR/redis-dump.rdb"
echo "${LOG_PREFIX}       Size: $(du -sh "$BACKUP_DIR/redis-dump.rdb" | cut -f1)"

# ─── Vexa Redis ───────────────────────────────────────────────────────────────
# Vexa's own Redis holds transcription pipeline state. Separate volume from
# the shared Redis; lose it = a transcription batch is re-queued from scratch.
echo ""
echo "${LOG_PREFIX} [5/13] Vexa Redis: BGSAVE + dump copy..."
if docker ps --format '{{.Names}}' | grep -q "^$(_ctr vexa-redis)$"; then
  docker exec "$(_ctr vexa-redis)" redis-cli BGSAVE >/dev/null
  sleep 3
  docker cp "$(_ctr vexa-redis):/data/dump.rdb" "$BACKUP_DIR/vexa-redis-dump.rdb"
  echo "${LOG_PREFIX}       Size: $(du -sh "$BACKUP_DIR/vexa-redis-dump.rdb" | cut -f1)"
else
  echo "${LOG_PREFIX}       Skipped (container not running)"
fi

# ─── Meilisearch ──────────────────────────────────────────────────────────────
# Snapshots are written to /meili_data/snapshots/ inside the meilisearch-data volume.
# Meilisearch indexes rebuild from MongoDB automatically — snapshot is a nice-to-have.
echo ""
echo "${LOG_PREFIX} [6/13] Meilisearch: creating snapshot..."
SNAPSHOT_RESPONSE=$(docker compose exec -T meilisearch \
  wget -qO- --post-data="" \
  --header="Authorization: Bearer ${MEILI_MASTER_KEY}" \
  http://127.0.0.1:7700/snapshots 2>/dev/null || echo '{"error":"snapshot failed"}')
echo "${LOG_PREFIX}       Response: $SNAPSHOT_RESPONSE"

# ─── Qdrant ───────────────────────────────────────────────────────────────────
# Zero-downtime snapshot via API. One snapshot file per collection, written
# inside the qdrant-data volume (/qdrant/storage/snapshots/<collection>/).
#
# The qdrant image itself is distroless — no wget/curl/python inside — so we
# use an alpine sidecar on the klai-net network to speak the HTTP API.
echo ""
echo "${LOG_PREFIX} [7/13] Qdrant: snapshotting collections via API..."
QDRANT_CONTAINER="$(_ctr qdrant)"
_qdrant_api() {
  # Usage: _qdrant_api <method> <path>
  # Emits raw JSON on stdout, "" on failure.
  local method="$1" path="$2"
  local flags=()
  case "${method}" in
    GET)    : ;;
    POST)   flags=(-X POST -d "") ;;
    DELETE) flags=(-X DELETE) ;;
    *)      echo "unknown method: ${method}" >&2; return 1 ;;
  esac
  docker run --rm --network klai-net curlimages/curl:8.11.1 \
    -sSf --max-time 30 \
    "${flags[@]}" \
    -H "api-key: ${QDRANT_API_KEY}" \
    "http://${QDRANT_CONTAINER}:6333${path}" 2>/dev/null || echo ""
}
if docker ps --format '{{.Names}}' | grep -q "^${QDRANT_CONTAINER}$"; then
  COLLECTIONS_JSON=$(_qdrant_api GET /collections)
  COLLECTIONS=$(echo "${COLLECTIONS_JSON}" | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    print('\n'.join(c['name'] for c in data.get('result', {}).get('collections', [])))
except Exception:
    pass
" 2>/dev/null || echo "")
  if [ -z "${COLLECTIONS}" ]; then
    echo "${LOG_PREFIX}       No collections found (or API unreachable)" >&2
  else
    for col in ${COLLECTIONS}; do
      # 1. Trigger snapshot creation. Qdrant writes it to an internal location
      #    and returns the snapshot's name.
      RESP=$(_qdrant_api POST "/collections/${col}/snapshots")
      SNAP_NAME=$(echo "${RESP}" | python3 -c "
import json, sys
try:
    print(json.load(sys.stdin).get('result', {}).get('name', ''))
except Exception:
    pass
" 2>/dev/null || echo "")
      if [ -z "${SNAP_NAME}" ]; then
        echo "${LOG_PREFIX}       ${col} — snapshot API returned no name: ${RESP}" >&2
        continue
      fi
      # 2. Download the snapshot via the same URL path (Qdrant v1 serves it on
      #    GET). Writing to BACKUP_DIR via a mounted volume so the bytes land
      #    on the host, not inside the sidecar container.
      # --user 0:0: curl image's default 1001 can't write to the host-owned
      # backup dir. We're already running as root in cron context.
      if docker run --rm --network klai-net \
           --user 0:0 \
           -v "$BACKUP_DIR:/out" \
           curlimages/curl:8.11.1 \
           -sSfL --max-time 300 \
           -H "api-key: ${QDRANT_API_KEY}" \
           "http://${QDRANT_CONTAINER}:6333/collections/${col}/snapshots/${SNAP_NAME}" \
           -o "/out/qdrant-${col}.snapshot" 2>/dev/null
      then
        echo "${LOG_PREFIX}       ${col} → $(du -sh "$BACKUP_DIR/qdrant-${col}.snapshot" | cut -f1)"
      else
        echo "${LOG_PREFIX}       ${col} — snapshot download failed" >&2
        rm -f "$BACKUP_DIR/qdrant-${col}.snapshot"
      fi
      # 3. Clean up the snapshot on Qdrant's side (don't let them pile up).
      _qdrant_api DELETE "/collections/${col}/snapshots/${SNAP_NAME}" >/dev/null || true
    done
  fi
else
  echo "${LOG_PREFIX}       Skipped (container not running)"
fi

# ─── FalkorDB ─────────────────────────────────────────────────────────────────
# Redis-protocol BGSAVE writes dump.rdb to /var/lib/falkordb/data/dump.rdb
# (post SPEC-INFRA-005 the host bind mount is correct). No auth on FalkorDB
# — it's on klai-net only, no external exposure.
echo ""
echo "${LOG_PREFIX} [8/13] FalkorDB: BGSAVE + dump copy (knowledge graph)..."
FALKORDB_CONTAINER="$(_ctr falkordb)"
if docker ps --format '{{.Names}}' | grep -q "^${FALKORDB_CONTAINER}$"; then
  docker exec "${FALKORDB_CONTAINER}" redis-cli BGSAVE >/dev/null
  sleep 3
  docker cp "${FALKORDB_CONTAINER}:/var/lib/falkordb/data/dump.rdb" \
    "$BACKUP_DIR/falkordb-dump.rdb"
  echo "${LOG_PREFIX}       Size: $(du -sh "$BACKUP_DIR/falkordb-dump.rdb" | cut -f1)"
else
  echo "${LOG_PREFIX}       Skipped (container not running)"
fi

# ─── Garage metadata ──────────────────────────────────────────────────────────
# `garage meta snapshot` writes a consistent LMDB snapshot under
# /var/lib/garage/meta/snapshots/ while the service keeps running (Garage
# v0.9.4+). We tar the snapshot dir itself rather than the live LMDB file.
echo ""
echo "${LOG_PREFIX} [9/13] Garage: metadata snapshot..."
GARAGE_CONTAINER="$(_ctr garage)"
if docker ps --format '{{.Names}}' | grep -q "^${GARAGE_CONTAINER}$"; then
  SNAP_OUT=$(docker exec "${GARAGE_CONTAINER}" /garage meta snapshot 2>&1 || echo 'snapshot-failed')
  echo "${LOG_PREFIX}       ${SNAP_OUT}"
  # Copy the most recent snapshot subdirectory out via volumes-from.
  docker run --rm \
    --volumes-from "${GARAGE_CONTAINER}:ro" \
    -v "$BACKUP_DIR:/backup" \
    alpine sh -c '
      latest=$(ls -1td /var/lib/garage/meta/snapshots/*/ 2>/dev/null | head -1)
      if [ -n "${latest}" ]; then
        tar -czf /backup/garage-meta.tar.gz -C "${latest}" . && echo "tar OK"
      else
        echo "no snapshot dir found" >&2; exit 1
      fi
    '
  echo "${LOG_PREFIX}       Size: $(du -sh "$BACKUP_DIR/garage-meta.tar.gz" 2>/dev/null | cut -f1 || echo '—')"
else
  echo "${LOG_PREFIX}       Skipped (container not running)"
fi

# ─── Garage data (blobs) ──────────────────────────────────────────────────────
# Blobs are immutable content-addressed files — rsync while live is safe.
# Bind mount at /opt/klai/garage-data, tar via sibling container so we don't
# shell-out to host tar (which would fight file ownership — garage runs root).
echo ""
echo "${LOG_PREFIX} [10/13] Garage: data (blob) tar..."
if [ -d /opt/klai/garage-data ]; then
  docker run --rm \
    -v /opt/klai/garage-data:/data:ro \
    -v "$BACKUP_DIR:/backup" \
    alpine tar -czf /backup/garage-data.tar.gz -C /data .
  echo "${LOG_PREFIX}       Size: $(du -sh "$BACKUP_DIR/garage-data.tar.gz" | cut -f1)"
else
  echo "${LOG_PREFIX}       Skipped (no /opt/klai/garage-data)"
fi

# ─── Firecrawl PostgreSQL ─────────────────────────────────────────────────────
# Firecrawl's Postgres uses POSTGRES_USER=firecrawl as the superuser.
echo ""
echo "${LOG_PREFIX} [11/13] Firecrawl Postgres: pg_dumpall..."
if docker ps --format '{{.Names}}' | grep -q "^$(_ctr firecrawl-postgres)$"; then
  if docker exec "$(_ctr firecrawl-postgres)" pg_dumpall -U firecrawl \
       > "$BACKUP_DIR/firecrawl-postgres-all.sql" 2>/dev/null; then
    echo "${LOG_PREFIX}       Size: $(du -sh "$BACKUP_DIR/firecrawl-postgres-all.sql" | cut -f1)"
  else
    rm -f "$BACKUP_DIR/firecrawl-postgres-all.sql"
    echo "${LOG_PREFIX}       pg_dumpall failed — skipping"
  fi
else
  echo "${LOG_PREFIX}       Skipped (container not running)"
fi

# ─── Scribe audio (Vexa transcription recordings) ─────────────────────────────
# Retention is event-driven (deleted on successful transcription). At rest
# this volume holds only `failed` recordings awaiting retry. Small tar.
echo ""
echo "${LOG_PREFIX} [12/13] Scribe audio: tar of failed-retry recordings..."
if docker ps --format '{{.Names}}' | grep -q "^$(_ctr scribe-api)$"; then
  docker run --rm \
    --volumes-from "$(_ctr scribe-api):ro" \
    -v "$BACKUP_DIR:/backup" \
    alpine tar -czf /backup/scribe-audio.tar.gz -C /data/audio . 2>/dev/null \
    || true
  if [ -s "$BACKUP_DIR/scribe-audio.tar.gz" ]; then
    echo "${LOG_PREFIX}       Size: $(du -sh "$BACKUP_DIR/scribe-audio.tar.gz" | cut -f1)"
  else
    rm -f "$BACKUP_DIR/scribe-audio.tar.gz"
    echo "${LOG_PREFIX}       Empty (no retained recordings — good)"
  fi
else
  echo "${LOG_PREFIX}       Skipped (container not running)"
fi

# ─── Research uploads (user-uploaded notebook sources) ────────────────────────
# Bind-mount on host — rsync directly, no docker involved.
echo ""
echo "${LOG_PREFIX} [13/13] Research uploads: rsync of /opt/klai/research-uploads..."
if [ -d /opt/klai/research-uploads ]; then
  rsync -a --delete /opt/klai/research-uploads/ "$BACKUP_DIR/research-uploads/"
  echo "${LOG_PREFIX}       Size: $(du -sh "$BACKUP_DIR/research-uploads" | cut -f1)"
else
  echo "${LOG_PREFIX}       Skipped (no /opt/klai/research-uploads)"
fi

# ─── Encrypt for remote storage (age) ───────────────────────────────────────
# Plaintext stays local (7-day retention). Only encrypted files go to the Storage Box.
# Both keys can decrypt: MacBook + core-01 (same keys as .sops.yaml)
AGE_RECIPIENTS=(
  "age1lyd243tsj8j7rn2wy4hdmnya99wsf2p87fpphys9k65kammerqsqnzpsur"
  "age15ztzw9vnngkdnw0pg5tn8upplglvhzkep23sm5zu86res5lcmv7syw5m4v"
)
ENCRYPT_DIR=$(mktemp -d)
# Encrypt every archive artifact produced above. Loop over all matching files
# so we don't silently skip a new backup kind when the script is extended.
shopt -s nullglob
for f in \
  "$BACKUP_DIR"/postgres-all.sql \
  "$BACKUP_DIR"/gitea-repos.tar.gz \
  "$BACKUP_DIR"/mongodb-all.archive \
  "$BACKUP_DIR"/redis-dump.rdb \
  "$BACKUP_DIR"/vexa-redis-dump.rdb \
  "$BACKUP_DIR"/qdrant-*.snapshot \
  "$BACKUP_DIR"/falkordb-dump.rdb \
  "$BACKUP_DIR"/garage-meta.tar.gz \
  "$BACKUP_DIR"/garage-data.tar.gz \
  "$BACKUP_DIR"/firecrawl-postgres-all.sql \
  "$BACKUP_DIR"/scribe-audio.tar.gz; do
  [ -f "$f" ] || continue
  age -r "${AGE_RECIPIENTS[0]}" -r "${AGE_RECIPIENTS[1]}" "$f" > "$ENCRYPT_DIR/$(basename "$f").age"
done
# research-uploads is a directory — tar first, then encrypt.
if [ -d "$BACKUP_DIR/research-uploads" ]; then
  tar -czf - -C "$BACKUP_DIR" research-uploads \
    | age -r "${AGE_RECIPIENTS[0]}" -r "${AGE_RECIPIENTS[1]}" \
    > "$ENCRYPT_DIR/research-uploads.tar.gz.age"
fi
shopt -u nullglob

# ─── Upload to Hetzner Storage Box ───────────────────────────────────────────
echo ""
if [ -n "${STORAGEBOX_HOST:-}" ] && [ -n "${STORAGEBOX_USER:-}" ]; then
  echo "${LOG_PREFIX} [14/14] Uploading encrypted backups to Hetzner Storage Box..."
  REMOTE_PATH="backups/core-01/${BACKUP_DATE}"

  rsync \
    -e "ssh -p 23 -o StrictHostKeyChecking=accept-new -o ConnectTimeout=30" \
    -az --mkpath --stats \
    "$ENCRYPT_DIR/" \
    "${STORAGEBOX_USER}@${STORAGEBOX_HOST}:${REMOTE_PATH}/"

  rm -rf "$ENCRYPT_DIR"
  echo "${LOG_PREFIX}       Uploaded to: ${STORAGEBOX_USER}@${STORAGEBOX_HOST}:${REMOTE_PATH}"

  # Remote retention: Storage Box uses a restricted shell (no find/rm via SSH).
  # At ~45MB/day and 100GB, pruning is not needed until ~2026 days of backups accumulate.
  # TODO: implement sftp-based pruning if/when storage grows beyond 80GB.

  echo "${LOG_PREFIX}       Storage Box upload complete."
else
  echo "${LOG_PREFIX} [14/14] Storage Box niet geconfigureerd (STORAGEBOX_HOST/STORAGEBOX_USER niet gezet) -- upload overgeslagen."
  echo "${LOG_PREFIX}       Zie klai-infra/SERVERS.md voor setup-instructies."
fi

# ─── Local retention: keep last 30 days ──────────────────────────────────────
echo ""
echo "${LOG_PREFIX} Lokale cleanup: backups ouder dan 30 dagen verwijderen..."
find /opt/klai/backups/ -maxdepth 1 -type d -name '20*' | sort | head -n -30 | xargs -r rm -rf
REMAINING=$(find /opt/klai/backups/ -maxdepth 1 -type d -name '20*' | wc -l)
echo "${LOG_PREFIX} Lokale backups bewaard: ${REMAINING}"

# ─── Uptime Kuma: report success ──────────────────────────────────────────────
_kuma_push up "OK — $(du -sh "$BACKUP_DIR" | cut -f1)"

# ─── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "${LOG_PREFIX} ============================================"
echo "${LOG_PREFIX} Backup voltooid: $BACKUP_DIR"
echo "${LOG_PREFIX} ============================================"
ls -lh "$BACKUP_DIR"
