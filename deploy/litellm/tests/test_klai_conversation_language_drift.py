"""Detect drift between vendored ``deploy/litellm/klai_conversation_language.py``
and the canonical ``klai-libs/chat-prompts/klai_chat_prompts/language.py``.

The vendored copy exists because the LiteLLM container is a stock upstream
image that bind-mounts flat .py files and cannot pip-install the package.
This test fails when the canonical module changes but the vendored copy is
not re-copied to match. Unlike the prompts drift test, the language module
is vendored BYTE-FOR-BYTE, so the source comparison is the primary guard;
the behaviour comparisons below it document exactly which observable surfaces
must agree and give the readable failure messages.

Implementation note
-------------------

Python's import system deduplicates by module name, so we cannot ``import``
once for the vendored copy and once for the canonical file and expect two
different namespaces. We use explicit
``importlib.util.spec_from_file_location`` to load each file under a unique
synthetic name (``_drift_vendored_language``, ``_drift_canonical_language``).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CANONICAL_PATH = (
    _REPO_ROOT / "klai-libs" / "chat-prompts" / "klai_chat_prompts" / "language.py"
)
_VENDORED_PATH = _REPO_ROOT / "deploy" / "litellm" / "klai_conversation_language.py"


def _load(name: str, path: Path) -> ModuleType:
    """Load ``path`` as a fresh module under ``name`` (no name-dedup with sys.path).

    Registration in sys.modules before exec is required here (unlike the
    prompts drift test): klai_conversation_language defines @dataclass classes
    under ``from __future__ import annotations``, and dataclasses resolves
    stringified field types via ``sys.modules[cls.__module__]`` at decoration
    time.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError(f"could not build module spec for {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _samples() -> list[str]:
    return [
        "Hoe kan ik een nieuwe gebruiker uitnodigen in ons team?",
        "How do I invite a new user to my team and set their permissions?",
        "```\nprint('code must never vote')\n```",
        "Subject: Re: Offerte\nFrom: jan@example.nl\n\nIk begrijp de factuur niet, legt u uit?",
        "Klant schreef \"Please refund the full amount immediately today\" en vroeg om opheldering",
        '{"status": "error", "code": 500, "detail": "internal server failure on the primary host"}',
        "Antwoord in het Nederlands",
        "In English please",
        "Duits?",
        "",
        "   \n  ",
    ]


def _conversations() -> list[list[dict]]:
    nl = "Hoe kan ik een nieuwe gebruiker uitnodigen in ons team?"
    nl2 = "Kunt u mij ook uitleggen hoe de facturatie maandelijks wordt berekend?"
    en = "How do I invite a new user to my team and set their permissions?"
    de = "Wie kann ich einen neuen Benutzer in mein Team einladen und seine Rechte aendern?"
    fr = "Comment puis-je inviter un nouvel utilisateur dans mon equipe et modifier ses droits ?"
    machine = (
        "Traceback (most recent call last):\n"
        '  File "app.py", line 42, in main\n'
        "    raise ValueError('boom')"
    )
    return [
        [],
        [{"role": "system", "content": en}, {"role": "assistant", "content": en}],
        [{"role": "user", "content": nl}],
        [{"role": "user", "content": nl}, {"role": "user", "content": en}],
        [{"role": "user", "content": nl}, {"role": "user", "content": nl2},
         {"role": "user", "content": nl}, {"role": "user", "content": de}],
        [{"role": "user", "content": nl}, {"role": "user", "content": nl2},
         {"role": "user", "content": nl}, {"role": "user", "content": de},
         {"role": "user", "content": fr}],
        [{"role": "user", "content": nl}, {"role": "user", "content": machine},
         {"role": "user", "content": "Antwoord in het Nederlands"}],
        [{"role": "user", "content": [{"type": "text", "text": nl},
                                      {"type": "image_url", "image_url": {"url": "x"}}]}],
        [None, "junk", {"role": "user"}],  # type: ignore[list-item]
    ]


def test_vendored_source_is_byte_identical_to_canonical() -> None:
    """The language module is vendored as a byte-for-byte copy, so the strongest
    possible drift guard applies: any difference at all fails here.
    """
    vendored_src = _VENDORED_PATH.read_text(encoding="utf-8")
    canonical_src = _CANONICAL_PATH.read_text(encoding="utf-8")

    assert vendored_src == canonical_src, (
        "klai_conversation_language.py source drift between vendored and canonical.\n"
        "  Copy klai-libs/chat-prompts/klai_chat_prompts/language.py over "
        "deploy/litellm/klai_conversation_language.py (byte-for-byte)."
    )


def test_vendored_constants_match_canonical() -> None:
    """Every tuning constant of the algorithm MUST be equal between vendored
    and canonical copies. A silently different threshold would make the
    LiteLLM hook (path A) decide differently from portal-backend and
    retrieval-api (paths B+C) on the same conversation.
    """
    vendored = _load("_drift_vendored_constants", _VENDORED_PATH)
    canonical = _load("_drift_canonical_constants", _CANONICAL_PATH)

    for name in (
        "TARGET_LANGUAGES",
        "MIN_PROSE_WORDS",
        "IDENTIFY_MIN_CONFIDENCE",
        "LONG_PROSE_WORDS",
        "IDENTIFY_MIN_CONFIDENCE_SHORT",
        "OPENING_WINDOW_TURNS",
        "FIRST_SWITCH_CONFIRMATIONS",
        "SUBSEQUENT_SWITCH_CONFIRMATIONS",
        "MAX_EXPLICIT_REQUEST_WORDS",
        "REASON_ESTABLISHED",
        "REASON_SWITCHED",
        "REASON_LOCKED",
        "REASON_EXPLICIT_REQUEST",
        "REASON_NO_EVIDENCE",
        "REASON_LOW_CONFIDENCE",
        "REASON_DETECTOR_UNAVAILABLE",
        "EVIDENCE_PROSE",
        "EVIDENCE_MACHINE",
        "EVIDENCE_EMPTY",
    ):
        assert getattr(vendored, name) == getattr(canonical, name), (
            f"{name} drift between vendored and canonical klai_conversation_language.\n"
            "  Update deploy/litellm/klai_conversation_language.py to match "
            "klai-libs/chat-prompts/klai_chat_prompts/language.py."
        )


def test_vendored_language_name_table_matches_canonical() -> None:
    """``EXPLICIT_LANGUAGE_NAMES`` MUST be dict-equal between vendored and
    canonical. The table drives the strongest signal in the algorithm (an
    explicit request switches immediately); a partial table in the container
    would silently turn "Respond in het Nederlands" into an abstention on
    one surface only.
    """
    vendored = _load("_drift_vendored_names", _VENDORED_PATH)
    canonical = _load("_drift_canonical_names", _CANONICAL_PATH)

    assert vendored.EXPLICIT_LANGUAGE_NAMES == canonical.EXPLICIT_LANGUAGE_NAMES, (
        "EXPLICIT_LANGUAGE_NAMES drift between vendored and canonical.\n"
        "  Update deploy/litellm/klai_conversation_language.py to match "
        "klai-libs/chat-prompts/klai_chat_prompts/language.py."
    )


def test_vendored_evidence_gate_matches_canonical() -> None:
    """``classify_turn_evidence`` MUST produce the same classification and the
    same surviving prose for the same input on both copies.
    """
    vendored = _load("_drift_vendored_gate", _VENDORED_PATH)
    canonical = _load("_drift_canonical_gate", _CANONICAL_PATH)

    for sample in _samples():
        v = vendored.classify_turn_evidence(sample)
        c = canonical.classify_turn_evidence(sample)
        assert (v.classification, v.prose) == (c.classification, c.prose), (
            f"classify_turn_evidence drift for sample={sample[:60]!r}.\n"
            "  Update deploy/litellm/klai_conversation_language.py to match "
            "klai-libs/chat-prompts/klai_chat_prompts/language.py."
        )


def test_vendored_explicit_request_detection_matches_canonical() -> None:
    """``detect_explicit_language_request`` MUST return the same target code
    (or None) for the same surviving prose on both copies.
    """
    vendored = _load("_drift_vendored_request", _VENDORED_PATH)
    canonical = _load("_drift_canonical_request", _CANONICAL_PATH)

    for sample in _samples():
        prose = canonical.classify_turn_evidence(sample).prose
        assert (
            vendored.detect_explicit_language_request(prose)
            == canonical.detect_explicit_language_request(prose)
        ), (
            f"detect_explicit_language_request drift for sample={sample[:60]!r}.\n"
            "  Update deploy/litellm/klai_conversation_language.py to match "
            "klai-libs/chat-prompts/klai_chat_prompts/language.py."
        )


def test_vendored_resolve_matches_canonical() -> None:
    """``resolve_conversation_language`` MUST produce an equal LanguageDecision
    for the same conversation on both copies — the whole point of the module
    is one unified decision across every chat surface.
    """
    vendored = _load("_drift_vendored_resolve", _VENDORED_PATH)
    canonical = _load("_drift_canonical_resolve", _CANONICAL_PATH)

    for conversation in _conversations():
        v = vendored.resolve_conversation_language(conversation)
        c = canonical.resolve_conversation_language(conversation)
        # Field-by-field: dataclass __eq__ compares class identity, and the two
        # importlib loads each have their own LanguageDecision class object.
        v_tuple = (v.language, v.reason, v.votes, v.abstentions, v.switches, v.locked)
        c_tuple = (c.language, c.reason, c.votes, c.abstentions, c.switches, c.locked)
        assert v_tuple == c_tuple, (
            f"resolve_conversation_language drift for conversation with "
            f"{len(conversation)} messages: vendored={v} canonical={c}.\n"
            "  Update deploy/litellm/klai_conversation_language.py to match "
            "klai-libs/chat-prompts/klai_chat_prompts/language.py."
        )


def test_vendored_module_all_matches_canonical() -> None:
    """``__all__`` must match the canonical module's ``__all__`` exactly.
    If the canonical library adds, removes, or renames an exported name,
    this test fails so we remember to vendor the change too. Equivalent to
    the public-API drift test in klai_chat_prompts.
    """
    vendored = _load("_drift_vendored_all", _VENDORED_PATH)
    canonical = _load("_drift_canonical_all", _CANONICAL_PATH)

    assert vendored.__all__ == canonical.__all__, (
        "__all__ drift between vendored and canonical klai_conversation_language.\n"
        f"  vendored.__all__ = {vendored.__all__}\n"
        f"  canonical.__all__ = {canonical.__all__}\n"
        "  If a new constant was added canonically, vendor it too."
    )
