"""Guards for stock LiteLLM container bind mounts.

The LiteLLM service runs the upstream image with explicit single-file mounts.
Local tests put ``deploy/litellm`` on ``sys.path``, so a newly extracted helper
can import locally while the production container crashloops unless the helper
is also mounted into ``/app``.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
LITELLM_DIR = REPO_ROOT / "deploy" / "litellm"
COMPOSE_FILE = REPO_ROOT / "deploy" / "docker-compose.yml"
COMPOSE_UP_SCRIPT = REPO_ROOT / "deploy" / "scripts" / "compose-up.sh"
DEPLOY_COMPOSE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "deploy-compose.yml"
# The env-drift sweep moved out of that workflow into a host script (22 October 2026).
ENV_DRIFT_SCRIPT = REPO_ROOT / "deploy" / "scripts" / "compose-env-drift-recreate.sh"
LITELLM_DEPLOY_WORKFLOW = (
    REPO_ROOT / ".github" / "workflows" / "litellm-hook-deploy.yml"
)


def test_all_litellm_top_level_python_modules_are_mounted_in_compose():
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    missing = [
        module.name
        for module in sorted(LITELLM_DIR.glob("*.py"))
        if f"./litellm/{module.name}:/app/{module.name}:ro" not in compose_text
    ]

    assert missing == []


def test_litellm_prisma_migrations_are_preflight_gated():
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    compose_up_text = COMPOSE_UP_SCRIPT.read_text(encoding="utf-8")
    deploy_compose_text = DEPLOY_COMPOSE_WORKFLOW.read_text(encoding="utf-8")
    litellm_deploy_text = LITELLM_DEPLOY_WORKFLOW.read_text(encoding="utf-8")

    assert 'USE_PRISMA_MIGRATE: "True"' in compose_text
    assert "check_litellm_prisma_migration_baseline" in compose_up_text
    assert "public._prisma_migrations is missing" in compose_up_text
    assert "Refusing to recreate litellm" in compose_up_text
    assert "if has_service litellm; then" in compose_up_text
    assert '[[ -z "$SERVICE" || "$SERVICE" == "litellm" ]]' not in compose_up_text
    # Since 2026-10-28 (#2118) a compose change reaches core-01 only through
    # deploy-compose.yml, which keeps the .prev copies and the data-store
    # snapshots; this workflow runs after a green sync and must never copy the
    # compose file itself.
    assert "- 'deploy/docker-compose.yml'" not in litellm_deploy_text
    assert "workflows: ['Sync compose and config to core-01']" in litellm_deploy_text
    assert "rsync -av deploy/docker-compose.yml" not in litellm_deploy_text
    # A recreate is a minute of "LLM connection error" for every consumer, and
    # the workflow runs after every green compose sync: only a changed hook
    # file may force it (2026-10-28: three idle recreates in a row, one rollback).
    assert litellm_deploy_text.count("compose-up.sh --force-recreate litellm") == 1
    assert 'if [ "${{ steps.sync.outputs.changed }}" != 0 ]' in litellm_deploy_text
    assert "retry rsync -ai deploy/litellm/" in litellm_deploy_text
    env_drift_text = ENV_DRIFT_SCRIPT.read_text(encoding="utf-8")
    assert "awk '$0 != \"litellm\"'" in env_drift_text
    assert "no env-drift services resolved; refusing an all-services compose up" in env_drift_text
    assert "compose-env-drift-recreate.sh" in deploy_compose_text


def test_compose_allowlist_preserves_the_empty_kill_switch():
    """`${VAR-*}`, never `${VAR:-*}`, for the PII enforcement allowlist.

    `_org_is_enforced` documents an explicitly empty allowlist as "enforce
    for NO org" and `test_empty_allowlist_means_enforcement_for_no_org_even_with_flag_on`
    pins that in the hook. The colon form would make it unreachable from
    the deployment: Compose substitutes the default for unset AND empty, so
    an operator emptying the variable to switch enforcement off would get
    `*` and switch it on for every tenant instead.
    """
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")

    assert "KLAI_PII_ENFORCE_ORG_IDS: ${KLAI_PII_ENFORCE_ORG_IDS-*}" in compose_text
    assert "${KLAI_PII_ENFORCE_ORG_IDS:-" not in compose_text
