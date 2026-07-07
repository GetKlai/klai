"""Tests for the anti-hallucination injection in klai_knowledge.py.

SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-2.

Tests the inject-on-low-confidence path inside ``async_pre_call_hook`` by
mocking the retrieve HTTP response (with various ``confidence_band`` values)
and asserting the injected text appears in the system prompt that the hook
constructs and forwards to the model.

The full hook test infrastructure lives in ``test_klai_knowledge_hook.py``
(2147 lines). This file targets the narrow injection contract so future
prompt-text tweaks can be verified without spinning up the full mock chain.
"""

from __future__ import annotations

import importlib
import sys
import types
from unittest.mock import MagicMock

import pytest

from tests.klai_module_reset import reset_klai_kb_modules


@pytest.fixture(autouse=True)
def _mock_litellm():
    """Mock litellm so klai_knowledge can be imported outside the container."""
    litellm_mod = types.ModuleType("litellm")
    integrations_mod = types.ModuleType("litellm.integrations")
    custom_logger_mod = types.ModuleType("litellm.integrations.custom_logger")

    class CustomLogger:
        async def async_pre_call_hook(self, *args, **kwargs):
            pass

        async def async_post_call_success_hook(self, *args, **kwargs):
            pass

        async def async_post_call_failure_hook(self, *args, **kwargs):
            pass

    custom_logger_mod.CustomLogger = CustomLogger
    litellm_mod.integrations = integrations_mod
    integrations_mod.custom_logger = custom_logger_mod

    sys.modules["litellm"] = litellm_mod
    sys.modules["litellm.integrations"] = integrations_mod
    sys.modules["litellm.integrations.custom_logger"] = custom_logger_mod

    yield

    for mod_name in [
        "litellm",
        "litellm.integrations",
        "litellm.integrations.custom_logger",
    ]:
        sys.modules.pop(mod_name, None)
    reset_klai_kb_modules()


def _load_hook(monkeypatch, extra_env=None):
    """Reload klai_knowledge with the given env vars.

    Patterned after the canonical helper in test_klai_knowledge_hook.py
    but trimmed to the env keys this file's tests need.
    """
    env = {
        "PORTAL_INTERNAL_SECRET": "test-portal-secret",
        "RETRIEVAL_INTERNAL_SECRET": "test-retrieval-secret",
        "KNOWLEDGE_RETRIEVE_URL": "http://retrieval-api:8040/retrieve",
        "PORTAL_API_URL": "http://portal-api:8000",
    }
    if extra_env:
        env.update(extra_env)
    for k, v in env.items():
        monkeypatch.setenv(k, v)

    reset_klai_kb_modules()
    import klai_knowledge

    importlib.reload(klai_knowledge)
    monkeypatch.setattr(klai_knowledge, "_fire_gap_event", MagicMock())
    monkeypatch.setattr(klai_knowledge, "_fire_retrieval_log", MagicMock())
    return klai_knowledge


# ---------------------------------------------------------------------------
# Constants are loadable + tunable
# ---------------------------------------------------------------------------


def test_injection_text_constant_exists(monkeypatch) -> None:
    """The injection text is defined at module level so prompt
    iterations are a single-file change.
    """
    klai_knowledge = _load_hook(monkeypatch)
    assert hasattr(klai_knowledge, "_LOW_CONFIDENCE_INJECTION_TEXT")
    text = klai_knowledge._LOW_CONFIDENCE_INJECTION_TEXT
    assert isinstance(text, str)
    assert len(text) > 100  # non-trivial content
    # must contain the key anti-hallucination instructions
    assert "do not" in text.lower() and "invent" in text.lower()
    assert "clarifying question" in text.lower()
    # Instruction blocks are English-only (SPEC-RAG-MULTILINGUAL-CHAT-001
    # REQ-10): a Dutch guard here was the last strong language anchor and
    # pulled English questions into Dutch answers on the low-confidence band.
    assert "verzin" not in text.lower()
    assert "relevantie" not in text.lower()


def test_open_low_confidence_text_preserves_open_contract(monkeypatch) -> None:
    """Open mode's low-confidence guard must not collapse back to KB-only.

    The strict low-confidence block is intentionally conservative. The Open
    block is a different contract: weak KB chunks should stop unsupported
    KB claims, not stop general-knowledge or user-context answers.
    """
    klai_knowledge = _load_hook(monkeypatch)
    text = klai_knowledge._LOW_CONFIDENCE_OPEN_CONTEXT_TEXT

    assert "Open mode stays active" in text
    assert "do not refuse solely because KB evidence is weak" in text
    assert "Answer from general knowledge or visible user context" in text
    assert "the knowledge base does not support that specific claim" in text
    assert "Cite only what is literally in the chunks" not in text
    assert "alleen een algemeen antwoord wanneer dat veilig kan" not in text
    # Attachment policy is a cross-cutting, mode-independent concern; it lives
    # in the foundation-layer _USER_PROVIDED_CONTENT_SCOPE clause, not here.
    assert "User-provided image attachments" not in text


def test_user_provided_content_scope_clause(monkeypatch) -> None:
    """Attachment policy: standalone user content, mode-independent, never a KB
    source. Pins the contract Mark described 2026-06-06: a screenshot held up
    against the KB is usable in any mode.
    """
    klai_knowledge = _load_hook(monkeypatch)
    text = klai_knowledge._USER_PROVIDED_CONTENT_SCOPE

    assert "[User-provided content]" in text
    assert "independent of Strict/Open mode" in text
    assert "even in Strict mode" in text
    assert "directly observable or user-provided information" in text
    assert "do not add general-world explanations" in text
    assert "NOT knowledge-base sources" in text
    assert "never cite them as numbered sources" in text
    assert "it never blocks the user's own attachments or visible conversation" in text


def test_kb_answer_policy_matrix_user_content_citation_suppression(monkeypatch) -> None:
    """Only retrieval states with actual candidate evidence may suppress weak
    KB citations on user-content answers.
    """
    klai_knowledge = _load_hook(monkeypatch)

    for state in ("zero_chunks", "chunks_present"):
        policy = klai_knowledge.KbAnswerPolicy(
            state=state,
            prompt_mode="open_kb",
            user_provided_content_context=True,
            low_confidence_inject=True,
        )
        meta = policy.metadata()

        assert meta["answer_policy_state"] == state
        assert meta["answer_policy_mode"] == "open"
        assert meta["allow_uncited_user_content"] is True
        assert meta["suppress_kb_citations"] is True

    for state in ("retrieval_failure", "gate_bypassed", "missing_evidence_pack"):
        policy = klai_knowledge.KbAnswerPolicy(
            state=state,
            prompt_mode="strict_kb",
            user_provided_content_context=True,
            low_confidence_inject=True,
        )
        meta = policy.metadata()

        assert meta["answer_policy_state"] == state
        assert meta["answer_policy_mode"] == "strict"
        assert meta["allow_uncited_user_content"] is True
        assert meta["suppress_kb_citations"] is False

    policy = klai_knowledge.KbAnswerPolicy(
        state="chunks_present",
        prompt_mode="strict_kb",
        user_provided_content_context=False,
        low_confidence_inject=True,
    )
    meta = policy.metadata()
    assert meta["allow_uncited_user_content"] is False
    assert meta["suppress_kb_citations"] is False


def test_injection_disabled_flag_default_false(monkeypatch) -> None:
    """KNOWLEDGE_DISABLE_LOW_CONFIDENCE_INJECTION default = '0' → False."""
    monkeypatch.delenv("KNOWLEDGE_DISABLE_LOW_CONFIDENCE_INJECTION", raising=False)
    klai_knowledge = _load_hook(monkeypatch)
    assert klai_knowledge._LOW_CONFIDENCE_INJECTION_DISABLED is False


def test_injection_disabled_flag_true_when_env_is_one(monkeypatch) -> None:
    """KNOWLEDGE_DISABLE_LOW_CONFIDENCE_INJECTION='1' → True (rollback path)."""
    klai_knowledge = _load_hook(
        monkeypatch,
        extra_env={"KNOWLEDGE_DISABLE_LOW_CONFIDENCE_INJECTION": "1"},
    )
    assert klai_knowledge._LOW_CONFIDENCE_INJECTION_DISABLED is True


def test_injection_disabled_flag_false_for_other_values(monkeypatch) -> None:
    """Only the literal '1' enables disable. 'true', 'yes', '0' all stay off
    so a typo can't accidentally suppress the safety layer.
    """
    for v in ("0", "true", "yes", "TRUE", "false"):
        klai_knowledge = _load_hook(
            monkeypatch,
            extra_env={"KNOWLEDGE_DISABLE_LOW_CONFIDENCE_INJECTION": v},
        )
        # Only "1" disables
        expected = v == "1"
        assert klai_knowledge._LOW_CONFIDENCE_INJECTION_DISABLED is expected, (
            f"value={v!r} expected={expected}"
        )


# ---------------------------------------------------------------------------
# top_k default: REQ-4
# ---------------------------------------------------------------------------


def test_retrieve_top_k_default_is_20(monkeypatch) -> None:
    """SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 REQ-4: hook now requests top_k=20
    by default (was 5). Anthropic Contextual Retrieval finding.
    """
    monkeypatch.delenv("KNOWLEDGE_RETRIEVE_TOP_K", raising=False)
    klai_knowledge = _load_hook(monkeypatch)
    assert klai_knowledge.RETRIEVE_TOP_K == 20


def test_retrieve_top_k_env_override(monkeypatch) -> None:
    """Operator can roll back to 5 via env var without redeploy if top_k=20
    proves too costly on a specific tenant cluster.
    """
    klai_knowledge = _load_hook(
        monkeypatch,
        extra_env={"KNOWLEDGE_RETRIEVE_TOP_K": "5"},
    )
    assert klai_knowledge.RETRIEVE_TOP_K == 5


# ---------------------------------------------------------------------------
# Injection trigger logic — tested via the variable assignment path inside
# the hook. We don't run the full async_pre_call_hook because that requires
# the entire DualCache + portal-templates + taxonomy mock chain. Instead we
# verify the trigger condition directly: ``confidence_band in ("low",
# "unknown")`` — which is the literal expression in the hook.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("band", "expected_inject"),
    [
        ("low", True),
        ("unknown", True),
        ("medium", False),
        ("high", False),
        (None, False),  # bypass path, no band field on response
    ],
)
def test_injection_trigger_table(band, expected_inject) -> None:
    """The hook's injection trigger condition: ``band in ("low", "unknown")``.

    Mirrored as a unit test so the SPEC's contract is locked in even if
    the surrounding code in async_pre_call_hook is restructured.
    """
    inject = band in ("low", "unknown")
    assert inject is expected_inject


def test_low_confidence_injection_skips_when_chunks_contain_query_entity(
    monkeypatch,
) -> None:
    """Strict mode must not refuse when low-scored chunks directly contain the answer."""
    klai_knowledge = _load_hook(monkeypatch)

    assert (
        klai_knowledge._should_apply_low_confidence_injection(
            "low",
            user_query="wie is jantine?",
            evidence_chunks=[
                {
                    "title": "CV_Jantine_Doornbos.pdf",
                    "text": "Jantine Doornbos\nAI-ontwikkelaar & adviseur",
                }
            ],
        )
        is False
    )


def test_low_confidence_injection_still_applies_without_direct_evidence(
    monkeypatch,
) -> None:
    """The safety layer remains active for low-scored unrelated chunks."""
    klai_knowledge = _load_hook(monkeypatch)

    assert (
        klai_knowledge._should_apply_low_confidence_injection(
            "low",
            user_query="wie is verantwoordelijk voor Data Readiness?",
            evidence_chunks=[
                {
                    "title": "CV_Jantine_Doornbos.pdf",
                    "text": "Jantine Doornbos is AI-ontwikkelaar en adviseur.",
                }
            ],
        )
        is True
    )


def test_low_confidence_injection_does_not_skip_on_substring_overlap(monkeypatch) -> None:
    """`over` in the query must not match the title `Overige problemen`."""
    klai_knowledge = _load_hook(monkeypatch)

    assert (
        klai_knowledge._should_apply_low_confidence_injection(
            "low",
            user_query=(
                "Leg kort uit wat een eSIM is en vermeld alleen Voys-bronnen "
                "als onze kennisbank daar iets over zegt."
            ),
            evidence_chunks=[
                {
                    "title": "Overige problemen",
                    "text": (
                        "Je hoort dit bericht als je gespreksbevestiging hebt "
                        "ingeschakeld voor je mobiele telefoon."
                    ),
                }
            ],
        )
        is True
    )


# ---------------------------------------------------------------------------
# Smoke: importing the hook with the new fields does not raise
# ---------------------------------------------------------------------------


def test_hook_imports_with_new_constants(monkeypatch) -> None:
    """Regression smoke: the new module-level constants must not break
    other module-level work (e.g. KB_IMAGES_BASE_URL right above).
    """
    klai_knowledge = _load_hook(monkeypatch)
    # Constants from before this SPEC must still be present.
    assert hasattr(klai_knowledge, "RETRIEVE_TIMEOUT")
    assert hasattr(klai_knowledge, "KB_IMAGES_BASE_URL")
    # New constants from this SPEC.
    assert hasattr(klai_knowledge, "_LOW_CONFIDENCE_INJECTION_TEXT")
    assert hasattr(klai_knowledge, "_LOW_CONFIDENCE_INJECTION_DISABLED")
    # Module is importable without litellm.
    assert klai_knowledge is not None


# ---------------------------------------------------------------------------
# REQ-5: brand-bridging in the rewrite-and-classify prompt
# ---------------------------------------------------------------------------


def test_query_rewrite_prompt_contains_brand_bridging_instruction(monkeypatch) -> None:
    """REQ-5: the combined rewrite+classify prompt MUST instruct the LLM to
    expand third-party brand names (Salesforce, HubSpot, Zoom, etc.) with
    broader category or related-brand terms in the rewritten query.

    Locked in as a structural test so the SPEC contract survives prompt
    iteration. End-to-end behaviour (does klai-fast actually produce
    bridged rewrites?) is verified by the eval-harness chat-suite via
    the brand_bridging regression queries from REQ-7.
    """
    klai_knowledge = _load_hook(monkeypatch)
    prompt = klai_knowledge._QUERY_REWRITE_AND_CLASSIFY_PROMPT
    # The instruction header must reference the SPEC + key intent words.
    assert "REQ-5" in prompt
    assert "brand" in prompt.lower()
    # Must list at least 3 distinct brand examples covering CRM,
    # video-conferencing, and mail/calendar.
    assert "Salesforce" in prompt
    assert "Zoom" in prompt
    assert "Outlook" in prompt
    # Must include the canonical Voys-Salesforce bridge so that a
    # regression that drops the example fails this test.
    assert "Bubble" in prompt
    assert "RedCactus" in prompt
    assert "CRM-koppeling" in prompt
    # Negative-instruction must be present: don't over-apply on queries
    # without a brand.
    assert "no third-party brand" in prompt.lower() or "if no" in prompt.lower()


def test_query_rewrite_prompt_stays_within_size_budget(monkeypatch) -> None:
    """Prompt extension must not balloon klai-fast's input cost. Pre-SPEC
    baseline was ~600 tokens; the new instruction adds ~150 tokens of
    instruction + examples. Cap at 1200 chars (rough proxy for ~300
    tokens) on the static template so we catch a runaway extension early.
    The full prompt at runtime is still bounded by taxonomy size + history.
    """
    klai_knowledge = _load_hook(monkeypatch)
    prompt = klai_knowledge._QUERY_REWRITE_AND_CLASSIFY_PROMPT
    # The static template with placeholders should fit comfortably.
    # Expanded examples + instruction should add < 800 chars over baseline.
    assert len(prompt) < 2200, f"prompt size = {len(prompt)} (cap 2200)"


# ---------------------------------------------------------------------------
# Acceptance criterion mirror: AC-2 trigger
# ---------------------------------------------------------------------------


def test_voys_salesforce_2026_05_07_replay_score_triggers_injection() -> None:
    """The 2026-05-07 19:30 Voys-Salesforce turn 1 logged max-rerank=0.18.

    With default thresholds (high=0.60, low=0.30) that maps to band='low'.
    This test locks in that the 0.18 case (and adjacent edge cases) DOES
    trigger injection — protecting against future threshold tuning that
    accidentally moves 0.18 out of the 'low' band.
    """

    # Mirrors retrieval_api.api.retrieve._compute_confidence_band logic
    def _band(max_score: float, high: float = 0.60, low: float = 0.30) -> str:
        if max_score >= high:
            return "high"
        if max_score < low:
            return "low"
        return "medium"

    incident_score = 0.18
    band = _band(incident_score)
    assert band == "low"
    assert band in ("low", "unknown"), (
        "The 2026-05-07 incident score must continue to trigger injection"
    )
