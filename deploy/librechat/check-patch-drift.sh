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
FAIL=0

# The fleet image is what portal-api actually hands to `containers.run`, which
# is the LIBRECHAT_IMAGE env in docker-compose.yml when set, and only otherwise
# the config.py default. Hardcoding upstream here made the guard validate an
# image the fleet no longer runs the moment Phase 5 flipped that env var --
# checking the wrong artifact is worse than not checking, because it looks
# green (SPEC-LIBRECHAT-PATCH-MODEL-001 Phase 5).
COMPOSE_FLEET_IMAGE=$(awk -F': ' '
  $1 ~ /^ +LIBRECHAT_IMAGE$/ { gsub(/^ +| +$/, "", $2); print $2; exit }
' "$ROOT_DIR/deploy/docker-compose.yml")

CONFIG_DEFAULT_IMAGE=$(sed -n 's/.*librechat_image: str = "\([^"]*\)".*/\1/p' \
  "$ROOT_DIR/klai-portal/backend/app/core/config.py" | head -1)

LIBRECHAT_IMAGE="${LIBRECHAT_IMAGE:-${COMPOSE_FLEET_IMAGE:-$CONFIG_DEFAULT_IMAGE}}"

if [ -z "$LIBRECHAT_IMAGE" ]; then
  echo "ERROR: could not resolve the fleet LibreChat image from compose or config.py" >&2
  exit 1
fi

COMPOSE_IMAGE=$(awk '
  $1 == "librechat-getklai:" { in_service = 1; next }
  in_service && $1 == "image:" { print $2; exit }
  in_service && $1 ~ /^[a-zA-Z0-9_-]+:/ { exit }
' "$ROOT_DIR/deploy/docker-compose.yml")

# config.py's default is the fallback when the env var is absent, so it must
# stay a real, pullable image -- but it is NOT required to equal the fleet
# image once compose overrides it. Requiring equality would force the fallback
# to move in lockstep with the rollout, which defeats the point of having one.
if [ -z "$CONFIG_DEFAULT_IMAGE" ]; then
  echo "ERROR: could not read librechat_image default from config.py" >&2
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

# Verify every runtime (entrypoint-applied, not bind-mounted) patch target
# still exists in the image, for both possible LibreChat data-schemas
# shapes. This is the check that would have caught the 2026-08-13 incident:
# the manifest above only covers bind-mounted patch files; the entrypoint's
# Meili tenant-index rewrite and the feedback-forwarding rewrite target
# files that are patched IN PLACE inside the running container and were
# never covered by the manifest/sha256 check, so a LibreChat upgrade that
# moves or removes those files went undetected until the container
# crashlooped in production.
validate_runtime_targets() {
  local image="$1"
  local label="$2"

  local legacy_message='/app/packages/data-schemas/dist/models/message.cjs'
  local legacy_convo='/app/packages/data-schemas/dist/models/convo.cjs'
  local legacy_mongo_meili='/app/packages/data-schemas/dist/models/plugins/mongoMeili.cjs'
  local bundled='/app/packages/data-schemas/dist/index.cjs'
  local index_sync='/app/api/db/indexSync.js'
  local feedback_route='/app/api/server/routes/messages.js'

  local report
  report=$(
    docker run --rm --entrypoint sh "$image" -c "
      for p in '$legacy_message' '$legacy_convo' '$legacy_mongo_meili' '$bundled' '$index_sync' '$feedback_route'; do
        if [ -f \"\$p\" ]; then echo \"1 \$p\"; else echo \"0 \$p\"; fi
      done
    " 2>/dev/null
  )

  if [ -z "$report" ]; then
    echo "ERROR: could not inspect runtime patch targets in $image (docker run failed)" >&2
    FAIL=1
    return
  fi

  local legacy_count=0
  local bundled_present=0
  local index_sync_present=0
  local feedback_present=0

  while read -r present path; do
    case "$path" in
      "$legacy_message" | "$legacy_convo" | "$legacy_mongo_meili")
        [ "$present" = "1" ] && legacy_count=$((legacy_count + 1))
        ;;
      "$bundled")
        [ "$present" = "1" ] && bundled_present=1
        ;;
      "$index_sync")
        [ "$present" = "1" ] && index_sync_present=1
        ;;
      "$feedback_route")
        [ "$present" = "1" ] && feedback_present=1
        ;;
    esac
  done <<REPORT
$report
REPORT

  # The klai-entrypoint.sh Meili patch tries the pre-rolldown per-model
  # shape (all 3 legacy files) first, then falls back to the rolldown
  # bundled dist/index.cjs. Anything else (a partial legacy set, or neither
  # shape) means the entrypoint will throw "required LibreChat Meili patch
  # target is missing" / "ambiguous ... dist shape" at container boot.
  if [ "$legacy_count" -eq 3 ]; then
    : # pre-rolldown data-schemas shape (<= v0.8.6) — entrypoint's legacy path handles it
  elif [ "$legacy_count" -eq 0 ] && [ "$bundled_present" -eq 1 ]; then
    : # rolldown-bundled data-schemas shape (>= v0.8.7) — entrypoint's bundled fallback handles it
  else
    echo "ERROR: $label Meili patch targets are ambiguous/missing in $image" >&2
    echo "       found $legacy_count/3 pre-rolldown per-model files, bundled dist/index.cjs present=$bundled_present" >&2
    echo "       expected either all of: $legacy_message, $legacy_convo, $legacy_mongo_meili" >&2
    echo "       or the bundled: $bundled" >&2
    FAIL=1
  fi

  if [ "$index_sync_present" -ne 1 ]; then
    echo "ERROR: $label Meili indexSync patch target missing in $image: $index_sync" >&2
    FAIL=1
  fi

  if [ "$feedback_present" -ne 1 ]; then
    echo "ERROR: $label feedback-route patch target missing in $image: $feedback_route" >&2
    FAIL=1
  fi
}

# Dry-run stage (2026-08-13 review finding 4): validate_runtime_targets above
# only checks that the runtime patch target FILES still exist in the image.
# That is not enough -- upstream can keep a file's path stable while
# reshaping its contents (renamed variable, moved anchor, different quote
# style), which passes the existence check but breaks the patch. This stage
# EXECUTES the actual entrypoint transform logic (extracted from
# klai-entrypoint.sh / getklai/entrypoint.sh, not a second copy of it)
# against files pulled from the image, so upstream syntax drift is caught
# here instead of at container boot.
dry_run_transforms() {
  local image="$1"
  local label="$2"

  local tmp
  tmp=$(mktemp -d)

  local targets="
/app/packages/data-schemas/dist/models/message.cjs
/app/packages/data-schemas/dist/models/convo.cjs
/app/packages/data-schemas/dist/models/plugins/mongoMeili.cjs
/app/packages/data-schemas/dist/index.cjs
/app/api/db/indexSync.js
/app/api/server/routes/messages.js
/app/packages/api/dist/index.cjs
"
  local p dest
  for p in $targets; do
    dest="$tmp$p"
    mkdir -p "$(dirname "$dest")"
    if ! docker run --rm --entrypoint cat "$image" "$p" >"$dest" 2>/dev/null || [ ! -s "$dest" ]; then
      rm -f "$dest"
    fi
  done

  if ! node "$ROOT_DIR/deploy/librechat/dry-run-transforms.cjs" \
      "$tmp" \
      "$ROOT_DIR/deploy/librechat/klai-entrypoint.sh" \
      "$ROOT_DIR/deploy/librechat/getklai/entrypoint.sh"; then
    echo "ERROR: $label runtime transform dry-run failed against $image (see DRY-RUN FAIL lines above)" >&2
    FAIL=1
  fi

  rm -rf "$tmp"
}

validate_image_pin "$LIBRECHAT_IMAGE" "Provisioned LibreChat"
validate_image_pin "$COMPOSE_IMAGE" "librechat-getklai"
# The fleet manifest pins UPSTREAM hashes of files the tenants bind-mount. Once
# the fleet image bakes those patches in, the tenants stop mounting them (see
# image_bakes_in_patches) and the manifest describes a model that no longer
# applies -- provenance then comes from the build manifest inside the image.
if printf '%s' "$LIBRECHAT_IMAGE" | grep -q '^ghcr.io/getklai/librechat'; then
  echo "SKIP [manifest]: fleet runs the Klai-owned image; patches are baked in, not mounted"
else
  validate_manifest "$MANIFEST" "$LIBRECHAT_IMAGE"
fi
validate_runtime_targets "$LIBRECHAT_IMAGE" "Provisioned LibreChat"
dry_run_transforms "$LIBRECHAT_IMAGE" "Provisioned LibreChat"

if [ -f "$GETKLAI_MANIFEST" ]; then
  validate_manifest "$GETKLAI_MANIFEST" "$COMPOSE_IMAGE"
fi

if [ -n "$COMPOSE_IMAGE" ]; then
  validate_runtime_targets "$COMPOSE_IMAGE" "librechat-getklai"
  dry_run_transforms "$COMPOSE_IMAGE" "librechat-getklai"
fi

if [ "$FAIL" -ne 0 ]; then
  echo "" >&2
  echo "Fix: review the LibreChat upgrade, update the mounted patch, then update deploy/librechat/patch-manifest.txt." >&2
  exit 1
fi

echo "OK: LibreChat image pins and mounted patch upstream hashes match provisioning=$LIBRECHAT_IMAGE getklai=$COMPOSE_IMAGE."
