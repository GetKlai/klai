#!/bin/sh
# SPEC-LIBRECHAT-PATCH-MODEL-001 — the Klai LibreChat image is pinned by DIGEST,
# never by tag.
#
# Incident 2026-08-14: `ghcr.io/getklai/librechat:v0.8.7-klai.1` was pushed
# twice with different content. The tag silently moved from
# sha256:518c181f… to sha256:42fa8a92…. A canary pinned to that tag could not
# be rolled back to what it was actually running, because the tag no longer
# named that image.
#
# The build workflow now refuses to overwrite a tag, but that only protects
# tags it creates. This guard protects the consuming end: nothing may reference
# our LibreChat image by a name that can move.
#
# usage: check-klai-librechat-digest.sh [file ...]
#        (defaults to the production manifests)

set -eu

if [ $# -gt 0 ]; then
    FILES="$*"
else
    FILES="deploy/docker-compose.yml klai-portal/backend/app/core/config.py"
fi

IMAGE='ghcr.io/getklai/librechat'
FAIL=0

for f in $FILES; do
    [ -f "$f" ] || continue

    # Every mention of the image, minus comment lines: a comment explaining the
    # rule must not trip the rule.
    refs=$(grep -n "$IMAGE" "$f" | grep -vE '^[0-9]+: *#' || true)
    [ -n "$refs" ] || continue

    # A digest reference is ghcr.io/getklai/librechat@sha256:<64 hex>.
    bad=$(printf '%s\n' "$refs" | grep -vE "${IMAGE}@sha256:[0-9a-f]{64}" || true)
    if [ -n "$bad" ]; then
        echo "ERROR: $IMAGE referenced by tag instead of digest in $f:" >&2
        printf '%s\n' "$bad" >&2
        FAIL=1
    fi
done

if [ "$FAIL" -ne 0 ]; then
    cat >&2 <<'EOF'

A tag can be moved; a digest cannot. On 2026-08-14 the tag
ghcr.io/getklai/librechat:v0.8.7-klai.1 was overwritten with a second,
different image, so "roll back to that tag" stopped meaning anything.

Pin the digest the build workflow printed:

    image: ghcr.io/getklai/librechat@sha256:<64 hex>

Keep the human-readable tag in a comment next to it if you want to know which
build it is.
EOF
    exit 1
fi

echo "OK: every $IMAGE reference is digest-pinned."
