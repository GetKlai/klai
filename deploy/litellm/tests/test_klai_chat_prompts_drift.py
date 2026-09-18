"""Detect drift between vendored ``deploy/litellm/klai_chat_prompts.py``
and the canonical ``klai-libs/chat-prompts/klai_chat_prompts/__init__.py``.

SPEC-RAG-MULTILINGUAL-CHAT-001 Phase 4 (REQ-10). The vendored copy exists
because the LiteLLM container is a stock upstream image without a path-dep
mechanism. This test fails when the canonical library changes but the
vendored copy isn't updated to match.

The plan to remove the vendored copy entirely: build a custom litellm
Dockerfile that ``pip install``s ``klai-chat-prompts`` and delete this test
along with ``klai_chat_prompts.py``.

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
import sys
from pathlib import Path
from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CANONICAL_PATH = (
    _REPO_ROOT / "klai-libs" / "chat-prompts" / "klai_chat_prompts" / "__init__.py"
)
_VENDORED_PATH = _REPO_ROOT / "deploy" / "litellm" / "klai_chat_prompts.py"


def _load(name: str, path: Path) -> ModuleType:
    """Load ``path`` as a fresh module under ``name`` (no name-dedup with sys.path).

    Registered in ``sys.modules`` under its synthetic name before exec: pydantic
    resolves a ``from __future__ import annotations`` forward reference (e.g.
    ``AnswerClaims``'s ``Literal`` field) by looking up ``sys.modules[cls.__module__]``
    when it builds the schema, so an unregistered module raises "not fully defined"
    the first time something calls ``model_json_schema()``.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"could not build module spec for {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
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


def test_vendored_kb_context_language_reminder_matches_canonical() -> None:
    """``KB_CONTEXT_LANGUAGE_REMINDER`` MUST be byte-identical between
    vendored and canonical copies. Path A (LiteLLM hook) appends the
    vendored copy as the final block of the KB context; path B
    (partner_chat) appends the canonical one after its Context block.
    The ``__all__`` test only catches a missing NAME — content drift
    here would silently give the two chat surfaces different final
    language anchors.
    """
    vendored = _load("_drift_vendored_kb_reminder", _VENDORED_PATH)
    canonical = _load("_drift_canonical_kb_reminder", _CANONICAL_PATH)

    assert (
        vendored.KB_CONTEXT_LANGUAGE_REMINDER == canonical.KB_CONTEXT_LANGUAGE_REMINDER
    ), (
        "KB_CONTEXT_LANGUAGE_REMINDER drift between vendored and canonical.\n"
        "  Update deploy/litellm/klai_chat_prompts.py to match "
        "klai-libs/chat-prompts/klai_chat_prompts/__init__.py."
    )


def test_vendored_final_response_language_reminder_matches_canonical() -> None:
    """``final_response_language_reminder`` MUST render identically on both copies.

    This is the last provider instruction before generation on every chat
    surface: path A appends the vendored copy via klai_kb_system_prompt, path B
    the canonical one via partner_chat. The ``__all__`` test only catches a
    missing NAME, so content drift here would hand the LibreChat chat and the
    widget different response-language contracts — the exact split this helper
    was moved into the shared library to prevent. Every supported code is
    checked, plus both fallbacks, because a broken NAME lookup degrades
    silently to the generic wording instead of failing.
    """
    vendored = _load("_drift_vendored_final_reminder", _VENDORED_PATH)
    canonical = _load("_drift_canonical_final_reminder", _CANONICAL_PATH)

    assert (
        vendored.FINAL_RESPONSE_LANGUAGE_REMINDER
        == canonical.FINAL_RESPONSE_LANGUAGE_REMINDER
    ), (
        "FINAL_RESPONSE_LANGUAGE_REMINDER drift between vendored and canonical.\n"
        "  Update deploy/litellm/klai_chat_prompts.py to match "
        "klai-libs/chat-prompts/klai_chat_prompts/__init__.py."
    )
    assert vendored.LANGUAGE_NAMES == canonical.LANGUAGE_NAMES, (
        "LANGUAGE_NAMES drift between vendored and canonical.\n"
        "  Update deploy/litellm/klai_chat_prompts.py to match "
        "klai-libs/chat-prompts/klai_chat_prompts/__init__.py."
    )
    for target in (*canonical.LANGUAGE_NAMES, "zz", "", None):
        assert vendored.final_response_language_reminder(
            target
        ) == canonical.final_response_language_reminder(target), (
            f"final_response_language_reminder({target!r}) drift between vendored "
            "and canonical.\n"
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

    # Language CODES (the helper takes a decided code, not a query): both
    # canned languages plus the documented fallbacks for other/missing codes.
    samples = [
        "nl",
        "en",
        "de",
        "",
        None,
        42,
    ]
    for sample in samples:
        assert vendored.no_citable_sources_message(sample) == canonical.no_citable_sources_message(sample), (
            f"no_citable_sources_message drift for sample={sample!r}.\n"
            "  Update deploy/litellm/klai_chat_prompts.py to match "
            "klai-libs/chat-prompts/klai_chat_prompts/__init__.py."
        )


def test_vendored_clarify_turn_addendum_matches_canonical() -> None:
    """``CLARIFY_TURN_ADDENDUM`` MUST be byte-identical between vendored and
    canonical copies. SPEC-RAG-CLARIFY-FLOW-001 REQ-1a: the LiteLLM hook
    (path A, REQ-5) and partner_chat.py (path B, REQ-3) wire the same
    addendum text; drift would give the two chat surfaces different
    clarifying-question instructions for the same decision.
    """
    vendored = _load("_drift_vendored_clarify_addendum", _VENDORED_PATH)
    canonical = _load("_drift_canonical_clarify_addendum", _CANONICAL_PATH)

    assert vendored.CLARIFY_TURN_ADDENDUM == canonical.CLARIFY_TURN_ADDENDUM, (
        "CLARIFY_TURN_ADDENDUM drift between vendored and canonical.\n"
        "  Update deploy/litellm/klai_chat_prompts.py to match "
        "klai-libs/chat-prompts/klai_chat_prompts/__init__.py."
    )


def test_vendored_answer_claims_schema_matches_canonical() -> None:
    """``ANSWER_CLAIMS_SYSTEM_PROMPT``, the ``AnswerClaims`` schema, and the
    ``response_format`` helper MUST match between vendored and canonical
    copies. SPEC-RAG-CLARIFY-FLOW-001 REQ-1b: both chat paths send this
    schema to the same classification call; drift here would mean path A
    and path B validate the model's answer-claims response differently.
    """
    vendored = _load("_drift_vendored_answer_claims", _VENDORED_PATH)
    canonical = _load("_drift_canonical_answer_claims", _CANONICAL_PATH)

    assert vendored.ANSWER_CLAIMS_SYSTEM_PROMPT == canonical.ANSWER_CLAIMS_SYSTEM_PROMPT, (
        "ANSWER_CLAIMS_SYSTEM_PROMPT drift between vendored and canonical.\n"
        "  Update deploy/litellm/klai_chat_prompts.py to match "
        "klai-libs/chat-prompts/klai_chat_prompts/__init__.py."
    )
    assert (
        vendored.AnswerClaims.model_json_schema() == canonical.AnswerClaims.model_json_schema()
    ), (
        "AnswerClaims schema drift between vendored and canonical.\n"
        "  Update deploy/litellm/klai_chat_prompts.py to match "
        "klai-libs/chat-prompts/klai_chat_prompts/__init__.py."
    )
    assert vendored.answer_claims_response_format() == canonical.answer_claims_response_format(), (
        "answer_claims_response_format() drift between vendored and canonical.\n"
        "  Update deploy/litellm/klai_chat_prompts.py to match "
        "klai-libs/chat-prompts/klai_chat_prompts/__init__.py."
    )


def test_vendored_parse_answer_claims_and_should_clarify_match_canonical() -> None:
    """``parse_answer_claims``, ``may_show_model_text_without_sources``,
    ``has_direct_evidence_for_query`` and ``should_clarify`` MUST behave
    identically on both copies — these are the pure decision functions REQ-2
    through REQ-5 wire into each chat path, so a behavioural drift here would
    silently diverge which turns get clarified or shown without a source.
    """
    vendored = _load("_drift_vendored_clarify_fns", _VENDORED_PATH)
    canonical = _load("_drift_canonical_clarify_fns", _CANONICAL_PATH)

    for content in (
        '{"category": "no_claims"}',
        '{"category": "claims"}',
        None,
        "",
        "not json",
        '{"category": "maybe"}',
        '{"category": "no_claims", "extra": "x"}',
    ):
        assert vendored.parse_answer_claims(content) == canonical.parse_answer_claims(content), (
            f"parse_answer_claims({content!r}) drift between vendored and canonical."
        )

    for result in ("no_claims", "claims", None):
        assert vendored.may_show_model_text_without_sources(
            result
        ) == canonical.may_show_model_text_without_sources(result), (
            f"may_show_model_text_without_sources({result!r}) drift between vendored and canonical."
        )

    query = "wie is verantwoordelijk voor Data Readiness?"
    chunks = [{"title": "CV_Jantine_Doornbos.pdf", "text": "Jantine Doornbos is AI-ontwikkelaar en adviseur."}]
    assert vendored.has_direct_evidence_for_query(
        query, chunks
    ) == canonical.has_direct_evidence_for_query(query, chunks), (
        "has_direct_evidence_for_query drift between vendored and canonical."
    )

    for band, has_evidence in (("low", False), ("low", True), ("unknown", False), ("medium", False), ("high", False)):
        assert vendored.should_clarify(
            band, has_direct_evidence=has_evidence
        ) == canonical.should_clarify(band, has_direct_evidence=has_evidence), (
            f"should_clarify({band!r}, has_direct_evidence={has_evidence}) drift between vendored and canonical."
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


def test_vendored_grounding_check_matches_canonical() -> None:
    """The statement-level grounding prompts and their schema MUST be identical
    on both chat paths: the whole point of checking the internal chat with the
    same words is that the two paths can be laid side by side.
    """
    canonical = _load("canonical_chat_prompts_grounding", _CANONICAL_PATH)
    vendored = _load("vendored_chat_prompts_grounding", _VENDORED_PATH)

    assert vendored.GROUNDING_CHECK_SYSTEM_PROMPT == canonical.GROUNDING_CHECK_SYSTEM_PROMPT, (
        "Vendored GROUNDING_CHECK_SYSTEM_PROMPT drifted from the canonical copy.\n"
        "  Update deploy/litellm/klai_chat_prompts.py to match klai-libs/chat-prompts."
    )
    assert vendored.GROUNDING_REPAIR_SYSTEM_PROMPT == canonical.GROUNDING_REPAIR_SYSTEM_PROMPT, (
        "Vendored GROUNDING_REPAIR_SYSTEM_PROMPT drifted from the canonical copy."
    )
    assert vendored.GROUNDING_NOTHING_LEFT == canonical.GROUNDING_NOTHING_LEFT
    assert (
        vendored.grounding_check_response_format() == canonical.grounding_check_response_format()
    ), "The strict schema the two paths send drifted."

    # The same checker reply must produce the same decision on both paths: the
    # prompts being equal is not enough if the parsing or the repair threshold
    # differs.
    reply = (
        '{"statements": ['
        '{"statement": "Ga naar Belplan.", "evidence": "Ga naar Belplan", "support": "supported"},'
        '{"statement": "Bel 020-1234567.", "evidence": "", "support": "not_in_articles"},'
        '{"statement": "Dat kost 5 euro.", "evidence": "", "support": "contradicted"}'
        "]}"
    )
    canonical_check = canonical.parse_grounding_check(reply)
    vendored_check = vendored.parse_grounding_check(reply)
    assert [item.statement for item in vendored_check.unsupported] == [
        item.statement for item in canonical_check.unsupported
    ], "The two copies select different statements as unsupported."
    assert vendored_check.worth_repairing == canonical_check.worth_repairing, (
        "The two copies disagree on when an answer is worth repairing."
    )
    assert vendored.parse_grounding_check("not json") is canonical.parse_grounding_check("not json")
