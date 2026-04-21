#!/usr/bin/env bash
# audit-compose-volumes.sh — verify every persistent bind mount lands on the
# image's actual data path.
#
# Context: SPEC-INFRA-005 / 2026-04-19 FalkorDB incident. A bind mount declared
# in docker-compose.yml tells Docker WHERE to mount, not WHERE the image writes.
# If the mount target does not match the image's configured data path
# (FALKORDB_DATA_PATH, PGDATA, REDIS_DIR, etc.), all writes go to the
# container's ephemeral writable layer and vanish on recreate.
#
# Two-part check:
#   A. Every persistent mount in deploy/docker-compose.yml must be registered
#      in deploy/volume-mounts.yaml. "Persistent" = RW directory bind to a
#      /opt/klai/* host path, OR a named volume declared in the top-level
#      `volumes:` section.
#   B. For each entry in deploy/volume-mounts.yaml, the container_path must
#      match one of the image's known data paths — either an env var in the
#      DATA_PATH_KEYS list, an explicit conventional path we've curated, or
#      the inventory entry's declared data_path_source with `verified: true`.
#
# Exits non-zero on any violation. Intended to run in CI on PRs that touch
# deploy/docker-compose.yml or deploy/volume-mounts.yaml.
#
# Usage:
#   scripts/audit-compose-volumes.sh               # run the audit
#   scripts/audit-compose-volumes.sh --inspect SVC # dump image env for one service
#
# Exit codes:
#   0 = all checks passed
#   1 = one or more violations (details on stderr)
#   2 = configuration error (missing docker, missing compose/inventory)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${REPO_ROOT}/deploy/docker-compose.yml"
INVENTORY_FILE="${REPO_ROOT}/deploy/volume-mounts.yaml"

# Env-var names that, when present on the image, point to the canonical data
# path for that service. Order = preference (first hit wins).
DATA_PATH_KEYS=(
  FALKORDB_DATA_PATH
  PGDATA
  MONGO_DBPATH
  REDIS_DATA_DIR
  MEILI_DB_PATH
  MEILI_DATA_PATH
  GITEA_WORK_DIR
  GF_PATHS_DATA
  GRAFANA_PATHS_DATA
  QDRANT__STORAGE__STORAGE_PATH
  GARAGE_METADATA_DIR
  GARAGE_DATA_DIR
  OLLAMA_MODELS
  AUDIO_STORAGE_DIR
)

# Conventional paths — upstream images that don't declare a data-path env var
# but still write to a well-known location.
CONVENTIONAL_PATHS=(
  /var/lib/postgresql/data
  /var/lib/postgresql
  /data/db
  /var/lib/falkordb/data
  /var/lib/garage/meta
  /var/lib/garage/data
  /var/lib/grafana
  /qdrant/storage
  /meili_data
  /data
  /storage
  /vlogs-data
  /root/.ollama
  /data/audio
  /code/uploads
  /gitea-data   # gitea maps /data
)

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "error: '$1' is required but not installed" >&2
    exit 2
  }
}

need docker

# Prefer host-installed yq/jq (fast, no docker-in-docker fragility). Fall back
# to pinned docker images when the host doesn't have them (e.g., someone's
# laptop without a yq install).
if command -v yq >/dev/null 2>&1; then
  YQ() { yq "$@"; }
else
  YQ() { docker run --rm -i -v "${REPO_ROOT}:/repo:ro" -w /repo mikefarah/yq:4.47.1 "$@"; }
fi
if command -v jq >/dev/null 2>&1; then
  JQ() { jq "$@"; }
else
  JQ() { docker run --rm -i ghcr.io/jqlang/jq:1.7.1 "$@"; }
fi

[[ -f "${COMPOSE_FILE}" ]] || {
  echo "error: ${COMPOSE_FILE} not found" >&2
  exit 2
}

# ---- Extract persistent mounts from compose -----------------------------
#
# A mount is "persistent" if:
#   - it is a named volume (declared in the top-level `volumes:`), OR
#   - it is a bind mount whose host path is a DIRECTORY (not a single file
#     like /path/Caddyfile) AND it is NOT read-only (no `:ro` flag).
#
# Produces TSV rows: service<TAB>kind<TAB>host_or_volume<TAB>container_path
extract_persistent_mounts() {
  YQ -o=json deploy/docker-compose.yml | JQ -r '
    .services // {} | to_entries[] |
    .key as $svc |
    (.value.volumes // []) | map(
      if type == "string" then
        (split(":")) as $parts |
        {
          host: ($parts[0] // ""),
          container: ($parts[1] // ""),
          mode: ($parts[2] // "rw")
        }
      elif type == "object" then
        {
          host: (.source // ""),
          container: (.target // ""),
          mode: (if (.read_only // false) then "ro" else "rw" end)
        }
      else empty end
    ) | map(
      select((.container // "") != "") |
      select((.host // "") != "") |
      # Drop read-only mounts — those are configs/static assets, not data.
      select(.mode != "ro") |
      # Drop bind mounts to a single file (anything with a file extension
      # in the last segment of the container path).
      select(
        ((.host | tostring) | test("^[/.]") | not) or
        ((.container | tostring) | split("/") | last | contains(".") | not)
      ) |
      # Classify as bind or named.
      if ((.host | tostring) | test("^[/.]")) then
        . + { kind: "bind" }
      else
        . + { kind: "named" }
      end
    ) |
    .[] | [$svc, .kind, .host, .container] | @tsv
  '
}

# Produces: service<TAB>image
extract_images() {
  YQ -o=json deploy/docker-compose.yml | JQ -r '
    .services | to_entries[] |
    [.key, (.value.image // "")] | @tsv
  '
}

# Print one env-derived data path per line (empty if no match).
image_env_paths() {
  local image="$1"
  [[ -z "${image}" ]] && return 0
  docker image inspect "${image}" >/dev/null 2>&1 || return 0

  local env_json
  env_json=$(docker image inspect "${image}" --format '{{json .Config.Env}}' 2>/dev/null || echo '[]')
  [[ -z "${env_json}" || "${env_json}" == "null" ]] && return 0

  {
    for key in "${DATA_PATH_KEYS[@]}"; do
      echo "${env_json}" | JQ -r --arg k "${key}" '
        .[] | select(startswith($k + "=")) | split("=") | .[1]
      ' 2>/dev/null || true
    done
  } | grep -v '^$' | head -5 || true
}

# Is `container_path` a plausible data-receiving path for `image`?
path_matches_image() {
  local image="$1" container_path="$2"
  container_path="${container_path%/}"
  [[ -z "${container_path}" ]] && return 1

  # Allow any of: env-derived paths, curated conventional paths, or a parent
  # of a known path (mounting /var/lib/postgresql covers /var/lib/postgresql/data).
  local candidates=()
  while IFS= read -r p; do
    [[ -n "${p}" ]] && candidates+=("${p%/}")
  done < <(image_env_paths "${image}")
  candidates+=("${CONVENTIONAL_PATHS[@]}")

  for known in "${candidates[@]}"; do
    known="${known%/}"
    [[ -z "${known}" ]] && continue
    [[ "${known}" == "${container_path}" ]] && return 0
    # A parent-dir mount also covers a child data path.
    [[ "${known}" == "${container_path}"/* ]] && return 0
  done
  return 1
}

# Cached inventory lines as TSV:
#   key<TAB>service<TAB>kind<TAB>host<TAB>container_path<TAB>category<TAB>image
INVENTORY_CACHE="$(mktemp)"
trap 'rm -f "${INVENTORY_CACHE}"' EXIT

build_inventory_cache() {
  if [[ -f "${INVENTORY_FILE}" ]]; then
    YQ -o=json deploy/volume-mounts.yaml | JQ -r '
      (.mounts // {}) | to_entries[] |
      [
        .key,
        (.value.service // ""),
        (.value.kind // ""),
        (.value.host // ""),
        (.value.container_path // ""),
        (.value.category // ""),
        (.value.image // ""),
        ((.value.verified // false) | tostring)
      ] | @tsv
    ' > "${INVENTORY_CACHE}"
  fi
}

# True if any row in the cache matches service + host + container.
inventory_has_mount() {
  local svc="$1" host="$2" container="$3"
  awk -F '\t' -v s="${svc}" -v h="${host}" -v c="${container}" '
    $2==s && $4==h && $5==c { found=1; exit 0 }
    END { exit found ? 0 : 1 }
  ' "${INVENTORY_CACHE}"
}

# ---- Inspect mode --------------------------------------------------------

if [[ "${1:-}" == "--inspect" ]]; then
  svc="${2:?usage: $0 --inspect <service>}"
  img=$(extract_images | awk -F '\t' -v s="${svc}" '$1==s { print $2 }')
  [[ -z "${img}" ]] && { echo "no image for ${svc}"; exit 2; }
  echo "Image: ${img}"
  echo ""
  echo "Env vars (full):"
  docker image inspect "${img}" --format '{{range .Config.Env}}{{println .}}{{end}}' \
    | sort
  echo ""
  echo "Declared VOLUMEs in image:"
  docker image inspect "${img}" --format '{{range $k, $v := .Config.Volumes}}{{println $k}}{{end}}' || true
  echo ""
  echo "WorkingDir: $(docker image inspect "${img}" --format '{{.Config.WorkingDir}}')"
  exit 0
fi

# ---- Main ----------------------------------------------------------------

declare -A SERVICE_IMAGE
while IFS=$'\t' read -r svc img; do
  [[ -n "${svc}" ]] && SERVICE_IMAGE["${svc}"]="${img}"
done < <(extract_images)

build_inventory_cache

violations_registry=0    # part A: persistent mount not in inventory
violations_mismatch=0    # part B: inventory container_path doesn't match image
rows=0

echo ""
echo "=== Part A: persistent mounts declared in docker-compose.yml ==="
printf '%-24s %-8s %-42s %-36s %s\n' "SERVICE" "KIND" "HOST/VOLUME" "CONTAINER_PATH" "STATUS"
printf '%-24s %-8s %-42s %-36s %s\n' "-------" "----" "-----------" "--------------" "------"

while IFS=$'\t' read -r svc kind host container; do
  [[ -z "${svc}" ]] && continue
  rows=$((rows+1))

  if inventory_has_mount "${svc}" "${host}" "${container}"; then
    status="OK"
  else
    status="MISSING from volume-mounts.yaml"
    violations_registry=$((violations_registry+1))
    {
      echo ""
      echo "  → ${svc}: persistent mount (${kind}) ${host}:${container} not in inventory"
      echo "    Fix: add an entry under 'mounts:' in deploy/volume-mounts.yaml."
    } >&2
  fi
  printf '%-24s %-8s %-42s %-36s %s\n' "${svc}" "${kind}" "${host}" "${container}" "${status}"
done < <(extract_persistent_mounts)

echo ""
echo "=== Part B: inventory entries vs image's actual data path ==="

if [[ ! -f "${INVENTORY_FILE}" ]]; then
  echo "(skipped — ${INVENTORY_FILE} does not exist yet)"
else
  printf '%-28s %-38s %-38s %s\n' "KEY (service)" "CONTAINER_PATH" "IMAGE_SAYS" "STATUS"
  printf '%-28s %-38s %-38s %s\n' "-------------" "--------------" "----------" "------"

  while IFS=$'\t' read -r key svc kind host container_path category image verified; do
    [[ -z "${key}" ]] && continue
    # config/ephemeral mounts are source-controlled or regenerable — skip image check.
    if [[ "${category}" != "data" ]]; then
      printf '%-28s %-38s %-38s %s\n' "${key} (${svc})" "${container_path}" "(category=${category})" "SKIP"
      continue
    fi
    # Explicit override: `verified: true` in the YAML declares the path has been
    # audited manually (e.g., app-specific literal like research-uploads, or a
    # dynamic writer path like caddy-tenants). Skip the image-env check.
    if [[ "${verified}" == "true" ]]; then
      printf '%-28s %-38s %-38s %s\n' "${key} (${svc})" "${container_path}" "(verified)" "OK"
      continue
    fi
    if path_matches_image "${image}" "${container_path}"; then
      env_hint=$(image_env_paths "${image}" 2>/dev/null | head -1 || true)
      [[ -z "${env_hint}" ]] && env_hint="(conventional)"
      printf '%-28s %-38s %-38s %s\n' "${key} (${svc})" "${container_path}" "${env_hint}" "OK"
    else
      env_hint=$(image_env_paths "${image}" 2>/dev/null | head -1 || true)
      [[ -z "${env_hint}" ]] && env_hint="(no env match)"
      printf '%-28s %-38s %-38s %s\n' "${key} (${svc})" "${container_path}" "${env_hint}" "MISMATCH"
      violations_mismatch=$((violations_mismatch+1))
      {
        echo ""
        echo "  → ${key} (${svc}, ${image})"
        echo "    inventory container_path : ${container_path}"
        echo "    image env paths          : $(image_env_paths "${image}" 2>/dev/null | paste -sd, - || true)"
        echo "    Fix: either (a) align the compose mount with the image's data path,"
        echo "         (b) add the path to CONVENTIONAL_PATHS in this script if widely known,"
        echo "         or (c) set 'verified: true' on this inventory entry after manual audit."
      } >&2
    fi
  done < "${INVENTORY_CACHE}"
fi

echo ""
total=$((violations_registry + violations_mismatch))
if [[ "${total}" -gt 0 ]]; then
  echo "audit-compose-volumes: FAILED (${violations_registry} unregistered mount(s), ${violations_mismatch} image mismatch(es))" >&2
  exit 1
fi

echo "audit-compose-volumes: OK — ${rows} persistent mount(s) verified against inventory and image metadata."
