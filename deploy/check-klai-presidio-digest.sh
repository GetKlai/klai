#!/bin/sh
# SPEC-PRIVACY-MISTRAL-PII-001 Phase 1 — the Klai presidio-analyzer image is
# pinned by DIGEST, never by tag.
#
# Same defect class as the LibreChat incident (2026-08-14,
# check-klai-librechat-digest.sh): a tag can be silently overwritten, and a
# canary — or here, a core-01 service — pinned to that tag can no longer be
# rolled back to what it was actually running once that happens.
#
# The build workflow (.github/workflows/presidio-analyzer-image-build.yml)
# refuses to overwrite a tag, but that only protects tags it creates. This
# guard protects the consuming end: nothing may reference the presidio-
# analyzer image by a name that can move.
#
# usage: check-klai-presidio-digest.sh [file ...]
#        (defaults to the production compose file)

set -eu

if [ $# -gt 0 ]; then
    FILES="$*"
else
    FILES="deploy/docker-compose.yml"
fi

IMAGE='ghcr.io/getklai/presidio-analyzer'
FAIL=0

for f in $FILES; do
    [ -f "$f" ] || continue

    # Every mention of the image, minus comment lines: a comment explaining the
    # rule must not trip the rule.
    refs=$(grep -n "$IMAGE" "$f" | grep -vE '^[0-9]+: *#' || true)
    [ -n "$refs" ] || continue

    # A digest reference is ghcr.io/getklai/presidio-analyzer@sha256:<64 hex>.
    bad=$(printf '%s\n' "$refs" | grep -vE "${IMAGE}@sha256:[0-9a-f]{64}" || true)
    if [ -n "$bad" ]; then
        echo "ERROR: $IMAGE referenced by tag instead of digest in $f:" >&2
        printf '%s\n' "$bad" >&2
        FAIL=1
    fi

    # Well-formed is not the same as real. The image-build workflow only
    # publishes a digest on a push to main, so the bootstrap PR necessarily
    # carries a placeholder — and a placeholder is 64 hex characters, so the
    # check above passes it happily. It would then reach core-01 as an
    # unpullable image and take the service down on deploy.
    #
    # Neither existing guard catches this: check-image-pullable.sh only matches
    # `image:tag` form, so every digest-pinned image is invisible to it.
    placeholder=$(printf '%s\n' "$refs" \
        | grep -E "${IMAGE}@sha256:(0{64}|f{64}|(0123456789abcdef)+)" || true)
    if [ -n "$placeholder" ]; then
        echo "ERROR: $IMAGE pinned to a placeholder digest in $f:" >&2
        printf '%s\n' "$placeholder" >&2
        echo "" >&2
        echo "This is a bootstrap placeholder, not a real image. Replace it with the" >&2
        echo "digest presidio-analyzer-image-build.yml printed in its step summary" >&2
        echo "after the image was actually published." >&2
        FAIL=1
    fi
done

if [ "$FAIL" -ne 0 ]; then
    cat >&2 <<'EOF'

A tag can be moved; a digest cannot.

Pin the digest presidio-analyzer-image-build.yml printed in its step summary:

    image: ghcr.io/getklai/presidio-analyzer@sha256:<64 hex>

Keep the human-readable tag in a comment next to it if you want to know which
build it is.
EOF
    exit 1
fi

echo "OK: every $IMAGE reference is digest-pinned."
