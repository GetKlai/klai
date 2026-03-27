#!/usr/bin/env bash
# backup.sh — Daily backup for all stateful services on core-01
#
# What gets backed up:
#   1. PostgreSQL  — pg_dumpall → postgres-all.sql (includes Gitea DB)
#   2. Gitea       — git repositories + config → gitea-repos.tar.gz (PRIMARY: KB source of truth)
#   3. MongoDB     — mongodump archive
#   4. Redis       — BGSAVE + dump.rdb copy
#   5. Meilisearch — snapshot trigger (written to volume)
#   6. Upload      — rsync to Hetzner Storage Box (if configured)
#
# Usage:
#   ./scripts/backup.sh                 # manual run
#   Cron: 0 2 * * * /opt/klai/scripts/backup.sh >> /opt/klai/logs/backup.log 2>&1
#
# Required env vars (loaded from /opt/klai/.env):
#   MONGO_ROOT_PASSWORD   — MongoDB root password
#   REDIS_PASSWORD        — Redis password
#   MEILI_MASTER_KEY      — Meilisearch master key
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
# Storage Box config from .env
_env_get() { grep "^${1}=" "$COMPOSE_DIR/.env" | head -1 | cut -d'=' -f2-; }
STORAGEBOX_HOST=${STORAGEBOX_HOST:-$(_env_get STORAGEBOX_HOST)}
STORAGEBOX_USER=${STORAGEBOX_USER:-$(_env_get STORAGEBOX_USER)}

echo ""
echo "${LOG_PREFIX} ============================================"
echo "${LOG_PREFIX} Starting backup: ${BACKUP_DATE}"
echo "${LOG_PREFIX} ============================================"

mkdir -p "$BACKUP_DIR"
cd "$COMPOSE_DIR"

# ─── PostgreSQL ───────────────────────────────────────────────────────────────
echo ""
echo "${LOG_PREFIX} [1/6] PostgreSQL: dumping all databases..."
docker compose exec -T postgres pg_dumpall -U klai > "$BACKUP_DIR/postgres-all.sql"
echo "${LOG_PREFIX}       Size: $(du -sh "$BACKUP_DIR/postgres-all.sql" | cut -f1)"

# ─── Gitea ────────────────────────────────────────────────────────────────────
# PRIMARY DATA: git repos are the source of truth for all knowledge base content.
# DB is already in PostgreSQL (pg_dumpall above). We only need the repo files + config.
# Uses docker run with --volumes-from to read the volume as non-root alpine.
echo ""
echo "${LOG_PREFIX} [2/6] Gitea: backing up repositories and config..."
docker run --rm \
  --volumes-from klai-core-gitea-1:ro \
  -v "$BACKUP_DIR:/backup" \
  alpine tar -czf /backup/gitea-repos.tar.gz -C /data git/repositories gitea/conf
echo "${LOG_PREFIX}       Size: $(du -sh "$BACKUP_DIR/gitea-repos.tar.gz" | cut -f1)"

# ─── MongoDB ──────────────────────────────────────────────────────────────────
echo ""
echo "${LOG_PREFIX} [3/6] MongoDB: dumping all databases..."
docker compose exec -T mongodb mongodump \
  --username klai \
  --password "${MONGO_ROOT_PASSWORD}" \
  --authenticationDatabase admin \
  --archive \
  > "$BACKUP_DIR/mongodb-all.archive"
echo "${LOG_PREFIX}       Size: $(du -sh "$BACKUP_DIR/mongodb-all.archive" | cut -f1)"

# ─── Redis ────────────────────────────────────────────────────────────────────
echo ""
echo "${LOG_PREFIX} [4/6] Redis: triggering BGSAVE and copying dump..."
docker compose exec -T redis redis-cli -a "${REDIS_PASSWORD}" --no-auth-warning BGSAVE
sleep 3
docker compose cp redis:/data/dump.rdb "$BACKUP_DIR/redis-dump.rdb"
echo "${LOG_PREFIX}       Size: $(du -sh "$BACKUP_DIR/redis-dump.rdb" | cut -f1)"

# ─── Meilisearch ──────────────────────────────────────────────────────────────
# Snapshots are written to /meili_data/snapshots/ inside the meilisearch-data volume.
# Meilisearch indexes rebuild from MongoDB automatically — snapshot is a nice-to-have.
echo ""
echo "${LOG_PREFIX} [5/6] Meilisearch: creating snapshot..."
SNAPSHOT_RESPONSE=$(docker compose exec -T meilisearch \
  wget -qO- --post-data="" \
  --header="Authorization: Bearer ${MEILI_MASTER_KEY}" \
  http://127.0.0.1:7700/snapshots 2>/dev/null || echo '{"error":"snapshot failed"}')
echo "${LOG_PREFIX}       Response: $SNAPSHOT_RESPONSE"

# ─── Encrypt for remote storage (age) ───────────────────────────────────────
# Plaintext stays local (7-day retention). Only encrypted files go to the Storage Box.
# Both keys can decrypt: MacBook + core-01 (same keys as .sops.yaml)
AGE_RECIPIENTS=(
  "age1lyd243tsj8j7rn2wy4hdmnya99wsf2p87fpphys9k65kammerqsqnzpsur"
  "age15ztzw9vnngkdnw0pg5tn8upplglvhzkep23sm5zu86res5lcmv7syw5m4v"
)
ENCRYPT_DIR=$(mktemp -d)
for f in "$BACKUP_DIR"/postgres-all.sql "$BACKUP_DIR"/gitea-repos.tar.gz "$BACKUP_DIR"/mongodb-all.archive "$BACKUP_DIR"/redis-dump.rdb; do
  [ -f "$f" ] || continue
  age -r "${AGE_RECIPIENTS[0]}" -r "${AGE_RECIPIENTS[1]}" "$f" > "$ENCRYPT_DIR/$(basename "$f").age"
done

# ─── Upload to Hetzner Storage Box ───────────────────────────────────────────
echo ""
if [ -n "${STORAGEBOX_HOST:-}" ] && [ -n "${STORAGEBOX_USER:-}" ]; then
  echo "${LOG_PREFIX} [6/6] Uploading encrypted backups to Hetzner Storage Box..."
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
  echo "${LOG_PREFIX} [6/6] Storage Box niet geconfigureerd (STORAGEBOX_HOST/STORAGEBOX_USER niet gezet) -- upload overgeslagen."
  echo "${LOG_PREFIX}       Zie klai-infra/SERVERS.md voor setup-instructies."
fi

# ─── Local retention: keep last 7 days ───────────────────────────────────────
echo ""
echo "${LOG_PREFIX} Lokale cleanup: backups ouder dan 7 dagen verwijderen..."
find /opt/klai/backups/ -maxdepth 1 -type d -name '20*' | sort | head -n -7 | xargs -r rm -rf
REMAINING=$(find /opt/klai/backups/ -maxdepth 1 -type d -name '20*' | wc -l)
echo "${LOG_PREFIX} Lokale backups bewaard: ${REMAINING}"

# ─── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "${LOG_PREFIX} ============================================"
echo "${LOG_PREFIX} Backup voltooid: $BACKUP_DIR"
echo "${LOG_PREFIX} ============================================"
ls -lh "$BACKUP_DIR"
