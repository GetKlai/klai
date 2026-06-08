#!/bin/sh
# SPEC-VEXA-003 REQ-U-002 enforcement — no mutable Vexa image tags.
#
# Fails the commit if any vexaai/* image in public deploy manifests points at a
# mutable tag (latest, dev, staging) or a non-pinned form.
#
# Three pinned tag forms are accepted:
#   1. Upstream version:           `<major>.<minor>.<patch>[.<patch>]`
#                                  (e.g. 0.10.6 or 0.10.6.2) — used since
#                                  v0.10.4, when upstream started publishing
#                                  pre-built images to Docker Hub.
#   2. Locally-built (new):        `<version>-local-YYMMDD-HHMM`
#                                  — for images we build on-host that
#                                    upstream does not publish (e.g.
#                                    transcription-service CUDA build).
#   3. Locally-built (legacy):     `<version>-YYMMDD-HHMM`
#                                  — pre-v0.10.4 SPEC-VEXA-003 convention,
#                                    retained for rollback to old images.
#
# Placeholder `<semver>-pending` tags are also rejected — they indicate
# a compose file is mid-migration and not deploy-ready.
#
# Wire into git hooks via .githooks/pre-commit or CI.

set -eu

FILES="deploy/docker-compose.yml"
FAIL=0

for F in $FILES; do
    [ -f "$F" ] || continue

    # Rule 1: mutable tags are always wrong in production compose files.
    MUTABLE=$(grep -nE 'vexaai/[a-z0-9-]+:(latest|dev|staging)\b' "$F" || true)
    if [ -n "$MUTABLE" ]; then
        echo "ERROR: mutable Vexa image tag in $F (REQ-U-002 violation):" >&2
        echo "$MUTABLE" >&2
        FAIL=1
    fi

    # Rule 2: placeholder `pending` tags indicate the file is mid-migration.
    PENDING=$(grep -nE 'vexaai/[a-z0-9-]+:[0-9]+(\.[0-9]+){2,3}-pending' "$F" || true)
    if [ -n "$PENDING" ]; then
        echo "ERROR: placeholder tag in $F — file is not deploy-ready:" >&2
        echo "$PENDING" >&2
        FAIL=1
    fi

    # Rule 3: any other vexaai/* tag must match upstream version,
    #         locally-built (`<version>-local-YYMMDD-HHMM`), or legacy
    #         timestamped (`<version>-YYMMDD-HHMM`).
    BAD=$(grep -nE 'vexaai/[a-z0-9-]+:[^[:space:]#]+' "$F" \
          | grep -vE 'vexaai/[a-z0-9-]+:[0-9]+(\.[0-9]+){2,3}(-(local-)?[0-9]{6}-[0-9]{4})?$' \
          | grep -vE 'vexaai/[a-z0-9-]+:[0-9]+(\.[0-9]+){2,3}(-(local-)?[0-9]{6}-[0-9]{4})?[[:space:]]' \
          | grep -vE 'vexaai/[a-z0-9-]+:(latest|dev|staging)\b' \
          | grep -vE 'vexaai/[a-z0-9-]+:[0-9]+(\.[0-9]+){2,3}-pending' \
          || true)
    if [ -n "$BAD" ]; then
        echo "ERROR: non-canonical Vexa image tag in $F:" >&2
        echo "$BAD" >&2
        FAIL=1
    fi
done

if [ "$FAIL" -eq 0 ]; then
    echo "OK: all Vexa image tags are pinned (upstream version, -local-YYMMDD-HHMM, or legacy timestamped)."
    exit 0
fi

echo "" >&2
echo "Fix: update tags to one of:" >&2
echo "     vexaai/<svc>:<version>                       (Docker Hub)" >&2
echo "     vexaai/<svc>:<version>-local-YYMMDD-HHMM     (locally built)" >&2
echo "     vexaai/<svc>:<version>-YYMMDD-HHMM           (legacy locally built)" >&2
exit 1
