#!/bin/sh
# Print every distinct digest-pinned Klai LibreChat image in a compose file,
# one ghcr.io/getklai/librechat@sha256:<64 hex> reference per line.
#
# usage: librechat-pinned-digests.sh <compose-file>
#
# Same parsing as deploy/check-klai-librechat-digest.sh: every mention of the
# image minus comment lines. That covers both pins in deploy/docker-compose.yml,
# the librechat-getklai service and LIBRECHAT_IMAGE of portal-api (the image
# tenant containers are created from). Exits 1 when there is none, so a
# renamed image cannot make the boot check pass on nothing.
set -eu

[ $# -eq 1 ] && [ -f "$1" ] || { echo "usage: $0 <compose-file>" >&2; exit 2; }

IMAGE='ghcr.io/getklai/librechat'
refs=$(grep -n "$IMAGE" "$1" | grep -vE '^[0-9]+: *#' | grep -oE "${IMAGE}@sha256:[0-9a-f]{64}" | sort -u || true)
[ -n "$refs" ] || { echo "no digest-pinned $IMAGE found in $1" >&2; exit 1; }
printf '%s\n' "$refs"
