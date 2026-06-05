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

    assert (
        vendored.GROUNDED_CHAT_SYSTEM_PROMPT == canonical.GROUNDED_CHAT_SYSTEM_PROMPT
    ), (
        "GROUNDED_CHAT_SYSTEM_PROMPT drift between vendored and canonical.\n"
        "  Update deploy/litellm/klai_chat_prompts.py to match "
        "klai-libs/chat-prompts/klai_chat_prompts/__init__.py.\n"
        "  See SPEC-RAG-MULTILINGUAL-CHAT-001 REQ-02 + REQ-10."
    )


def test_vendored_general_prompt_matches_canonical() -> None:
    """The ``GENERAL_CHAT_SYSTEM_PROMPT`` constant string MUST be
    byte-identical between vendored and canonical copies. Same rationale
    as :func:`test_vendored_grounded_prompt_matches_canonical`: the
    LiteLLM hook (path A) imports from the vendored copy, while paths
    B + C never reach GENERAL because they always carry KB scope. A
    drift here is silent and only surfaces via wrong model behaviour
    in the no-KB branch.
    """
    vendored = _load("_drift_vendored_general", _VENDORED_PATH)
    canonical = _load("_drift_canonical_general", _CANONICAL_PATH)

    assert (
        vendored.GENERAL_CHAT_SYSTEM_PROMPT == canonical.GENERAL_CHAT_SYSTEM_PROMPT
    ), (
        "GENERAL_CHAT_SYSTEM_PROMPT drift between vendored and canonical.\n"
        "  Update deploy/litellm/klai_chat_prompts.py to match "
        "klai-libs/chat-prompts/klai_chat_prompts/__init__.py."
    )


def test_vendored_open_kb_prompt_matches_canonical() -> None:
    """The Open-with-KB prompt is path-A critical: it is the only foundation
    that allows general-knowledge fallback while KB scope remains selected.
    Drift would silently reintroduce KB-only behaviour in production.
    """
    vendored = _load("_drift_vendored_open_kb", _VENDORED_PATH)
    canonical = _load("_drift_canonical_open_kb", _CANONICAL_PATH)

    assert (
        vendored.OPEN_KB_CHAT_SYSTEM_PROMPT == canonical.OPEN_KB_CHAT_SYSTEM_PROMPT
    ), (
        "OPEN_KB_CHAT_SYSTEM_PROMPT drift between vendored and canonical.\n"
        "  Update deploy/litellm/klai_chat_prompts.py to match "
        "klai-libs/chat-prompts/klai_chat_prompts/__init__.py."
    )


def test_vendored_meta_prompt_matches_canonical() -> None:
    """The ``META_CHAT_SYSTEM_PROMPT`` constant string MUST be byte-identical
    between vendored and canonical copies. The LiteLLM hook (path A) prepends
    this prompt on the meta-question early-return path (``_is_meta_query``);
    drift here would mean Klai answers "what is Klai?" with stale wording in
    production while passing local tests against the canonical lib.

    Paths B (partner_chat) and C (synthesis) do NOT use META — they are
    server-to-server with KB scope always in play.
    """
    vendored = _load("_drift_vendored_meta", _VENDORED_PATH)
    canonical = _load("_drift_canonical_meta", _CANONICAL_PATH)

    assert vendored.META_CHAT_SYSTEM_PROMPT == canonical.META_CHAT_SYSTEM_PROMPT, (
        "META_CHAT_SYSTEM_PROMPT drift between vendored and canonical.\n"
        "  Update deploy/litellm/klai_chat_prompts.py to match "
        "klai-libs/chat-prompts/klai_chat_prompts/__init__.py."
    )


def test_vendored_dutch_markers_match_canonical() -> None:
    """``DUTCH_QUERY_MARKERS`` MUST be set-equal between vendored and
    canonical. The set drives the language choice for the strict-mode
    refusal in both the LiteLLM hook (path A) and partner_chat.py
    (path B); drift here means one surface refuses in Dutch while the
    other refuses in English for the same query.
    """
    vendored = _load("_drift_vendored_markers", _VENDORED_PATH)
    canonical = _load("_drift_canonical_markers", _CANONICAL_PATH)

    assert vendored.DUTCH_QUERY_MARKERS == canonical.DUTCH_QUERY_MARKERS, (
        "DUTCH_QUERY_MARKERS drift between vendored and canonical.\n"
        "  Update deploy/litellm/klai_chat_prompts.py to match "
        "klai-libs/chat-prompts/klai_chat_prompts/__init__.py."
    )


def test_vendored_no_citable_sources_message_matches_canonical() -> None:
    """``no_citable_sources_message`` MUST produce the same output for
    the same input on both the vendored and canonical copies. Drift here
    means the LiteLLM hook and partner_chat.py disagree on which language
    to use for the same canned refusal.
    """
    vendored = _load("_drift_vendored_refusal", _VENDORED_PATH)
    canonical = _load("_drift_canonical_refusal", _CANONICAL_PATH)

    samples = [
        "Wat is dit?",
        "What is this?",
        "Hoeveel kost het?",
        "How much does it cost?",
        "",
        None,
    ]
    for sample in samples:
        assert vendored.no_citable_sources_message(sample) == canonical.no_citable_sources_message(sample), (
            f"no_citable_sources_message drift for sample={sample!r}.\n"
            "  Update deploy/litellm/klai_chat_prompts.py to match "
            "klai-libs/chat-prompts/klai_chat_prompts/__init__.py."
        )


def test_vendored_module_all_matches_canonical() -> None:
    """``__all__`` must match the canonical library's ``__all__`` exactly.
    If the canonical library adds, removes, or renames an exported
    constant, this test fails so we remember to vendor the change too.
    Equivalent to the public-API drift test in service-auth."""
    vendored = _load("_drift_vendored_all", _VENDORED_PATH)
    canonical = _load("_drift_canonical_all", _CANONICAL_PATH)

    assert vendored.__all__ == canonical.__all__, (
        "__all__ drift between vendored and canonical klai_chat_prompts.\n"
        f"  vendored.__all__ = {vendored.__all__}\n"
        f"  canonical.__all__ = {canonical.__all__}\n"
        "  If a new constant was added canonically, vendor it too."
    )
