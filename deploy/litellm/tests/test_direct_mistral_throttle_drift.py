"""Drift guard: deploy-side Python must not bypass LiteLLM quota accounting."""

from __future__ import annotations

from pathlib import Path

_LITELLM_ROOT = Path(__file__).resolve().parent.parent


def test_no_deploy_python_calls_mistral_chat_completions_directly() -> None:
    offenders: list[str] = []

    for path in sorted(_LITELLM_ROOT.rglob("*.py")):
        if "tests" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        if "api.mistral.ai/v1/chat/completions" in source:
            offenders.append(str(path.relative_to(_LITELLM_ROOT)))

    assert not offenders, (
        "These files bypass LiteLLM's shared RPM/TPM accounting and fallback: "
        f"{offenders}"
    )
