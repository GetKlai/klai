#!/usr/bin/env bash
# backup.sh — Pre-upgrade backup for all stateful services on core-01
# Usage: ./scripts/backup.sh
# Output: /opt/klai/backups/YYYY-MM-DD/
set -euo pipefail

BACKUP_DIR="/opt/klai/backups/$(date +%Y-%m-%d)"
COMPOSE_DIR="/opt/klai"

# Load secrets from .env
# shellcheck source=/dev/null
set -a; source "$COMPOSE_DIR/.env"; set +a

echo "==> Creating backup directory: $BACKUP_DIR"
mkdir -p "$BACKUP_DIR"

cd "$COMPOSE_DIR"

# ─── PostgreSQL ───────────────────────────────────────────────────────────────
echo ""
echo "==> [1/4] PostgreSQL: dumping all databases..."
docker compose exec -T postgres pg_dumpall -U klai > "$BACKUP_DIR/postgres-all.sql"
echo "    Size: $(du -sh "$BACKUP_DIR/postgres-all.sql" | cut -f1)"

# ─── MongoDB ──────────────────────────────────────────────────────────────────
echo ""
echo "==> [2/4] MongoDB: dumping all databases..."
docker compose exec -T mongodb mongodump \
  --username klai \
  --password "${MONGO_ROOT_PASSWORD}" \
  --authenticationDatabase admin \
  --archive \
  > "$BACKUP_DIR/mongodb-all.archive"
echo "    Size: $(du -sh "$BACKUP_DIR/mongodb-all.archive" | cut -f1)"

# ─── Redis ────────────────────────────────────────────────────────────────────
echo ""
echo "==> [3/4] Redis: triggering BGSAVE and copying dump..."
docker compose exec -T redis redis-cli -a "${REDIS_PASSWORD}" --no-auth-warning BGSAVE
sleep 3
docker compose cp redis:/data/dump.rdb "$BACKUP_DIR/redis-dump.rdb"
echo "    Size: $(du -sh "$BACKUP_DIR/redis-dump.rdb" | cut -f1)"

# ─── Meilisearch ──────────────────────────────────────────────────────────────
# Note: Meilisearch runs on an internal Docker network — access via docker exec, not host curl.
# Snapshots are written to /meili_data/snapshots/ inside the meilisearch-data volume.
echo ""
echo "==> [4/4] Meilisearch: creating snapshot..."
SNAPSHOT_RESPONSE=$(docker compose exec -T meilisearch \
  wget -qO- --post-data="" \
  --header="Authorization: Bearer ${MEILI_MASTER_KEY}" \
  http://127.0.0.1:7700/snapshots)
echo "    Response: $SNAPSHOT_RESPONSE"
echo "    Note: snapshot written to meilisearch-data volume at /meili_data/snapshots/"

# ─── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo "==> Backup complete: $BACKUP_DIR"
ls -lh "$BACKUP_DIR"
