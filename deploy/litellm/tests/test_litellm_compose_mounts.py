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
LITELLM_DEPLOY_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "litellm-hook-deploy.yml"


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
    assert '[[ "$SERVICE" == "litellm" ]]' in compose_up_text
    assert '[[ -z "$SERVICE" || "$SERVICE" == "litellm" ]]' not in compose_up_text
    assert "- 'deploy/docker-compose.yml'" in litellm_deploy_text
    assert "grep -vx 'litellm'" in deploy_compose_text
