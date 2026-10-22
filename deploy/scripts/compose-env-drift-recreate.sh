#!/usr/bin/env bash
# /opt/klai/scripts/compose-env-drift-recreate.sh
#
# The env-drift sweep at the end of deploy-compose.yml: recreate every compose
# service whose declaration changed (litellm excepted), verify the ones that were
# recreated, and re-apply the host firewall afterwards, on the failure path too.
#
# Installed by deploy-compose.yml's deploy/scripts loop and called from the same
# step, so it and the compose-up.sh beside it are the versions from the commit
# being deployed.
#
# Tests: deploy/scripts/tests/compose-env-drift-recreate.test.sh

set -euo pipefail

# Seams for the test-suite; production keeps every default.
KLAI_DIR="${KLAI_COMPOSE_DIR:-/opt/klai}"
DOCKER="${KLAI_DOCKER_BIN:-docker}"

# SPEC-MCP-RETRIEVAL-001 follow-up — auto-recreate on env-only changes.
#
# Without this, an env-var change in docker-compose.yml lands on
# disk but never reaches the running container — only discovered
# via a 401 in production (PR #493 was exactly this trap).
#
# ``compose up -d`` with the service list is idempotent: Compose
# recreates only services whose declaration changed, leaves the
# rest untouched. LiteLLM is intentionally excluded here: its
# docker-compose.yml changes trigger litellm-hook-deploy.yml,
# which recreates it through /opt/klai/scripts/compose-up.sh
# litellm so the Prisma migration baseline gate runs before a
# serving-container recreate.
echo "::group::SPEC-MCP-RETRIEVAL-001 follow-up — compose up -d for env-drift recreate"
cd "$KLAI_DIR"
COMPOSE_ENV_DRIFT_SERVICES="$("$DOCKER" compose config --services | awk '$0 != "litellm"' | tr '\n' ' ')"
if [ -z "$COMPOSE_ENV_DRIFT_SERVICES" ]; then
  echo "::error::no env-drift services resolved; refusing an all-services compose up"
  exit 1
fi

# Docker can recreate a network before a later container fails. Arm
# the EXIT trap only after config/list validation, then clear it once
# compose succeeds so both success and failure paths re-apply exactly
# once. The failure handler preserves compose's original exit code.
reapply_firewall() {
  echo "Re-applying firewall rules (network definitions may have changed) ..."
  if ! sudo systemctl restart klai-harden-firewall.service; then
    echo "::error::klai-harden-firewall failed to restart"
    return 1
  fi
  systemctl is-active --quiet klai-harden-firewall.service \
    || { echo "::error::klai-harden-firewall failed to re-apply"; return 1; }
}
on_compose_exit() {
  local compose_exit="$1"
  trap - EXIT
  reapply_firewall || exit 1
  exit "$compose_exit"
}
trap 'on_compose_exit "$?"' EXIT

# Through compose-up.sh rather than a bare `docker compose up -d`.
# compose returns once containers are CREATED, so the bare call
# reported green while a recreated container could be dead on boot,
# crash-looping, or left under a hash-prefixed name by a
# half-finished recreate. On 2026-09-18 this sweep recreated 31
# services in one merge, Caddy and the fail-closed PII analyzer
# among them, with nothing checking any of them came back.
#
# compose-up.sh still issues one `up -d` for the whole list, so
# depends_on ordering is unchanged; it then verifies each container
# that was actually recreated, in parallel, and fails the step if
# any did not come up. --no-pull keeps this sweep's behaviour: it
# never pulled, and pulling every image on every compose change
# would be slow and a new way to fail. compose-up.sh is installed by
# the same deploy/scripts loop as this script, so it is the version
# from this same commit.
#
# No `| tail` here: it hid the start of compose's output, and the
# per-service verdict is exactly what someone reading a red run needs.
# shellcheck disable=SC2086
"$KLAI_DIR/scripts/compose-up.sh" --no-pull $COMPOSE_ENV_DRIFT_SERVICES
trap - EXIT
echo "::endgroup::"

# Re-apply the firewall after compose has settled.
#
# klai-harden-firewall.service is After=docker.service / WantedBy=
# multi-user.target — it runs at boot and never again. The bot
# isolation rules (SPEC-SEC-022 REQ-2) carry the vexa12-bots subnet,
# read from Docker at the time they were installed. A compose change
# that recreates that network can hand it a different subnet, and the
# rules then match nothing at all: bots regain the host until the next
# reboot, silently.
#
# That is not hypothetical — recreating the network on 2026-08-17 is
# what surfaced this, and the rules had to be re-applied by hand.
# A deploy is exactly when the network can change, so a deploy is when
# the rules get rewritten.
reapply_firewall
