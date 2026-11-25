#!/usr/bin/env bash
# deploy/scripts/chat-image-rollout-host.sh <op> [args]
#
# The core-01 half of the staged chat image rollout. chat-image-rollout.sh (on
# the runner) copies this script and tenant-config-regenerate.sh from its own
# commit into a per-run directory on core-01 and calls one op per ssh call:
#
#   read-state            print the rollout state file, or nothing when absent
#   write-state           replace the state file with the JSON on stdin
#   ensure-baseline       no state file: write one with phase stable and the
#                         one image every provisioning-managed chat container
#                         runs; fails when they run more than one
#   images                one "<slug> <image>" line per provisioning-managed
#                         chat container: what runs, read back after a move
#   canary-check <slug>   fails unless the tenant may be the canary: its
#                         container is provisioning-managed, its .env marks it
#                         as synthetic, and portal-api has no image override
#                         for it (an override would win over the state file)
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
KLAI_DIR="${KLAI_COMPOSE_DIR:-/opt/klai}"
LIBRECHAT_DIR="${KLAI_LIBRECHAT_DIR:-$KLAI_DIR/librechat}"
DOCKER="${KLAI_DOCKER_BIN:-docker}"
STATE="$LIBRECHAT_DIR/image-rollout.json"
SLUG_RE='^[a-z0-9][a-z0-9-]{0,57}$'
DIGEST_RE='^[^[:space:]@]+@sha256:[0-9a-f]{64}$'
# Set by an operator in the tenant's own .env, never by provisioning: the
# platform has no tenant-level "synthetic" field, and the journeys' config
# (playwright.prod.config.ts) also runs against real customer tenants.
SYNTHETIC_MARKER='KLAI_TENANT_PURPOSE=synthetic'

die() { echo "chat-image-rollout-host: $*" >&2; exit 1; }

images() {
  "$DOCKER" ps -a --filter label=klai.managed_by=portal-api-provisioning --filter label=klai.kind=librechat \
    --format '{{.Label "klai.tenant_slug"}} {{.Image}}'
}

write_state() {
  local tmp
  tmp="$(mktemp "$STATE.XXXXXX")"
  trap 'rm -f "$tmp"' EXIT
  cat > "$tmp"
  # python3, not jq: deploy-compose.yml already runs python3 on core-01,
  # nothing there proves jq is installed. Same rules as portal-api's loader,
  # so a state it would refuse is never written.
  python3 -I - "$tmp" <<'PY' || die "refusing to write an invalid state"
import json, re, sys
state = json.load(open(sys.argv[1]))
digest = re.compile(r"^[^\s@]+@sha256:[0-9a-f]{64}$")
phase = state.get("phase")
ok = phase in ("stable", "canary", "fleet", "reverting", "rejected") and digest.match(state.get("stable") or "")
if phase in ("canary", "fleet", "reverting"):
    ok = ok and digest.match(state.get("candidate") or "")
sys.exit(0 if ok else 1)
PY
  # portal-api reads the file as another user; mktemp creates it 0600.
  chmod 0644 "$tmp"
  # Same directory, so the rename is atomic: portal-api never reads half a file.
  mv -f "$tmp" "$STATE"
  trap - EXIT
}

op="${1:-}"
case "$op" in
  read-state)
    if [[ -f "$STATE" ]]; then cat "$STATE"; fi
    ;;
  write-state)
    write_state
    ;;
  ensure-baseline)
    [[ -f "$STATE" ]] && exit 0
    distinct="$(images | awk '{ print $2 }' | sort -u)"
    # One line that is a digest reference: exactly one image, pinned.
    [[ "$distinct" =~ $DIGEST_RE ]] \
      || die "no rollout state, and the provisioning-managed chat containers do not run exactly one digest-pinned image: '${distinct//$'\n'/, }'"
    printf '{"phase": "stable", "stable": "%s", "candidate": "", "canary": ""}\n' "$distinct" | write_state
    echo "baseline written: every provisioning-managed tenant runs $distinct"
    ;;
  images)
    images
    ;;
  canary-check)
    slug="${2:-}"
    [[ "$slug" =~ $SLUG_RE ]] || die "not a tenant slug: '$slug'"
    line="$("$DOCKER" inspect --format \
      '{{index .Config.Labels "klai.managed_by"}} {{index .Config.Labels "klai.tenant_slug"}}' \
      "librechat-$slug")" || die "no chat container for tenant '$slug'"
    read -r managed_by label <<< "$line"
    # A compose-managed container is never recreated by portal-api, so it
    # cannot be the canary: the image would not move.
    [[ "$managed_by" == portal-api-provisioning && "$label" == "$slug" ]] \
      || die "the chat container of '$slug' is not provisioning-managed (managed_by='$managed_by', tenant_slug='$label')"
    grep -qxF "$SYNTHETIC_MARKER" "$LIBRECHAT_DIR/$slug/.env" 2>/dev/null \
      || die "tenant '$slug' is not marked synthetic ($SYNTHETIC_MARKER in its .env); refusing to use it as the canary"
    overrides="$("$DOCKER" compose -f "$KLAI_DIR/docker-compose.yml" exec -T portal-api printenv LIBRECHAT_IMAGE_OVERRIDES || true)"
    [[ ",${overrides// /}," != *",$slug="* ]] \
      || die "portal-api pins tenant '$slug' through LIBRECHAT_IMAGE_OVERRIDES, which wins over the rollout state"
    ;;
  recreate)
    slug="${2:-}"
    [[ -z "$slug" || "$slug" =~ $SLUG_RE ]] || die "not a tenant slug: '$slug'"
    RECREATE_CONTAINERS=true TENANT="$slug" bash "$(dirname "$0")/tenant-config-regenerate.sh"
    ;;
  *)
    die "usage: ${0##*/} read-state | write-state | ensure-baseline | images | canary-check <slug> | recreate [<slug>]"
    ;;
esac
