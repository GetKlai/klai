#!/bin/sh
# Forcing function — fail loud at commit time when a vexaai/* image
# referenced in public compose files is not actually pullable.
#
# Why this exists: a public deploy compose tag that cannot be pulled will
# only fail at deploy time unless we verify registry manifests first.
#
# This script catches that class of bug at commit time.
#
# Behaviour:
#   1. For every vexaai/* image reference in public deploy compose files,
#      run `docker manifest inspect` against the public registry.
#   2. If the manifest exists → OK (image is pullable).
#   3. If the manifest is missing AND the tag matches a known locally-
#      built convention → OK (with INFO note).
#   4. If the manifest is missing AND the tag does not match a locally-
#      built convention → FAIL (the most likely cause is a typo or a
#      reference to an upstream tag that was never published).
#
# Locally-built tag conventions accepted without manifest:
#   - `<semver>-local-YYMMDD-HHMM`  (preferred, since 2026-05-03)
#   - `<semver>-YYMMDD-HHMM`        (legacy, SPEC-VEXA-003 build pattern)
#
# Wire into git pre-commit and into the deploy-compose CI workflow.

set -eu

FILES="deploy/docker-compose.yml"
FAIL=0
PUBLIC_OK=0
LOCAL_OK=0

for F in $FILES; do
    [ -f "$F" ] || continue

    # Extract every `vexaai/<svc>:<tag>` reference (both `image:` lines
    # and env vars like `BOT_IMAGE_NAME` / `BROWSER_IMAGE`).
    REFS=$(grep -oE 'vexaai/[a-z0-9-]+:[A-Za-z0-9._-]+' "$F" | sort -u || true)

    [ -z "$REFS" ] && continue

    for REF in $REFS; do
        # Allow the explicit "mid-migration placeholder" that
        # check-image-tags.sh already rejects with its own message.
        case "$REF" in
            *":"*"-pending"|*":latest"|*":dev"|*":staging")
                continue  # check-image-tags.sh owns this category
                ;;
        esac

        # `docker manifest inspect` is anonymous-friendly for public
        # repos and exits non-zero with stderr if the manifest is
        # missing. Suppress stdout and stderr; only the exit code
        # matters here.
        if docker manifest inspect "$REF" >/dev/null 2>&1; then
            PUBLIC_OK=$((PUBLIC_OK + 1))
            continue
        fi

        # Manifest missing — accept only locally-built conventions.
        TAG=${REF#*:}
        case "$TAG" in
            *.*.*-local-[0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9])
                # `<semver>-local-YYMMDD-HHMM`
                echo "INFO: $REF — locally-built (new convention), not on registry."
                LOCAL_OK=$((LOCAL_OK + 1))
                ;;
            *.*.*-[0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9])
                # `<semver>-YYMMDD-HHMM` (legacy)
                echo "INFO: $REF — locally-built (legacy convention), not on registry."
                LOCAL_OK=$((LOCAL_OK + 1))
                ;;
            *)
                echo "ERROR: $REF in $F — manifest not pullable from registry," >&2
                echo "       and tag does not match a locally-built convention." >&2
                echo "       This is what bit us in PR #269. Either:" >&2
                echo "         - fix the tag to one that exists on Docker Hub, OR" >&2
                echo "         - rename to <semver>-local-YYMMDD-HHMM if locally built." >&2
                FAIL=1
                ;;
        esac
    done
done

if [ "$FAIL" -eq 0 ]; then
    echo "OK: $PUBLIC_OK public manifest(s) verified, $LOCAL_OK locally-built ref(s) accepted."
    exit 0
fi

echo "" >&2
echo "Hint: run \`docker manifest inspect <ref>\` locally to reproduce." >&2
exit 1
