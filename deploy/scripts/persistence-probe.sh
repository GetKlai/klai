#!/usr/bin/env bash
# persistence-probe.sh — export host-side data-file age as Prometheus metrics.
#
# Writes a .prom file to /var/lib/node_exporter/textfile_collector/ every time
# it's invoked. Alloy's node_exporter (prometheus.exporter.unix with textfile
# block) picks the file up and ships the metrics to VictoriaMetrics.
#
# Metric emitted (gauge):
#   klai_persistence_file_age_seconds{service="<name>",path="<abs-host-path>"} <seconds>
#
# Run:
#   sudo /opt/klai/scripts/persistence-probe.sh
#
# Intended invocation: systemd timer every 10 minutes — see
# /etc/systemd/system/klai-persistence-probe.{service,timer}.
#
# Exit codes:
#   0 — .prom written (with zero or more metrics, including non-existent files)
#   1 — textfile dir missing or unwritable
set -uo pipefail

TEXTFILE_DIR="${TEXTFILE_DIR:-/var/lib/node_exporter/textfile_collector}"
OUT="${TEXTFILE_DIR}/klai_persistence.prom"
NOW=$(date +%s)

if [ ! -d "${TEXTFILE_DIR}" ]; then
  echo "persistence-probe: ${TEXTFILE_DIR} does not exist" >&2
  exit 1
fi
if [ ! -w "${TEXTFILE_DIR}" ]; then
  echo "persistence-probe: ${TEXTFILE_DIR} is not writable" >&2
  exit 1
fi

# Probe targets — keep in sync with deploy/volume-mounts.yaml. Each line:
#   service<SPACE>absolute-path-on-host
#
# Bind mounts: the host path is the real filesystem location.
# Named Docker volumes: resolve dynamically via `docker volume inspect`.
#
# Only data-category volumes with a meaningful single-file signature are
# probed — directory-level volumes (postgres pg_wal, qdrant collections) use
# the newest child file.

_named_volume_path() {
  docker volume inspect "$1" --format '{{.Mountpoint}}' 2>/dev/null
}

_newest_in_dir() {
  local dir="$1"
  [ -d "${dir}" ] || { echo ""; return; }
  find "${dir}" -maxdepth 3 -type f -printf '%T@ %p\n' 2>/dev/null \
    | sort -nr | head -1 | awk '{print $2}'
}

# Build TARGETS array: "service|path"
TARGETS=()

# FalkorDB (bind mount)
TARGETS+=("falkordb|/opt/klai/falkordb-data/dump.rdb")

# Redis (named volume)
if p=$(_named_volume_path klai-core_redis-data) && [ -n "${p}" ]; then
  TARGETS+=("redis|${p}/dump.rdb")
fi

# Vexa-Redis (named volume, AOF)
if p=$(_named_volume_path klai-core_vexa-redis-data) && [ -n "${p}" ]; then
  TARGETS+=("vexa-redis|${p}/appendonlydir")
fi

# Postgres — newest file inside pg_wal (varies by image layout).
if p=$(_named_volume_path klai-core_postgres-data) && [ -n "${p}" ]; then
  wal=$(find "${p}" -maxdepth 5 -type d -name pg_wal 2>/dev/null | head -1)
  newest=$(_newest_in_dir "${wal}")
  [ -n "${newest}" ] && TARGETS+=("postgres|${newest}")
fi

# MongoDB (named volume) — newest WAL file.
if p=$(_named_volume_path klai-core_mongodb-data) && [ -n "${p}" ]; then
  newest=$(_newest_in_dir "${p}")
  [ -n "${newest}" ] && TARGETS+=("mongodb|${newest}")
fi

# Qdrant (named volume) — newest file under collections/.
if p=$(_named_volume_path klai-core_qdrant-data) && [ -n "${p}" ]; then
  newest=$(_newest_in_dir "${p}/collections")
  [ -n "${newest}" ] && TARGETS+=("qdrant|${newest}")
fi

# Garage (bind mount) — newest snapshot dir (proxy for activity).
newest=$(_newest_in_dir /opt/klai/garage-meta/snapshots)
[ -n "${newest}" ] && TARGETS+=("garage-meta|${newest}")

# Gitea (named volume) — newest file (repo push or internal state).
if p=$(_named_volume_path klai-core_gitea-data) && [ -n "${p}" ]; then
  newest=$(_newest_in_dir "${p}")
  [ -n "${newest}" ] && TARGETS+=("gitea|${newest}")
fi

# Meilisearch (named volume) — newest snapshot or data file.
if p=$(_named_volume_path klai-core_meilisearch-data) && [ -n "${p}" ]; then
  newest=$(_newest_in_dir "${p}")
  [ -n "${newest}" ] && TARGETS+=("meilisearch|${newest}")
fi

# Write the .prom file atomically: write to tmp, then rename.
TMP="${OUT}.$$.tmp"
{
  echo "# HELP klai_persistence_file_age_seconds Age in seconds of the most-recently-written file on a stateful-service data volume."
  echo "# TYPE klai_persistence_file_age_seconds gauge"
  for entry in "${TARGETS[@]}"; do
    svc="${entry%%|*}"
    path="${entry#*|}"
    if [ -e "${path}" ]; then
      mtime=$(stat -c %Y "${path}" 2>/dev/null || echo "${NOW}")
      age=$((NOW - mtime))
      # Escape backslashes + double-quotes in label values per Prometheus spec.
      esc_path=$(echo "${path}" | sed 's/\\/\\\\/g; s/"/\\"/g')
      echo "klai_persistence_file_age_seconds{service=\"${svc}\",path=\"${esc_path}\"} ${age}"
    else
      echo "klai_persistence_file_age_seconds{service=\"${svc}\",path=\"missing\"} -1"
    fi
  done
  echo "# HELP klai_persistence_probe_timestamp_seconds Unix timestamp of the last persistence-probe run."
  echo "# TYPE klai_persistence_probe_timestamp_seconds gauge"
  echo "klai_persistence_probe_timestamp_seconds ${NOW}"
} > "${TMP}"

mv "${TMP}" "${OUT}"
chmod 0644 "${OUT}"

echo "persistence-probe: wrote ${#TARGETS[@]} metrics → ${OUT}"
