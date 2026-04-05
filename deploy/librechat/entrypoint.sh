#!/bin/sh
# SPEC-KB-015: Apply Klai feedback forwarding patch to LibreChat messages.js at startup.
# This is safer than a full-file mount -- only our diff is applied, upstream changes survive.
#
# If the patch fails after a LibreChat upgrade:
#   1. Extract original: docker run --rm <image> cat /app/api/server/routes/messages.js > upstream.js
#   2. Manually re-apply the SPEC-KB-015 block (see feedback.patch for the exact insertion point)
#   3. Regenerate: diff -u upstream.js patched.js > deploy/librechat/patches/feedback.patch
#   4. Adjust the @@ hunk header line numbers if the context shifted

set -e

TARGET=/app/api/server/routes/messages.js
PATCH=/klai-patches/feedback.patch

echo "[klai-entrypoint] Checking SPEC-KB-015 feedback patch..."

if grep -q "SPEC-KB-015" "$TARGET" 2>/dev/null; then
    echo "[klai-entrypoint] Patch already applied (SPEC-KB-015 marker found), skipping."
elif patch --dry-run "$TARGET" < "$PATCH" > /dev/null 2>&1; then
    patch "$TARGET" < "$PATCH"
    echo "[klai-entrypoint] Patch applied successfully."
else
    echo "[klai-entrypoint] WARNING: Patch failed. LibreChat was probably upgraded."
    echo "[klai-entrypoint] See deploy/librechat/patches/feedback.patch for re-apply instructions."
    echo "[klai-entrypoint] Starting LibreChat WITHOUT kb-feedback forwarding (SPEC-KB-015 inactive)."
fi

# Hand off to the original LibreChat start command
exec node /app/api/server/index.js
