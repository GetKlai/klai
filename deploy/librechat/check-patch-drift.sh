#!/bin/sh
# Validate LibreChat patch assumptions before syncing patched files to prod.
#
# This fails when the pinned LibreChat image, docker-compose image, portal-api
# provisioning default, or upstream files drift out of lockstep. That is
# intentional: mounted route/bundle patches are acceptable only when upgrades
# are explicit and reviewed.

set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
MANIFEST="$ROOT_DIR/deploy/librechat/patch-manifest.txt"
GETKLAI_MANIFEST="$ROOT_DIR/deploy/librechat/getklai/patch-manifest.txt"
LIBRECHAT_IMAGE="${LIBRECHAT_IMAGE:-ghcr.io/danny-avila/librechat:v0.8.7}"
FAIL=0

COMPOSE_IMAGE=$(awk '
  $1 == "librechat-getklai:" { in_service = 1; next }
  in_service && $1 == "image:" { print $2; exit }
  in_service && $1 ~ /^[a-zA-Z0-9_-]+:/ { exit }
' "$ROOT_DIR/deploy/docker-compose.yml")

if ! grep -q "librechat_image: str = \"$LIBRECHAT_IMAGE\"" \
  "$ROOT_DIR/klai-portal/backend/app/core/config.py"; then
  echo "ERROR: portal-api default librechat_image is not pinned to $LIBRECHAT_IMAGE" >&2
  FAIL=1
fi

validate_image_pin() {
  local image="$1"
  local label="$2"
  if printf '%s\n' "$image" | grep -Eq ':(latest|dev|staging)$'; then
    echo "ERROR: $label image must be explicitly pinned, got $image" >&2
    FAIL=1
  fi
}

validate_manifest() {
  local manifest="$1"
  local image="$2"
  while IFS='|' read -r local_patch upstream_path expected_sha _reason; do
  case "$local_patch" in
    ""|\#*) continue ;;
  esac

  if [ ! -f "$ROOT_DIR/deploy/librechat/$local_patch" ]; then
    echo "ERROR: manifest references missing patch deploy/librechat/$local_patch" >&2
    FAIL=1
    continue
  fi

  case "$local_patch" in
    *.js|*.cjs)
      node --check "$ROOT_DIR/deploy/librechat/$local_patch" >/dev/null
      ;;
  esac

  actual_sha=$(
    docker run --rm --entrypoint sh "$image" \
      -c "sha256sum '$upstream_path'" 2>/dev/null | awk '{ print $1 }'
  )
  if [ -z "$actual_sha" ]; then
    echo "ERROR: could not read $upstream_path from $image" >&2
    FAIL=1
    continue
  fi

  if [ "$actual_sha" != "$expected_sha" ]; then
    echo "ERROR: upstream drift for $local_patch" >&2
    echo "       image:    $image" >&2
    echo "       upstream: $upstream_path" >&2
    echo "       expected: $expected_sha" >&2
    echo "       actual:   $actual_sha" >&2
    FAIL=1
  fi
  done < "$manifest"
}

validate_image_pin "$LIBRECHAT_IMAGE" "Provisioned LibreChat"
validate_image_pin "$COMPOSE_IMAGE" "librechat-getklai"
validate_manifest "$MANIFEST" "$LIBRECHAT_IMAGE"

if [ -f "$GETKLAI_MANIFEST" ]; then
  validate_manifest "$GETKLAI_MANIFEST" "$COMPOSE_IMAGE"
fi

if [ "$FAIL" -ne 0 ]; then
  echo "" >&2
  echo "Fix: review the LibreChat upgrade, update the mounted patch, then update deploy/librechat/patch-manifest.txt." >&2
  exit 1
fi

echo "OK: LibreChat image pins and mounted patch upstream hashes match provisioning=$LIBRECHAT_IMAGE getklai=$COMPOSE_IMAGE."
