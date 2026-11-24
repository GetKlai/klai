#!/usr/bin/env bash
# deploy/scripts/chat-image-rollout-host.sh <op> [args]
#
# The core-01 half of the staged chat image rollout. chat-image-rollout.sh (on
# the runner) copies this script and tenant-config-regenerate.sh from its own
# commit into a per-run directory on core-01 and calls one op per ssh call:
#
#   read-state            print the rollout state file, or nothing when absent
#   write-state           replace the state file with the JSON on stdin
#   running-image <slug>  print the image the tenant's chat container runs; the
#                         container must be provisioning-managed and carry
#                         that slug, or this fails
#   recreate [<slug>]     recreate one tenant, or every provisioning-managed
#                         tenant, through portal-api (tenant-config-regenerate.sh)
#
# The state file sits in the directory portal-api mounts at /librechat, and
# portal-api reads it on every container create (app/core/librechat_images.py),
# so a state change takes effect on the next recreate without restarting
# portal-api.
#
# Tests: deploy/scripts/tests/chat-image-rollout.test.sh

set -euo pipefail

# Seams for the test-suite; production keeps every default.
LIBRECHAT_DIR="${KLAI_LIBRECHAT_DIR:-/opt/klai/librechat}"
DOCKER="${KLAI_DOCKER_BIN:-docker}"
STATE="$LIBRECHAT_DIR/image-rollout.json"
SLUG_RE='^[a-z0-9][a-z0-9-]{0,57}$'

die() { echo "chat-image-rollout-host: $*" >&2; exit 1; }

op="${1:-}"
case "$op" in
  read-state)
    if [[ -f "$STATE" ]]; then cat "$STATE"; fi
    ;;
  write-state)
    tmp="$(mktemp "$STATE.XXXXXX")"
    trap 'rm -f "$tmp"' EXIT
    cat > "$tmp"
    # python3, not jq: deploy-compose.yml already runs python3 on core-01,
    # nothing there proves jq is installed.
    python3 -I -c 'import json, sys; sys.exit(json.load(open(sys.argv[1])).get("phase") not in ("stable", "canary", "fleet", "rejected"))' "$tmp" \
      || die "refusing to write a state without a known phase"
    # portal-api reads the file as another user; mktemp creates it 0600.
    chmod 0644 "$tmp"
    # Same directory, so the rename is atomic: portal-api never reads half a file.
    mv -f "$tmp" "$STATE"
    trap - EXIT
    ;;
  running-image)
    slug="${2:-}"
    [[ "$slug" =~ $SLUG_RE ]] || die "not a tenant slug: '$slug'"
    line="$("$DOCKER" inspect --format \
      '{{index .Config.Labels "klai.managed_by"}} {{index .Config.Labels "klai.tenant_slug"}} {{.Config.Image}}' \
      "librechat-$slug")" || die "no chat container for tenant '$slug'"
    read -r managed_by label image <<< "$line"
    # A compose-managed container is never recreated by portal-api, so it
    # cannot be the canary: the image would not move.
    [[ "$managed_by" == portal-api-provisioning && "$label" == "$slug" ]] \
      || die "the chat container of '$slug' is not provisioning-managed (managed_by='$managed_by', tenant_slug='$label')"
    echo "$image"
    ;;
  recreate)
    slug="${2:-}"
    [[ -z "$slug" || "$slug" =~ $SLUG_RE ]] || die "not a tenant slug: '$slug'"
    RECREATE_CONTAINERS=true TENANT="$slug" bash "$(dirname "$0")/tenant-config-regenerate.sh"
    ;;
  *)
    die "usage: ${0##*/} read-state | write-state | running-image <slug> | recreate [<slug>]"
    ;;
esac
