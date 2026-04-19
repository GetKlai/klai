#!/bin/sh
# SPEC-VEXA-003 REQ-U-002 enforcement — no mutable Vexa image tags.
#
# Fails the commit if any vexaai/* image in deploy manifests points at a
# mutable tag (latest, dev, staging) or a non-timestamp form. The immutable
# pattern is `<semver>-YYMMDD-HHMM` per upstream build convention.
#
# Placeholder `0.10.0-pending` tags are also rejected — compose files are
# NOT deploy-ready until Phase 6 writes real timestamps to them.
#
# Wire into git hooks via .githooks/pre-commit or CI.

set -eu

FILES="deploy/docker-compose.yml deploy/docker-compose.gpu.yml"
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
    PENDING=$(grep -nE 'vexaai/[a-z0-9-]+:[0-9]+\.[0-9]+\.[0-9]+-pending' "$F" || true)
    if [ -n "$PENDING" ]; then
        echo "ERROR: placeholder tag in $F — Phase 6 has not pinned real timestamps:" >&2
        echo "$PENDING" >&2
        FAIL=1
    fi

    # Rule 3: any other vexaai/* tag must match `<semver>-YYMMDD-HHMM`.
    BAD=$(grep -nE 'vexaai/[a-z0-9-]+:[^[:space:]#]+' "$F" \
          | grep -vE 'vexaai/[a-z0-9-]+:[0-9]+\.[0-9]+\.[0-9]+-[0-9]{6}-[0-9]{4}' \
          | grep -vE 'vexaai/[a-z0-9-]+:(latest|dev|staging)\b' \
          | grep -vE 'vexaai/[a-z0-9-]+:[0-9]+\.[0-9]+\.[0-9]+-pending' \
          || true)
    if [ -n "$BAD" ]; then
        echo "ERROR: non-canonical Vexa image tag in $F:" >&2
        echo "$BAD" >&2
        FAIL=1
    fi
done

if [ "$FAIL" -eq 0 ]; then
    echo "OK: all Vexa image tags are pinned to an immutable <semver>-YYMMDD-HHMM timestamp."
    exit 0
fi

echo "" >&2
echo "Fix: update tags to the form vexaai/<svc>:<semver>-YYMMDD-HHMM." >&2
echo "Phase 6 of SPEC-VEXA-003 writes these after 'make build' on core-01." >&2
exit 1
