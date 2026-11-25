#!/bin/sh
# Scan one third-party image for the triage in scripts/triage-image-findings.py.
#
# Usage: scripts/trivy-scan-image.sh <image> <output.json> [remote|docker]
#
# The image source defaults to `remote` (pull from the registry). `docker`
# scans an image in the local Docker daemon: the crawler image is built from
# deploy/crawl4ai in the job because it is built on the host and never pushed.
#
# JSON, not SARIF: the triage needs the package inventory (--list-all-pkgs) to
# tell a stale SBOM inside the image from the packages it really installed, and
# `trivy convert --format sarif` turns the triaged JSON into the same SARIF a
# direct SARIF scan produces (checked on 2026-11-24 against a 312-result scan:
# identical rule ids, locations and messages), so existing alerts keep matching.
#
# Severity filter as the WARN tier of .trivy.yaml, but WITHOUT --ignore-unfixed:
# a known-exploited finding matters most when there is no fix, so the triage
# sees unfixed findings and keeps them when they are in KEV or cross the EPSS
# threshold on an image that is not internal. One retry, because
# registry pulls fail transiently; a second failure fails the job and leaves
# that image's alerts as they were rather than closing them.
set -eu

image=$1
out=$2
src=${3:-remote}

for attempt in 1 2; do
    if trivy image \
        --format json \
        --list-all-pkgs \
        --output "$out" \
        --severity CRITICAL,HIGH \
        --scanners vuln \
        --image-src "$src" \
        --timeout 6m \
        --skip-version-check \
        --exit-code 0 \
        "$image"; then
        exit 0
    fi
    if [ "$attempt" -eq 2 ]; then
        exit 1
    fi
    echo "Trivy scan of $image failed on attempt $attempt; retrying after 30s..."
    sleep 30
done
