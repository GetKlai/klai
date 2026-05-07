"""Detect drift between vendored ``deploy/litellm/klai_chat_prompts.py``
and the canonical ``klai-libs/chat-prompts/klai_chat_prompts/__init__.py``.

SPEC-RAG-MULTILINGUAL-CHAT-001 Phase 4 (REQ-10). The vendored copy exists
because the LiteLLM container is a stock upstream image without a path-dep
mechanism. This test fails when the canonical library changes but the
vendored copy isn't updated to match.

Same rationale as ``test_klai_service_auth_drift.py``. The plan to remove
the vendored copy entirely is the same: build a custom litellm Dockerfile
that ``pip install``s ``klai-chat-prompts`` and delete this test along with
``klai_chat_prompts.py``.

Implementation note
-------------------

Python's import system deduplicates by module name, so we cannot ``import
klai_chat_prompts`` once for the vendored copy and once for the canonical
package and expect to get two different namespaces. We use explicit
``importlib.util.spec_from_file_location`` to load each file under a
unique synthetic name (``_drift_vendored_prompts``,
``_drift_canonical_prompts``).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CANONICAL_PATH = (
    _REPO_ROOT / "klai-libs" / "chat-prompts" / "klai_chat_prompts" / "__init__.py"
)
_VENDORED_PATH = _REPO_ROOT / "deploy" / "litellm" / "klai_chat_prompts.py"


def _load(name: str, path: Path) -> ModuleType:
    """Load ``path`` as a fresh module under ``name`` (no name-dedup with sys.path)."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"could not build module spec for {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_vendored_grounded_prompt_matches_canonical() -> None:
    """The ``GROUNDED_CHAT_SYSTEM_PROMPT`` constant string MUST be byte-identical
    between vendored and canonical copies. Even a whitespace difference would
    make path A (LiteLLM hook) drift from paths B+C (synthesis.py +
    partner_chat.py) and break the unified multilingual contract that REQ-02
    + REQ-10 ship together.
    """
    vendored = _load("_drift_vendored_prompts", _VENDORED_PATH)
    canonical = _load("_drift_canonical_prompts", _CANONICAL_PATH)

    assert vendored.GROUNDED_CHAT_SYSTEM_PROMPT == canonical.GROUNDED_CHAT_SYSTEM_PROMPT, (
        "GROUNDED_CHAT_SYSTEM_PROMPT drift between vendored and canonical.\n"
        "  Update deploy/litellm/klai_chat_prompts.py to match "
        "klai-libs/chat-prompts/klai_chat_prompts/__init__.py.\n"
        "  See SPEC-RAG-MULTILINGUAL-CHAT-001 REQ-02 + REQ-10."
    )


def test_vendored_module_exports_only_grounded_prompt() -> None:
    """``__all__`` must list exactly ``GROUNDED_CHAT_SYSTEM_PROMPT``. If the
    canonical library starts exporting a second constant (e.g. a separate
    LITELLM_HOOK_NL_PREFIX), this test fails so we remember to vendor it
    too. Equivalent to the public-API drift test in service-auth."""
    vendored = _load("_drift_vendored_all", _VENDORED_PATH)
    canonical = _load("_drift_canonical_all", _CANONICAL_PATH)

    assert vendored.__all__ == canonical.__all__, (
        "__all__ drift between vendored and canonical klai_chat_prompts.\n"
        f"  vendored.__all__ = {vendored.__all__}\n"
        f"  canonical.__all__ = {canonical.__all__}\n"
        "  If a new constant was added canonically, vendor it too."
    )
