#!/usr/bin/env bash
# Tests for check-dockerfile-security-updates.sh.
#
# The guard takes file arguments, so every case is a fixture Dockerfile in a
# temp dir — no Docker, no network.
#
# Run: bash deploy/scripts/tests/check-dockerfile-security-updates.test.sh

set -euo pipefail

repo_root=$(CDPATH='' cd -- "$(dirname -- "$0")/../../.." && pwd)
guard="$repo_root/deploy/scripts/check-dockerfile-security-updates.sh"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

failures=0

run_guard() {
    set +e
    ( cd "$repo_root" && bash "$guard" "$@" ) > "$work/out" 2>&1
    rc=$?
    set -e
}

check() {
    if [[ "$2" == "$3" ]]; then
        echo "  ok   $1"
    else
        echo "  FAIL $1 (expected exit $2, got $3)"
        sed 's/^/       /' "$work/out"
        failures=$((failures + 1))
    fi
}

fixture() {
    local name="$1"; shift
    printf '%s\n' "$@" > "$work/$name"
    printf '%s' "$work/$name"
}

echo "check-dockerfile-security-updates.sh"

f=$(fixture Dockerfile.debian_ok \
    'FROM python:3.12-slim' \
    'RUN apt-get update && apt-get upgrade -y --no-install-recommends \' \
    '    && rm -rf /var/lib/apt/lists/*' \
    'COPY app app')
run_guard "$f"; check "debian base with the upgrade -> pass" 0 "$rc"

f=$(fixture Dockerfile.debian_missing \
    'FROM python:3.12-slim' \
    'RUN apt-get update && apt-get install -y curl' \
    'COPY app app')
run_guard "$f"; check "debian base without the upgrade -> fail" 1 "$rc"

for base in node:22 python:3.12 postgres:16 golang:1.25; do
    f=$(fixture "Dockerfile.plain-${base%%:*}" \
        "FROM $base" \
        'RUN echo shipped-without-security-updates')
    run_guard "$f"; check "plain Debian-family base $base without apt upgrade -> fail" 1 "$rc"
done

f=$(fixture Dockerfile.plain_debian_ok \
    'FROM node:22' \
    'RUN apt-get update && apt-get upgrade -y --no-install-recommends')
run_guard "$f"; check "plain Debian-family base with apt upgrade -> pass" 0 "$rc"

f=$(fixture Dockerfile.alpine_ok \
    'FROM node:22-alpine' \
    'RUN apk update && apk upgrade --no-cache')
run_guard "$f"; check "alpine base with apk upgrade -> pass" 0 "$rc"

f=$(fixture Dockerfile.alpine_missing \
    'FROM node:22-alpine' \
    'RUN apk add --no-cache tini')
run_guard "$f"; check "alpine base without apk upgrade -> fail" 1 "$rc"

# The mistake worth guarding hardest: patching a stage whose layers never ship.
# Trivy scans the final filesystem, so an upgrade in the builder buys nothing
# while looking exactly like a fix in review.
f=$(fixture Dockerfile.builder_only \
    'FROM python:3.12-slim AS builder' \
    'RUN apt-get update && apt-get upgrade -y' \
    'RUN pip install .' \
    '' \
    'FROM python:3.12-slim' \
    'COPY --from=builder /usr/local /usr/local')
run_guard "$f"; check "upgrade only in the builder stage -> fail" 1 "$rc"

f=$(fixture Dockerfile.builder_and_final \
    'FROM python:3.12-slim AS builder' \
    'RUN pip install .' \
    '' \
    'FROM python:3.12-slim' \
    'RUN apt-get update && apt-get upgrade -y --no-install-recommends' \
    'COPY --from=builder /usr/local /usr/local')
run_guard "$f"; check "upgrade in the final stage of a multi-stage -> pass" 0 "$rc"

# klai-docs is why base resolution exists: its runtime stage inherits from a
# named local stage, so the literal FROM line says nothing about the OS.
f=$(fixture Dockerfile.named_stage \
    'FROM node:22-alpine AS base' \
    'FROM base AS deps' \
    'RUN npm ci' \
    'FROM base AS runner' \
    'RUN apk update && apk upgrade --no-cache')
run_guard "$f"; check "final stage inherits a named stage -> resolves the base" 0 "$rc"

f=$(fixture Dockerfile.named_stage_missing \
    'FROM node:22-alpine AS base' \
    'FROM base AS runner' \
    'COPY dist dist')
run_guard "$f"; check "named-stage inheritance without the upgrade -> fail" 1 "$rc"

# Nothing to patch, and no package manager to do it with.
f=$(fixture Dockerfile.scratch \
    'FROM scratch' \
    'COPY server /server')
run_guard "$f"; check "scratch base -> skipped, not failed" 0 "$rc"
if grep -q "skip" "$work/out"; then
    echo "  ok     says it skipped rather than passing silently"
else
    echo "  FAIL   skipped without saying so"
    failures=$((failures + 1))
fi

f=$(fixture Dockerfile.unknown \
    'FROM vendor/custom-runtime:1.0' \
    'RUN echo unknown-package-manager')
run_guard "$f"; check "unclassifiable base -> fail loudly" 1 "$rc"
if grep -q "cannot classify" "$work/out"; then
    echo "  ok     explains that the base needs classification or an allow-list entry"
else
    echo "  FAIL   unclassifiable base failed without an actionable reason"
    failures=$((failures + 1))
fi

# The allow-list is keyed on real repo paths, so this case uses one.
run_guard deploy/crawl4ai/Dockerfile
check "allow-listed upstream image -> skipped" 0 "$rc"
if grep -q "allow-list" "$work/out"; then
    echo "  ok     points at the allow-list for the reason"
else
    echo "  FAIL   skipped an allow-listed file without saying why"
    failures=$((failures + 1))
fi

# The whole repo has to be clean, or the guard is decoration.
run_guard
check "every tracked Dockerfile in the repo -> pass" 0 "$rc"

echo
if [[ "$failures" -eq 0 ]]; then
    echo "check-dockerfile-security-updates: all cases passed"
else
    echo "check-dockerfile-security-updates: $failures case(s) failed"
    exit 1
fi
