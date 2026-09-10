#!/bin/sh
# SPEC-VEXA-003 REQ-U-002 enforcement — no mutable Vexa image tags.
#
# Fails the commit if any vexaai/* image in public deploy manifests points at a
# mutable tag (latest, dev, staging) or a non-pinned form.
#
# Three pinned tag forms are accepted:
#   1. Upstream version:           `<major>.<minor>.<patch>[.<patch>...]`
#                                  (e.g. 0.10.6, 0.10.6.2, or 0.10.6.3.14) — used since
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
# Rules 4-6 extend the same gate to every third-party image: no tag at all
# (Docker reads that as :latest), a mutable tag (latest/dev/staging), or a
# tag that is not a dotted version. They classify the image VALUE, not the
# line, so an inline comment can neither smuggle a bad image through nor
# fail a good one. Exempt: ghcr.io/getklai/*:latest (built by our own CI,
# deliberately mutable — only `latest`, not dev/staging) and `@sha256:`
# digest pins.
#
# Wire into git hooks via .githooks/pre-commit or CI.

set -eu

# The gpu workflow calls this script on gpu-compose changes, so it has to
# actually read that file.
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
    PENDING=$(grep -nE 'vexaai/[a-z0-9-]+:v?[0-9]+(\.[0-9]+){2,}-pending' "$F" || true)
    if [ -n "$PENDING" ]; then
        echo "ERROR: placeholder tag in $F — file is not deploy-ready:" >&2
        echo "$PENDING" >&2
        FAIL=1
    fi

    # Rule 3: any other vexaai/* tag must match upstream version,
    #         locally-built (`<version>-local-YYMMDD-HHMM`), or legacy
    #         timestamped (`<version>-YYMMDD-HHMM`).
    #
    #         The leading `v` is optional because upstream changed convention
    #         mid-flight: the 0.10 line publishes `0.10.6.3.14`, the 0.12 line
    #         publishes ONLY `v0.12.22` (plain `0.12.22` is not on Docker Hub —
    #         verified with `docker manifest inspect`). Accepting `v?` keeps every
    #         other property of this rule intact: still immutable, still
    #         version-shaped, and Rule 1 still rejects latest/dev/staging.
    BAD=$(grep -nE 'vexaai/[a-z0-9-]+:[^[:space:]#]+' "$F" \
          | grep -vE 'vexaai/[a-z0-9-]+:v?[0-9]+(\.[0-9]+){2,}(-(local-)?[0-9]{6}-[0-9]{4})?$' \
          | grep -vE 'vexaai/[a-z0-9-]+:v?[0-9]+(\.[0-9]+){2,}(-(local-)?[0-9]{6}-[0-9]{4})?[[:space:]]' \
          | grep -vE 'vexaai/[a-z0-9-]+:(latest|dev|staging)\b' \
          | grep -vE 'vexaai/[a-z0-9-]+:v?[0-9]+(\.[0-9]+){2,}-pending' \
          || true)
    if [ -n "$BAD" ]; then
        echo "ERROR: non-canonical Vexa image tag in $F:" >&2
        echo "$BAD" >&2
        FAIL=1
    fi

    # Rules 4-6 cover everything that is not vexaai/*. They classify the
    # image VALUE, not the line: a grep over whole lines lets
    # `image: redis:latest # ghcr.io/getklai/x:latest` pass on the strength
    # of its comment, and fails a fully commented-out line that is not an
    # image reference at all.
    IMAGES=$(awk '
        {
            line = $0
            sub(/\r$/, "", line)
            if (line ~ /^[[:space:]]*#/) next
            if (line !~ /^[[:space:]]*image:[[:space:]]*/) next
            sub(/^[[:space:]]*image:[[:space:]]*/, "", line)
            sub(/[[:space:]]+#.*$/, "", line)
            gsub(/^["\x27]|["\x27]$/, "", line)
            sub(/[[:space:]]+$/, "", line)
            if (line == "") next
            print NR ":" line
        }
    ' "$F")

    BAD3P=""
    for ENTRY in $IMAGES; do
        LINE=${ENTRY%%:*}
        REF=${ENTRY#*:}

        # vexaai/* has its own, stricter rules above.
        case "$REF" in vexaai/*) continue ;; esac

        # A digest is the strongest pin there is.
        case "$REF" in *@sha256:*) continue ;; esac

        # Rule 4: no tag at all. Docker reads that as `:latest`.
        REPO=${REF%:*}
        TAG=${REF##*:}
        if [ "$REF" = "$REPO" ] || [ -z "$TAG" ]; then
            BAD3P="$BAD3P$LINE: $REF (no tag — Docker reads this as :latest)
"
            continue
        fi

        # Rule 5: our own CI builds ghcr.io/getklai/*:latest and that is
        # deliberate. Only `latest`; `dev` and `staging` are not exempt.
        [ "$TAG" = "latest" ] && case "$REPO" in ghcr.io/getklai/*) continue ;; esac

        case "$TAG" in
            latest|dev|staging)
                BAD3P="$BAD3P$LINE: $REF (mutable tag)
"
                continue
                ;;
        esac

        # Rule 6: every other tag must be a dotted version, optionally with a
        # dash-separated variant (3.13.7-alpine, 0.8.6-pg18, 2026.8.12-cdfdaa5a8,
        # 0.9.2-local-260813-1211). Anchored at both ends, and the variant must
        # start with a dash: allowing a dot there let `1.2.3junk` through on
        # its prefix. An all-numeric fourth segment (1.8.1.3) is still a
        # version, so `(\.[0-9]+)+` keeps repeating.
        if ! printf '%s' "$TAG" | grep -qE '^v?[0-9]+(\.[0-9]+)+(-[A-Za-z0-9._-]+)?$'; then
            BAD3P="$BAD3P$LINE: $REF (not a pinned version)
"
        fi
    done

    if [ -n "$BAD3P" ]; then
        echo "ERROR: unpinned third-party image in $F:" >&2
        printf '%s' "$BAD3P" >&2
        FAIL=1
    fi
done

if [ "$FAIL" -eq 0 ]; then
    echo "OK: all Vexa image tags are pinned (upstream version, -local-YYMMDD-HHMM, or legacy timestamped) and all third-party image tags are versioned or digest-pinned."
    exit 0
fi

echo "" >&2
echo "Fix: update tags to one of:" >&2
echo "     vexaai/<svc>:<version> or :v<version>        (Docker Hub)" >&2
echo "     vexaai/<svc>:<version>-local-YYMMDD-HHMM     (locally built)" >&2
echo "     vexaai/<svc>:<version>-YYMMDD-HHMM           (legacy locally built)" >&2
echo "     third-party: a dotted version (e.g. 3.13.7-alpine) or an @sha256: digest" >&2
exit 1
