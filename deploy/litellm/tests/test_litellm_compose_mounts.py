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


def test_all_litellm_top_level_python_modules_are_mounted_in_compose():
    compose_text = COMPOSE_FILE.read_text(encoding="utf-8")
    missing = [
        module.name
        for module in sorted(LITELLM_DIR.glob("*.py"))
        if f"./litellm/{module.name}:/app/{module.name}:ro" not in compose_text
    ]

    assert missing == []
