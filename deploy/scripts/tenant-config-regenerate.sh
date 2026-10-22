#!/usr/bin/env bash
# deploy/scripts/tenant-config-regenerate.sh
#
# Regenerate per-tenant LibreChat configs from the updated base template.
# portal-api exposes an internal endpoint that re-runs _generate_librechat_yaml
# for each tenant, writes the result, flushes Redis, and restarts containers.
# Push-triggered config/catalog/patch changes use restart-only. Full recreate is
# reserved for explicit workflow_dispatch image rollouts.
#
# Usage: RECREATE_CONTAINERS=true|false tenant-config-regenerate.sh
#
# deploy-librechat-config.yml runs this from its own sparse checkout, so the
# script and the base config it just synced always come from the same commit.
#
# Tests: deploy/scripts/tests/tenant-config-regenerate.test.sh

set -euo pipefail

# Seams for the test-suite; production keeps every default.
KLAI_DIR="${KLAI_COMPOSE_DIR:-/opt/klai}"
DOCKER="${KLAI_DOCKER_BIN:-docker}"
AUTHZ_WAIT_ATTEMPTS="${KLAI_AUTHZ_WAIT_ATTEMPTS:-24}"
AUTHZ_WAIT_INTERVAL="${KLAI_AUTHZ_WAIT_INTERVAL:-5}"
RECREATE_CONTAINERS="${RECREATE_CONTAINERS:-false}"

# docker compose calls getwd() and crashes if cwd no longer exists, which is
# where a caller that just removed its checkout would leave us.
cd "$KLAI_DIR"

# The same host lock compose-up.sh takes: the regenerate recreates tenant
# containers through klai-docker-authz, and must not run while another deploy
# recreates that proxy (two workflows recreated it in the same second on
# 21 October 2026). Taken before the health poll below, so the proxy cannot be
# recreated between "it answers" and the regenerate. Without flock the
# regenerate would run unlocked, so a missing binary is an error, not a skip.
# The test suite puts a stub flock on PATH.
LOCK_FILE="${KLAI_CONTAINER_LOCK:-$KLAI_DIR/.container-deploy.lock}"
if ! command -v flock >/dev/null 2>&1; then
    echo "ERROR: flock not found; refusing to regenerate without the host lock $LOCK_FILE" >&2
    exit 1
fi
exec 9>"$LOCK_FILE"
if ! flock -w "${KLAI_LOCK_WAIT_SECONDS:-900}" 9; then
    echo "ERROR: another container deploy has held $LOCK_FILE for 15 minutes; not regenerating" >&2
    exit 1
fi

# portal-api has no host port mapping — it only listens on portal-api:8010
# inside the klai-net Docker network. We therefore run the request *inside*
# the portal-api container itself, where localhost:8010 is the API. We use
# Python's httpx (already installed) instead of curl because the python:slim
# base image does not ship curl. The INTERNAL_SECRET env var is already set
# inside the container from .env.
portal_api_python() {
    "$DOCKER" compose -f "$KLAI_DIR/docker-compose.yml" exec -T "$@"
}

# The regenerate endpoint recreates containers through klai-docker-authz. The
# host lock above keeps the proxy's own deploy from running at the same time;
# this poll covers a proxy that is restarting for any other reason.
authz_ready=no
for (( attempt = 1; attempt <= AUTHZ_WAIT_ATTEMPTS; attempt++ )); do
    if portal_api_python portal-api python -c '
import sys

import httpx

sys.exit(0 if httpx.get("http://klai-docker-authz:2375/health", timeout=5.0).status_code == 200 else 1)
' 2>/dev/null; then
        authz_ready=yes
        break
    fi
    echo "klai-docker-authz not answering yet (attempt $attempt of $AUTHZ_WAIT_ATTEMPTS)"
    if (( attempt < AUTHZ_WAIT_ATTEMPTS )); then sleep "$AUTHZ_WAIT_INTERVAL"; fi
done
if [[ "$authz_ready" != yes ]]; then
    # Regenerating now would write every tenant's config and then fail each
    # container action; better to fail before writing.
    echo "ERROR: klai-docker-authz did not answer within $(( AUTHZ_WAIT_ATTEMPTS * AUTHZ_WAIT_INTERVAL )) seconds; not regenerating." >&2
    exit 1
fi

# Finding 3D (adversarial review 2026-08-13): a bare "HTTP 200 = success"
# check is not enough -- the endpoint can report per-tenant failures in its
# errors array on a 200 (restart-only mode keeps that contract on purpose,
# see finding 3E). The python below is belt and braces: it fails on any
# non-200 status AND, independently, fails whenever the errors array is
# non-empty regardless of status code. A non-zero exit from it is this
# script's exit (set -e), and every printed line, the errors list included,
# is already in the output.
echo "Triggering tenant config regeneration via portal-api..."
portal_api_python -e RECREATE_CONTAINERS="$RECREATE_CONTAINERS" portal-api python -c '
import json
import os
import sys

import httpx

secret = os.environ["INTERNAL_SECRET"]
url = "http://localhost:8010/internal/librechat/regenerate"
if os.environ.get("RECREATE_CONTAINERS") == "true":
    url += "?recreate_containers=true"

try:
    r = httpx.post(
        url,
        headers={
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
        },
        timeout=600.0,
    )
except Exception as exc:
    print(f"ERROR: request to {url} failed: {exc}", file=sys.stderr)
    sys.exit(1)

print(f"HTTP {r.status_code}")
try:
    body = r.json()
except (json.JSONDecodeError, ValueError):
    print(r.text)
    print("ERROR: response body was not valid JSON", file=sys.stderr)
    sys.exit(1)

updated = body.get("tenants_updated") or []
skipped = body.get("tenants_skipped") or []
errors = body.get("errors") or []
compose_managed = body.get("compose_managed_skipped") or []
print(f"tenants_updated ({len(updated)}): {updated}")
print(f"tenants_skipped ({len(skipped)}): {skipped}")
if compose_managed:
    # Incident 2026-08-14: portal-api must not take over a container
    # that docker compose owns, so these were restarted, not
    # recreated. A restart does NOT change the image -- an image
    # rollout for these tenants lands via deploy-compose instead.
    print(
        f"compose_managed_skipped ({len(compose_managed)}): {compose_managed} "
        "-- restarted, NOT recreated; image rollout belongs to deploy-compose"
    )
if errors:
    print(f"errors ({len(errors)}):")
    for e in errors:
        print(f"  - {e}")

if r.status_code != 200:
    print(f"ERROR: regenerate endpoint returned HTTP {r.status_code}", file=sys.stderr)
    sys.exit(1)
if errors:
    # Belt and braces: some paths can report errors on a 200.
    print("ERROR: regenerate endpoint reported a non-empty errors array on HTTP 200", file=sys.stderr)
    sys.exit(1)

print("Tenant configs regenerated successfully")
'
