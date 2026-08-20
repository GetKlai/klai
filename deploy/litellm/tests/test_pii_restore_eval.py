"""Unit tests for klai_pii_restore_eval — the pure, network-free core of the
Phase 0 PII-restore measurement harness (SPEC-PRIVACY-MISTRAL-PII-001
REQ-0a/REQ-0b).

These test the harness's OWN logic (restore-outcome classification, the
Dutch drafting-prompt corpus, token-survival classification, aggregation)
with no network calls. The live invocation
(deploy/litellm/scripts/eval_pii_restore_live.py) is a separate, manually
run, opt-in script — it cannot run in normal CI because it needs real
Mistral quota and a reachable Presidio guardrail, same constraint as the
existing correspondence-eval harness.
"""

from __future__ import annotations

import pytest

from klai_pii_restore_eval import (
    RestoreProbe,
    SurvivalSample,
    VERBATIM_TOKEN_SYSTEM_INSTRUCTION,
    classify_restore_outcome,
    classify_token_survival,
    dutch_drafting_prompts,
    summarize_restore_probes,
    summarize_token_survival,
)


class TestClassifyRestoreOutcome:
    def test_exact_match_when_original_value_present(self):
        outcome = classify_restore_outcome(
            original_value="Jan de Vries",
            response_text="Beste Jan de Vries, bedankt voor uw bericht.",
            entity_type="PERSON",
        )
        assert outcome == "exact_match"

    def test_empty_map_when_placeholder_still_visible(self):
        outcome = classify_restore_outcome(
            original_value="Jan de Vries",
            response_text="Beste <PERSON_1>, bedankt voor uw bericht.",
            entity_type="PERSON",
        )
        assert outcome == "empty_map"

    def test_empty_map_matches_unnumbered_placeholder_case_insensitively(self):
        outcome = classify_restore_outcome(
            original_value="Jan de Vries",
            response_text="Beste <person>, bedankt.",
            entity_type="PERSON",
        )
        assert outcome == "empty_map"

    def test_corrupted_when_neither_original_nor_placeholder_present(self):
        # #6247's own example shape: the map fired but restored garbage.
        outcome = classify_restore_outcome(
            original_value="Jan de Vries",
            response_text="Beste Mike. Wh, bedankt voor uw bericht.",
            entity_type="PERSON",
        )
        assert outcome == "corrupted"

    def test_raises_on_empty_response(self):
        with pytest.raises(ValueError, match="empty response"):
            classify_restore_outcome(
                original_value="Jan de Vries", response_text="   ", entity_type="PERSON"
            )


class TestRestoreProbe:
    def test_outcome_property_delegates_to_classifier(self):
        probe = RestoreProbe(
            mode="streaming",
            entity_type="PHONE_NUMBER",
            original_value="06-12345678",
            response_text="Bel 06-12345678 voor meer info.",
        )
        assert probe.outcome == "exact_match"


class TestSummarizeRestoreProbes:
    def test_all_pass_true_when_every_probe_matches(self):
        probes = [
            RestoreProbe("non_streaming", "PERSON", "Jan de Vries", "Jan de Vries"),
            RestoreProbe("streaming", "PERSON", "Jan de Vries", "Jan de Vries"),
        ]
        summary = summarize_restore_probes(probes)
        assert summary["all_pass"] is True
        assert summary["failures"] == []

    def test_all_pass_false_and_failures_listed_on_any_mismatch(self):
        probes = [
            RestoreProbe("non_streaming", "PERSON", "Jan de Vries", "Jan de Vries"),
            RestoreProbe("streaming", "PERSON", "Jan de Vries", "<PERSON_1>"),
        ]
        summary = summarize_restore_probes(probes)
        assert summary["all_pass"] is False
        assert len(summary["failures"]) == 1
        assert summary["failures"][0]["mode"] == "streaming"
        assert summary["failures"][0]["outcome"] == "empty_map"

    def test_raises_on_empty_probe_list(self):
        with pytest.raises(ValueError, match="at least one probe"):
            summarize_restore_probes([])

    def test_not_masked_probe_counts_as_failure_not_silent_pass(self):
        # Sol delta-review finding: an entity the analyzer never detected
        # never exercised restore at all — must not count as evidence of
        # anything, and specifically must not silently pass.
        probes = [
            RestoreProbe(
                "non_streaming",
                "PERSON",
                "Jan de Vries",
                "Jan de Vries",
                masked_by_analyzer=False,
            ),
        ]
        summary = summarize_restore_probes(probes)
        assert summary["all_pass"] is False
        assert summary["failures"][0]["outcome"] == "not_masked"


class TestRestoreProbeMaskedByAnalyzerPrecondition:
    def test_not_masked_outcome_when_analyzer_never_detected_entity(self):
        # Even though the original value IS present (it reached the model
        # unmasked and was echoed back), this must NOT be scored as
        # exact_match — no restore mechanism was exercised.
        probe = RestoreProbe(
            mode="non_streaming",
            entity_type="PERSON",
            original_value="Jan de Vries",
            response_text="Beste Jan de Vries, bedankt.",
            masked_by_analyzer=False,
        )
        assert probe.outcome == "not_masked"

    def test_defaults_to_masked_true_for_backward_compatible_call_sites(self):
        probe = RestoreProbe(
            mode="non_streaming",
            entity_type="PERSON",
            original_value="Jan de Vries",
            response_text="Jan de Vries",
        )
        assert probe.outcome == "exact_match"


class TestDutchDraftingPrompts:
    def test_at_least_thirty_prompts(self):
        prompts = dutch_drafting_prompts()
        assert len(prompts) >= 30

    def test_every_prompt_has_unique_id(self):
        prompts = dutch_drafting_prompts()
        ids = [p.id for p in prompts]
        assert len(ids) == len(set(ids))

    def test_every_prompt_contains_its_person_and_phone(self):
        for prompt in dutch_drafting_prompts():
            assert prompt.person in prompt.text
            assert prompt.phone in prompt.text

    def test_every_prompt_mentions_person_and_phone_exactly_once(self):
        # Sol delta-review finding: Presidio numbers placeholders per
        # detected SPAN, so a name repeated twice in one prompt becomes two
        # DISTINCT placeholders (<PERSON_1>, <PERSON_2>) — a survival check
        # based on substring presence alone would then pass even if only
        # one of the two round-tripped. Prompts must not repeat the entity.
        for prompt in dutch_drafting_prompts():
            assert prompt.text.count(prompt.person) == 1, prompt.id
            assert prompt.text.count(prompt.phone) == 1, prompt.id

    def test_covers_all_three_drafting_shapes(self):
        categories = {p.category for p in dutch_drafting_prompts()}
        assert categories == {"write_email", "summarize_call", "draft_reply"}


class TestVerbatimTokenSystemInstruction:
    def test_mentions_all_three_stock_entity_placeholders(self):
        for token in ("PERSON_1", "PHONE_NUMBER_1", "EMAIL_ADDRESS_1"):
            assert token in VERBATIM_TOKEN_SYSTEM_INSTRUCTION


class TestClassifyTokenSurvival:
    def test_survived_when_original_value_present(self):
        outcome = classify_token_survival(
            response_text="Beste Jan de Vries, hierbij de bevestiging.",
            entity_type="PERSON",
            original_value="Jan de Vries",
        )
        assert outcome == "survived"

    def test_survived_when_exact_wellformed_placeholder_present(self):
        outcome = classify_token_survival(
            response_text="Beste <PERSON_1>, hierbij de bevestiging.",
            entity_type="PERSON",
            original_value="Jan de Vries",
        )
        assert outcome == "survived"

    def test_altered_not_survived_when_placeholder_case_differs(self):
        # Sol delta-review finding, verified against the actual LiteLLM
        # v1.96.2 source (_unmask_pii_text: plain, case-sensitive
        # str.replace): a case-varied placeholder would NOT have been
        # restored by the real mechanism, so the client genuinely sees the
        # mangled token — this is "altered", not "survived".
        outcome = classify_token_survival(
            response_text="Beste <person_1>, hierbij de bevestiging.",
            entity_type="PERSON",
            original_value="Jan de Vries",
        )
        assert outcome == "altered"

    def test_altered_not_survived_when_placeholder_has_extra_whitespace(self):
        outcome = classify_token_survival(
            response_text="Beste < PERSON_1 >, hierbij de bevestiging.",
            entity_type="PERSON",
            original_value="Jan de Vries",
        )
        assert outcome == "altered"

    def test_not_masked_when_analyzer_never_detected_the_entity(self):
        outcome = classify_token_survival(
            response_text="Beste Jan de Vries, hierbij de bevestiging.",
            entity_type="PERSON",
            original_value="Jan de Vries",
            masked_by_analyzer=False,
        )
        assert outcome == "not_masked"

    def test_paraphrase_marker_is_scoped_to_its_own_entity_type(self):
        # Sol delta-review finding: a PERSON-only paraphrase marker must
        # not also mark an unrelated PHONE_NUMBER check as "paraphrased"
        # when the number was simply dropped.
        response_text = "Beste deze persoon, we nemen contact op."
        person_outcome = classify_token_survival(
            response_text=response_text,
            entity_type="PERSON",
            original_value="Jan de Vries",
        )
        phone_outcome = classify_token_survival(
            response_text=response_text,
            entity_type="PHONE_NUMBER",
            original_value="06-12345678",
        )
        assert person_outcome == "paraphrased"
        assert phone_outcome == "not_returned"

    def test_altered_when_placeholder_missing_a_bracket(self):
        outcome = classify_token_survival(
            response_text="Beste PERSON_1>, hierbij de bevestiging.",
            entity_type="PERSON",
            original_value="Jan de Vries",
        )
        assert outcome == "altered"

    def test_altered_when_placeholder_has_no_brackets_at_all(self):
        outcome = classify_token_survival(
            response_text="Beste PERSON_1, hierbij de bevestiging.",
            entity_type="PERSON",
            original_value="Jan de Vries",
        )
        assert outcome == "altered"

    def test_paraphrased_when_generic_referent_used(self):
        outcome = classify_token_survival(
            response_text="Beste deze persoon, hierbij de bevestiging.",
            entity_type="PERSON",
            original_value="Jan de Vries",
        )
        assert outcome == "paraphrased"

    def test_not_returned_when_nothing_recognisable_remains(self):
        outcome = classify_token_survival(
            response_text="Bedankt voor uw bericht, we nemen spoedig contact op.",
            entity_type="PERSON",
            original_value="Jan de Vries",
        )
        assert outcome == "not_returned"

    def test_not_returned_on_empty_response(self):
        outcome = classify_token_survival(
            response_text="", entity_type="PERSON", original_value="Jan de Vries"
        )
        assert outcome == "not_returned"


class TestSummarizeTokenSurvival:
    def test_survival_rate_and_bucket_counts(self):
        samples = [
            SurvivalSample("p1", "with_instruction", "PERSON", "survived"),
            SurvivalSample("p2", "with_instruction", "PERSON", "survived"),
            SurvivalSample("p3", "with_instruction", "PERSON", "not_returned"),
            SurvivalSample("p4", "with_instruction", "PERSON", "altered"),
        ]
        report = summarize_token_survival(samples)
        stats = report["PERSON"]["with_instruction"]
        assert stats["total"] == 4
        assert stats["survived"] == 2
        assert stats["survival_rate"] == pytest.approx(0.5)
        assert stats["not_returned"] == 1
        assert stats["altered"] == 1
        assert stats["paraphrased"] == 0
        assert stats["below_95_percent_gate"] is True

    def test_separates_conditions_and_entity_types_independently(self):
        samples = [
            SurvivalSample("p1", "with_instruction", "PERSON", "survived"),
            SurvivalSample("p1", "without_instruction", "PERSON", "not_returned"),
            SurvivalSample("p1", "with_instruction", "PHONE_NUMBER", "survived"),
        ]
        report = summarize_token_survival(samples)
        assert report["PERSON"]["with_instruction"]["survival_rate"] == pytest.approx(1.0)
        assert report["PERSON"]["without_instruction"]["survival_rate"] == pytest.approx(0.0)
        assert report["PHONE_NUMBER"]["with_instruction"]["survival_rate"] == pytest.approx(1.0)
        assert "without_instruction" not in report["PHONE_NUMBER"]

    def test_gate_false_at_or_above_95_percent(self):
        samples = [
            SurvivalSample(f"p{i}", "with_instruction", "PERSON", "survived")
            for i in range(19)
        ] + [SurvivalSample("p19", "with_instruction", "PERSON", "not_returned")]
        report = summarize_token_survival(samples)
        stats = report["PERSON"]["with_instruction"]
        assert stats["survival_rate"] == pytest.approx(0.95)
        assert stats["below_95_percent_gate"] is False

    def test_raises_on_empty_sample_list(self):
        with pytest.raises(ValueError, match="at least one sample"):
            summarize_token_survival([])

    def test_not_masked_samples_excluded_from_survival_rate_denominator(self):
        # Sol delta-review finding: a not_masked sample never exercised
        # survival at all — counting it in the rate would understate
        # survival for a reason unrelated to the model's behaviour.
        samples = [
            SurvivalSample("p1", "with_instruction", "PERSON", "survived"),
            SurvivalSample("p2", "with_instruction", "PERSON", "survived"),
            SurvivalSample("p3", "with_instruction", "PERSON", "not_masked"),
        ]
        report = summarize_token_survival(samples)
        stats = report["PERSON"]["with_instruction"]
        assert stats["total"] == 3
        assert stats["scored_total"] == 2
        assert stats["not_masked"] == 1
        assert stats["survived"] == 2
        assert stats["survival_rate"] == pytest.approx(1.0)
        assert stats["below_95_percent_gate"] is False

    def test_survival_rate_is_none_when_every_sample_is_not_masked(self):
        samples = [
            SurvivalSample("p1", "with_instruction", "PERSON", "not_masked"),
            SurvivalSample("p2", "with_instruction", "PERSON", "not_masked"),
        ]
        report = summarize_token_survival(samples)
        stats = report["PERSON"]["with_instruction"]
        assert stats["scored_total"] == 0
        assert stats["survival_rate"] is None
        # None must never look like a passing gate.
        assert stats["below_95_percent_gate"] is True
