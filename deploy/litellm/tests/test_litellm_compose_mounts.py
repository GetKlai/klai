"""Regression tests for LiteLLM hook single-file bind mounts."""

from __future__ import annotations

import ast
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]
LITELLM_DIR = REPO_ROOT / "deploy" / "litellm"

PACKAGE_MOUNTS = {"klai_citations", "klai_llm_safety"}


def test_klai_knowledge_single_file_imports_are_mounted_in_compose() -> None:
    """Every direct local .py dependency must be mounted into the stock image.

    The production LiteLLM image runs with ``PYTHONPATH=/app`` and only sees
    files listed in ``deploy/docker-compose.yml``. Extracting a helper without
    adding a mount makes ``klai_knowledge.klai_knowledge_hook`` fail to import,
    which breaks all chat completions at startup.
    """

    tree = ast.parse((LITELLM_DIR / "klai_knowledge.py").read_text())
    local_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module
        and node.level == 0
        and (LITELLM_DIR / f"{node.module}.py").exists()
        and node.module not in PACKAGE_MOUNTS
    }

    compose = (REPO_ROOT / "deploy" / "docker-compose.yml").read_text()
    missing = sorted(
        module
        for module in local_modules
        if f"./litellm/{module}.py:/app/{module}.py:ro" not in compose
    )

    assert missing == []
