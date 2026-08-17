#!/usr/bin/env bash
# SPEC-INFRA-CONTAINER-HYGIENE-001 REQ-5 — notice edits made outside the pipeline.
#
# /opt/klai/docker-compose.yml is written by deploy-compose.yml from the repo, and
# nothing in that pipeline creates a sibling file. So a docker-compose.yml.bak,
# .orig, .pre-victorialogs or .before-cadvisor-flags is the fingerprint of someone
# editing the deployed file by hand — which the repo forbids as CRIT.
#
# On 2026-08-17 there were 26 of them, April through May, 1.7 MB. Nothing was
# lost: none was mounted, and every service in them also existed somewhere in the
# 6773 commits touching deploy/docker-compose.yml. But each one marked an edit
# made outside the pipeline, and the pile grew for four months with nothing
# watching it. The audit is the only layer that sees a human on the server.
#
# The risk in a detection like this is the opposite of the last one: it must not
# fire on the compose files the deploy legitimately places next to each other.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
AUDIT="$REPO_ROOT/deploy/scripts/docker-orphan-audit.sh"
FAIL=0

run_with() {
    # $@ = filenames to create next to the compose file
    local tmp; tmp="$(mktemp -d)"
    printf 'services:\n  portal-api:\n' > "$tmp/docker-compose.yml"
    for f in "$@"; do printf 'services:\n' > "$tmp/$f"; done

    cat > "$tmp/docker" <<'STUB'
#!/usr/bin/env bash
case "$1" in
  ps)
    for a in "$@"; do [[ "$a" == "-a" ]] && exit 0; done
    case "$*" in
      *'{{.Names}}'*) echo "klai-core-portal-api-1" ;;
      *com.docker.compose.service*) echo "portal-api" ;;
      *) : ;;
    esac
    ;;
  inspect)
    case "$4" in
      *com.docker.compose.project*) echo "klai-core" ;;
      *com.docker.compose.service*) echo "portal-api" ;;
      *) : ;;
    esac
    ;;
  *) : ;;
esac
exit 0
STUB
    chmod +x "$tmp/docker"

    PATH="$tmp:$PATH" COMPOSE_FILE="$tmp/docker-compose.yml" \
        OVERRIDE_FILE="$tmp/none.yml" DEV_FILE="$tmp/none.yml" \
        CADDYFILE="$tmp/no-caddyfile" \
        bash "$AUDIT" 2>/dev/null
    rm -rf "$tmp"
}

flagged() { echo "$1" | grep -c "\"event\":\"compose_hand_edit_artifact\"" | tr -d ' '; }

expect_count() {
    want="$1"; got="$2"; why="$3"
    if [ "$got" = "$want" ]; then echo "OK:   $got flagged — $why"
    else echo "FAIL: expected $want flagged, got $got — $why" >&2; FAIL=1; fi
}

echo "── compose hand-edit detection ──"

OUT=$(run_with)
expect_count 0 "$(flagged "$OUT")" "a clean deploy leaves no siblings"

# The exact names found on core-01.
OUT=$(run_with docker-compose.yml.bak docker-compose.yml.orig \
               docker-compose.yml.pre-victorialogs.bak \
               docker-compose.yml.before-cadvisor-flags-20260606141107)
expect_count 4 "$(flagged "$OUT")" "every real leftover shape is caught"

# Must NOT fire on legitimate compose files that share the directory. These are
# separate files, not siblings of docker-compose.yml, and the deploy places them
# there on purpose.
OUT=$(run_with docker-compose.override.yml docker-compose.dev.yml)
expect_count 0 "$(flagged "$OUT")" "override/dev compose files are not hand-edit marks"

if echo "$OUT" | grep -q '"event":"audit_run_completed"'; then
    echo "OK:   audit ran to completion"
else
    echo "FAIL: audit aborted before its final event" >&2; FAIL=1
fi

echo "─────────────────────────────────"
if [ "$FAIL" -eq 0 ]; then
    echo "compose hand-edit guard: OK"
else
    echo "compose hand-edit guard: FAILED" >&2
fi
exit "$FAIL"
