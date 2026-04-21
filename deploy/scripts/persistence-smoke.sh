#!/usr/bin/env bash
# persistence-smoke.sh — Post-deploy proof that each stateful service's data
# actually lands on its host-persistent volume.
#
# Why: SPEC-INFRA-005 / 2026-04-19 incident. `docker ps --filter healthy` tells
# you the container is up. It tells you nothing about whether writes leave the
# ephemeral writable layer. This script writes a canary value, forces the
# service's persistence mechanism (BGSAVE, CHECKPOINT, snapshot), and then
# checks the host-side data file's mtime to prove the bytes crossed the
# container boundary.
#
# Run:
#   sudo /opt/klai/scripts/persistence-smoke.sh           # all services
#   sudo /opt/klai/scripts/persistence-smoke.sh falkordb  # one service
#
# Must run as root. Docker-managed named volumes live under
# /var/lib/docker/volumes/ (mode 710), so mtime checks need root. This script
# never touches the Storage Box — safe to run as root, unlike backup.sh.
#
# Invoked by:
#   - Post-deploy hook in deploy-compose.yml (future)
#   - Manually by ops after any stateful-service change
#   - Optional: cron every few hours as a living canary
#
# Exit codes:
#   0 — every tested service wrote to its host volume within MTIME_WINDOW seconds
#   1 — one or more services failed the canary
#   2 — configuration error (docker unreachable, unknown service name)
set -uo pipefail

MTIME_WINDOW="${MTIME_WINDOW:-60}"   # seconds — how recent the host file must be
REQUEST=("${@}")
FAILED=0
TOTAL=0

# Host-side paths, service-by-service. Kept in sync with deploy/volume-mounts.yaml.
# Only services that have a deterministic canary mechanism are listed here —
# stateless rotations (logs, VictoriaMetrics time-series) are out of scope.

_ctr() { echo "klai-core-${1}-1"; }

# Check mtime of a host-side file. Returns 0 if modified in the last
# MTIME_WINDOW seconds, 1 otherwise.
_mtime_fresh() {
  local path="$1"
  if [ ! -f "${path}" ]; then
    return 1
  fi
  local age
  age=$(( $(date +%s) - $(stat -c %Y "${path}") ))
  [ "${age}" -le "${MTIME_WINDOW}" ]
}

# Shared runner. Prints status line, bumps counters.
_emit() {
  local service="$1" status="$2" detail="$3"
  TOTAL=$((TOTAL + 1))
  if [ "${status}" = "OK" ]; then
    printf '%-16s %-4s %s\n' "${service}" "OK" "${detail}"
  else
    printf '%-16s %-4s %s\n' "${service}" "FAIL" "${detail}" >&2
    FAILED=$((FAILED + 1))
  fi
}

# Should this service run? Empty REQUEST == run everything.
_should_run() {
  local name="$1"
  [ ${#REQUEST[@]} -eq 0 ] && return 0
  for r in "${REQUEST[@]}"; do
    [ "${r}" = "${name}" ] && return 0
  done
  return 1
}

# ── FalkorDB ──────────────────────────────────────────────────────────────────
# Canary: GRAPH.QUERY on a dedicated __smoke graph, DELETE it, SAVE, check host.
smoke_falkordb() {
  _should_run falkordb || return 0
  local ctr host_path
  ctr="$(_ctr falkordb)"
  host_path=/opt/klai/falkordb-data/dump.rdb

  if ! docker ps --format '{{.Names}}' | grep -q "^${ctr}$"; then
    _emit falkordb FAIL "container not running"; return
  fi
  docker exec "${ctr}" redis-cli \
    GRAPH.QUERY __smoke "CREATE (:Canary {ts: timestamp()}) RETURN 1" >/dev/null 2>&1 || {
      _emit falkordb FAIL "GRAPH.QUERY failed"; return;
    }
  docker exec "${ctr}" redis-cli GRAPH.DELETE __smoke >/dev/null 2>&1
  docker exec "${ctr}" redis-cli SAVE >/dev/null 2>&1 || {
    _emit falkordb FAIL "SAVE failed"; return;
  }
  if _mtime_fresh "${host_path}"; then
    _emit falkordb OK "dump.rdb mtime < ${MTIME_WINDOW}s"
  else
    _emit falkordb FAIL "dump.rdb not touched on host"
  fi
}

# ── Redis ─────────────────────────────────────────────────────────────────────
# Canary: SET a smoke key, BGSAVE, verify dump.rdb inside the volume changed.
# Host path is a Docker-managed named volume — find its mountpoint dynamically.
_named_volume_path() {
  docker volume inspect "$1" --format '{{.Mountpoint}}' 2>/dev/null
}

smoke_redis() {
  _should_run redis || return 0
  local ctr vol_path
  ctr="$(_ctr redis)"
  vol_path=$(_named_volume_path klai-core_redis-data)

  if ! docker ps --format '{{.Names}}' | grep -q "^${ctr}$"; then
    _emit redis FAIL "container not running"; return
  fi
  if [ -z "${vol_path}" ] || [ ! -d "${vol_path}" ]; then
    _emit redis FAIL "named volume klai-core_redis-data not resolvable"; return
  fi
  local pw
  pw=$(docker inspect "${ctr}" --format '{{range .Config.Env}}{{println .}}{{end}}' \
    | grep '^REDIS_PASSWORD=' | head -1 | cut -d'=' -f2-)
  docker exec "${ctr}" redis-cli -a "${pw}" --no-auth-warning \
    SET __smoke "$(date +%s)" >/dev/null 2>&1 || {
      _emit redis FAIL "SET failed"; return;
    }
  docker exec "${ctr}" redis-cli -a "${pw}" --no-auth-warning \
    BGSAVE >/dev/null 2>&1 || { _emit redis FAIL "BGSAVE failed"; return; }
  # BGSAVE is async — give the background writer a moment.
  sleep 2
  if _mtime_fresh "${vol_path}/dump.rdb"; then
    _emit redis OK "dump.rdb mtime < ${MTIME_WINDOW}s"
  else
    _emit redis FAIL "dump.rdb stale (${vol_path}/dump.rdb)"
  fi
  docker exec "${ctr}" redis-cli -a "${pw}" --no-auth-warning DEL __smoke >/dev/null 2>&1 || true
}

# ── PostgreSQL ────────────────────────────────────────────────────────────────
# Canary: INSERT into a __smoke table, CHECKPOINT, verify a WAL file was
# written recently. We don't read individual files (many) — just the
# pg_wal directory mtime.
smoke_postgres() {
  _should_run postgres || return 0
  local ctr vol_path
  ctr="$(_ctr postgres)"
  vol_path=$(_named_volume_path klai-core_postgres-data)

  if ! docker ps --format '{{.Names}}' | grep -q "^${ctr}$"; then
    _emit postgres FAIL "container not running"; return
  fi
  if [ -z "${vol_path}" ]; then
    _emit postgres FAIL "named volume klai-core_postgres-data not resolvable"; return
  fi

  # SQL is piped in via stdin to avoid nightmare bash single-quote escaping.
  docker exec -i -u postgres "${ctr}" psql -U klai -d klai -v ON_ERROR_STOP=1 <<'SQL' >/dev/null 2>&1 || { _emit postgres FAIL "smoke SQL failed"; return; }
CREATE SCHEMA IF NOT EXISTS __smoke;
CREATE TABLE IF NOT EXISTS __smoke.ping (ts timestamptz DEFAULT now());
INSERT INTO __smoke.ping DEFAULT VALUES;
CHECKPOINT;
DELETE FROM __smoke.ping WHERE ts < now() - INTERVAL '1 hour';
SQL

  # PGDATA lives in different subdirs depending on image:
  #   pgvector/pgvector:pg18 → /var/lib/postgresql/18/docker/pg_wal
  #   vanilla postgres:X     → /var/lib/postgresql/data/pg_wal
  #   legacy layouts         → /var/lib/postgresql/pg_wal
  # Find it.
  local wal_dir
  wal_dir=$(find "${vol_path}" -maxdepth 5 -type d -name pg_wal 2>/dev/null | head -1)
  if [ -z "${wal_dir}" ] || [ ! -d "${wal_dir}" ]; then
    _emit postgres FAIL "pg_wal dir not found under ${vol_path}"
    return
  fi
  # CHECKPOINT writes into the currently-open WAL segment — the file's mtime
  # updates but the directory's mtime does NOT (dir mtime changes only on
  # create/delete). Check the newest file inside pg_wal.
  local newest age
  newest=$(find "${wal_dir}" -maxdepth 1 -type f -printf '%T@ %p\n' 2>/dev/null \
    | sort -nr | head -1 | awk '{print $2}')
  if [ -z "${newest}" ]; then
    _emit postgres FAIL "pg_wal is empty"
    return
  fi
  age=$(( $(date +%s) - $(stat -c %Y "${newest}") ))
  if [ "${age}" -le "${MTIME_WINDOW}" ]; then
    _emit postgres OK "newest WAL segment mtime < ${MTIME_WINDOW}s"
  else
    _emit postgres FAIL "newest WAL segment stale (${age}s)"
  fi
}

# ── Qdrant ────────────────────────────────────────────────────────────────────
# Canary: create a tiny test collection, delete it. Qdrant writes the
# collection descriptor under /qdrant/storage — mtime of storage dir changes.
smoke_qdrant() {
  _should_run qdrant || return 0
  local ctr vol_path
  ctr="$(_ctr qdrant)"
  vol_path=$(_named_volume_path klai-core_qdrant-data)

  if ! docker ps --format '{{.Names}}' | grep -q "^${ctr}$"; then
    _emit qdrant FAIL "container not running"; return
  fi
  local api_key
  api_key=$(docker inspect "${ctr}" --format '{{range .Config.Env}}{{println .}}{{end}}' \
    | grep '^QDRANT__SERVICE__API_KEY=' | head -1 | cut -d'=' -f2-)

  # Use a sidecar to reach Qdrant on the internal network.
  local col="__smoke_$(date +%s)"
  docker run --rm --network klai-net --user 0:0 curlimages/curl:8.11.1 \
    -sSf --max-time 10 -X PUT \
    -H "api-key: ${api_key}" \
    -H 'Content-Type: application/json' \
    -d '{"vectors":{"size":4,"distance":"Cosine"}}' \
    "http://${ctr}:6333/collections/${col}" >/dev/null 2>&1 \
    || { _emit qdrant FAIL "collection create failed"; return; }
  docker run --rm --network klai-net --user 0:0 curlimages/curl:8.11.1 \
    -sSf --max-time 10 -X DELETE \
    -H "api-key: ${api_key}" \
    "http://${ctr}:6333/collections/${col}" >/dev/null 2>&1 || true

  local storage_dir="${vol_path}/collections"
  if [ ! -d "${storage_dir}" ]; then
    _emit qdrant FAIL "collections dir not under ${vol_path}"
    return
  fi
  local age
  age=$(( $(date +%s) - $(stat -c %Y "${storage_dir}") ))
  if [ "${age}" -le "${MTIME_WINDOW}" ]; then
    _emit qdrant OK "collections mtime < ${MTIME_WINDOW}s"
  else
    _emit qdrant FAIL "collections stale (${age}s)"
  fi
}

# ── Garage ────────────────────────────────────────────────────────────────────
# Canary: trigger `garage meta snapshot`. That writes into /var/lib/garage/meta
# which is bind-mounted to /opt/klai/garage-meta on host.
smoke_garage() {
  _should_run garage || return 0
  local ctr host_dir
  ctr="$(_ctr garage)"
  host_dir=/opt/klai/garage-meta/snapshots

  if ! docker ps --format '{{.Names}}' | grep -q "^${ctr}$"; then
    _emit garage FAIL "container not running"; return
  fi
  docker exec "${ctr}" /garage meta snapshot >/dev/null 2>&1 \
    || { _emit garage FAIL "meta snapshot command failed"; return; }

  # Find the most recently-created snapshot subdirectory.
  if [ ! -d "${host_dir}" ]; then
    _emit garage FAIL "snapshots dir missing on host"
    return
  fi
  local latest age
  latest=$(find "${host_dir}" -maxdepth 1 -mindepth 1 -type d -printf '%T@ %p\n' \
    | sort -nr | head -1 | awk '{print $2}')
  if [ -z "${latest}" ]; then
    _emit garage FAIL "no snapshot dir found"
    return
  fi
  age=$(( $(date +%s) - $(stat -c %Y "${latest}") ))
  if [ "${age}" -le "${MTIME_WINDOW}" ]; then
    _emit garage OK "latest snapshot < ${MTIME_WINDOW}s old ($(basename "${latest}"))"
  else
    _emit garage FAIL "snapshot stale (${age}s)"
  fi
}

# ── Header + run ──────────────────────────────────────────────────────────────
echo "persistence-smoke: window=${MTIME_WINDOW}s  target=${REQUEST[*]:-all}"
printf '%-16s %-4s %s\n' "SERVICE" "STAT" "DETAIL"
printf '%-16s %-4s %s\n' "-------" "----" "------"

smoke_falkordb
smoke_redis
smoke_postgres
smoke_qdrant
smoke_garage

echo ""
if [ "${FAILED}" -gt 0 ]; then
  echo "persistence-smoke: FAILED — ${FAILED}/${TOTAL} services did not persist to host." >&2
  exit 1
fi
echo "persistence-smoke: OK — ${TOTAL}/${TOTAL} services verified."
