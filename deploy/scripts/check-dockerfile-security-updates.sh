#!/usr/bin/env bash
# Every image we build and ship must patch its base OS packages.
#
# Why this guard exists: on 2026-08-17 the Trivy gate blocked the retrieval-api
# deploy on CVE-2026-53615 (util-linux, 9 packages, fix available). It was not
# a retrieval-api problem — it came from the shared python:3.12-slim base, so
# every service on that base was one rebuild away from a blocked deploy.
# klai-portal/backend had carried the fix for months; nobody else had it, and
# nothing said so.
#
# Seven Dockerfiles then got the same three lines. Seven copies of anything
# drift (see `url-shape-multi-file-drift` in the process pitfalls), and the
# drift here is invisible until a deploy is already blocked. This turns "did
# somebody remember" into a check.
#
# What it enforces: the FINAL stage of each Dockerfile — the one that produces
# the shipped filesystem, which is what Trivy scans — runs an OS package
# upgrade. Builder stages are irrelevant; their layers do not ship.
#
# Usage:  bash deploy/scripts/check-dockerfile-security-updates.sh [file...]
#         (no arguments = every tracked Dockerfile)

set -euo pipefail

# Images whose base OS we do not own. Adding an apt/apk upgrade to these means
# patching somebody else's image on top of their pinned dependency set, which
# is how you get a build that works until upstream moves. Each line needs a
# reason, not just a path.
is_allowlisted() {
    case "$1" in
        deploy/crawl4ai/Dockerfile)
            # Thin re-tag of unclecode/crawl4ai. Upstream owns the OS layer and
            # pins a Playwright/Chromium combination that an OS upgrade can
            # break; it is scanned WARN-tier, not STRICT.
            return 0 ;;
        deploy/librechat/Dockerfile.klai)
            # Source-level patches on top of ghcr.io/danny-avila/librechat.
            # Same reasoning, plus the image is digest-pinned downstream, so an
            # upgrade here would move a digest a canary can be rolled back to.
            return 0 ;;
        *)
            return 1 ;;
    esac
}

# Resolve the final stage's real base, following `FROM <earlier-stage>` links.
# klai-docs is the reason this is not a one-liner: its runtime stage is
# `FROM base AS runner`, where `base` is defined earlier as node:22-alpine.
resolve_base() {
    local file="$1" target="$2" depth=0 line base alias
    while (( depth++ < 10 )); do
        line=""
        while IFS= read -r candidate; do
            base="$(sed -E 's/^FROM +//; s/ +AS +.*$//I' <<< "$candidate")"
            alias="$(sed -nE 's/^FROM +.* +AS +(.*)$/\1/Ip' <<< "$candidate")"
            if [[ -z "$target" || "$alias" == "$target" ]]; then
                line="$base"
                [[ -z "$target" ]] && break
            fi
        done < <(grep -E '^FROM ' "$file")
        [[ -n "$line" ]] || { printf '%s' "$target"; return; }
        # Another local stage? follow it. Otherwise this is the real base.
        if grep -qiE "^FROM +.* +AS +${line}$" "$file" 2>/dev/null; then
            target="$line"; continue
        fi
        printf '%s' "$line"; return
    done
    printf '%s' "$target"
}

final_stage_body() {
    # Everything after the last FROM — the shipped stage.
    local file="$1" last
    last="$(grep -nE '^FROM ' "$file" | tail -1 | cut -d: -f1)"
    tail -n "+$last" "$file"
}

failures=0
checked=0
skipped=0

files=("$@")
if (( ${#files[@]} == 0 )); then
    mapfile -t files < <(git ls-files | grep -E '(^|/)Dockerfile[^/]*$')
fi

for file in "${files[@]}"; do
    [[ -f "$file" ]] || continue

    if is_allowlisted "$file"; then
        echo "  skip  $file (upstream-owned base, see allow-list)"
        skipped=$((skipped + 1))
        continue
    fi

    final_from="$(grep -E '^FROM ' "$file" | tail -1 | sed -E 's/^FROM +//; s/ +AS +.*$//I')"
    if grep -qiE "^FROM +.* +AS +${final_from}$" "$file" 2>/dev/null; then
        base="$(resolve_base "$file" "$final_from")"
    else
        base="$final_from"
    fi

    body="$(final_stage_body "$file")"

    case "$base" in
        *alpine*)
            want='apk upgrade'
            ;;
        *slim*|*debian*|*ubuntu*|*bookworm*|*trixie*|*bullseye*)
            want='apt-get upgrade'
            ;;
        *)
            echo "  skip  $file (base '$base' is neither Debian- nor Alpine-based)"
            skipped=$((skipped + 1))
            continue
            ;;
    esac

    checked=$((checked + 1))
    if grep -qF "$want" <<< "$body"; then
        echo "  ok    $file (final stage: $base)"
    else
        echo "  FAIL  $file — final stage is '$base' but never runs '$want'" >&2
        failures=$((failures + 1))
    fi
done

echo
echo "checked=$checked  skipped=$skipped  failures=$failures"

if (( failures > 0 )); then
    cat >&2 <<'EOF'

A shipped image is not patching its base OS packages. Trivy gates the deploy on
exactly those findings, so this becomes a blocked deploy the next time that
service builds — usually for somebody who did not touch the Dockerfile.

Add to the FINAL stage (the pattern klai-portal/backend/Dockerfile has used
since before this guard existed):

    # Apply security patches to base image OS packages
    RUN apt-get update && apt-get upgrade -y --no-install-recommends \
        && rm -rf /var/lib/apt/lists/*

Alpine equivalent: `apk update && apk upgrade --no-cache`.

If the base genuinely is not ours to patch, add it to is_allowlisted() with the
reason — not a bare path.
EOF
    exit 1
fi
