"""Unit tests for klai_correspondence_eval — the pure, fast-CI-testable core
of the pasted-correspondence distillation eval harness (SPEC-RAG-
CORRESPONDENCE-DISTILL-001 REQ-6/AC-6).

These test the harness's OWN logic (canary loading, chunk matching, pass-
rate aggregation) with no network calls. The live LLM+retrieval-api
invocation (deploy/litellm/scripts/eval_pasted_correspondence_live.py) is a
separate, manually-run, opt-in script — it cannot run in normal CI because
it needs real Mistral quota and network access to retrieval-api's
Docker-internal hostname (same constraint the existing knowledge-ingest eval
harness already has: retrieval_api_url defaults to http://retrieval-api:8040).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from klai_correspondence_eval import (
    CorrespondenceCanary,
    answer_shape_matches_expectation,
    chunk_matches_expected,
    load_pasted_correspondence_canaries,
    summarize_canary_samples,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CHAT_YAML = (
    _REPO_ROOT
    / "klai-knowledge-ingest"
    / "knowledge_ingest"
    / "eval"
    / "suites"
    / "chat.yaml"
)


class TestLoadPastedCorrespondenceCanaries:
    def test_loads_exactly_the_three_shipped_canaries(self):
        canaries = load_pasted_correspondence_canaries(_CHAT_YAML)

        ids = {c.id for c in canaries}
        assert ids == {
            "chat-pasted-correspondence-incident-shape",
            "chat-pasted-correspondence-short-ticket",
            "chat-pasted-correspondence-control-plain-question",
        }

    def test_each_canary_carries_org_id_and_query(self):
        canaries = load_pasted_correspondence_canaries(_CHAT_YAML)

        for canary in canaries:
            assert canary.org_zitadel_id == "368884765035593759"
            assert canary.query.strip()

    def test_incident_canary_replays_sender_claims_and_expected_chunks(self):
        canaries = load_pasted_correspondence_canaries(_CHAT_YAML)
        by_id = {c.id: c for c in canaries}

        incident = by_id["chat-pasted-correspondence-incident-shape"]
        assert incident.expected_chunks == [
            "Gebruiker/toestel bestaat niet, of extensie niet gevonden"
        ]
        for claim in (
            "fraudedetectiesysteem",
            "5 van 5 geselecteerd",
            "alles geverifieerd correct",
            "routerings- of belmachtiging-logica",
            "<CALL_ID_A>",
            "<CALL_ID_B>",
        ):
            assert claim in incident.query

    def test_answer_shape_expectations_cover_positive_and_negative_canaries(self):
        canaries = load_pasted_correspondence_canaries(_CHAT_YAML)
        by_id = {canary.id: canary for canary in canaries}

        expected_sections = [
            "sender_statements",
            "kb_evidence",
            "open_questions",
            "verify_first",
        ]
        assert (
            by_id["chat-pasted-correspondence-incident-shape"].expected_answer_sections
            == expected_sections
        )
        assert (
            by_id["chat-pasted-correspondence-short-ticket"].expected_answer_sections
            == expected_sections
        )
        assert (
            by_id[
                "chat-pasted-correspondence-control-plain-question"
            ].expected_answer_sections
            == []
        )

    def test_missing_file_raises_file_not_found(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_pasted_correspondence_canaries(tmp_path / "does-not-exist.yaml")

    def test_ignores_queries_outside_the_pasted_correspondence_mix(
        self, tmp_path: Path
    ):
        suite = tmp_path / "mini.yaml"
        suite.write_text(
            "suite: mini\n"
            "queries:\n"
            "  - id: other-mix\n"
            "    org_zitadel_id: '1'\n"
            "    query: irrelevant\n"
            "    mix: easy_lookup\n"
            "  - id: only-this-one\n"
            "    org_zitadel_id: '1'\n"
            "    query: relevant\n"
            "    expected_chunks: ['some chunk marker']\n"
            "    expected_answer_sections: []\n"
            "    mix: pasted_correspondence\n",
            encoding="utf-8",
        )

        canaries = load_pasted_correspondence_canaries(suite)

        assert [c.id for c in canaries] == ["only-this-one"]

    def test_canary_without_expected_chunks_raises_value_error(self, tmp_path: Path):
        """A pasted_correspondence canary with no expected_chunks can never
        fail — chunk_matches_expected has nothing to check against — so it
        vacuously passes every eval run regardless of retrieval quality.
        Fail loudly at load time instead of silently shipping a canary that
        can never catch a regression."""
        suite = tmp_path / "mini.yaml"
        suite.write_text(
            "suite: mini\n"
            "queries:\n"
            "  - id: has-expected-chunks\n"
            "    org_zitadel_id: '1'\n"
            "    query: relevant\n"
            "    expected_chunks: ['some chunk marker']\n"
            "    expected_answer_sections: []\n"
            "    mix: pasted_correspondence\n"
            "  - id: missing-expected-chunks\n"
            "    org_zitadel_id: '1'\n"
            "    query: relevant\n"
            "    expected_answer_sections: []\n"
            "    mix: pasted_correspondence\n",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="missing-expected-chunks"):
            load_pasted_correspondence_canaries(suite)

    def test_canary_without_answer_shape_assertion_raises_value_error(
        self, tmp_path: Path
    ):
        suite = tmp_path / "mini.yaml"
        suite.write_text(
            "suite: mini\n"
            "queries:\n"
            "  - id: missing-shape-assertion\n"
            "    org_zitadel_id: '1'\n"
            "    query: relevant\n"
            "    expected_chunks: ['some chunk marker']\n"
            "    mix: pasted_correspondence\n",
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="missing-shape-assertion"):
            load_pasted_correspondence_canaries(suite)


class TestChunkMatchesExpected:
    def test_matches_on_title_case_insensitive_substring(self):
        chunk = {"title": "SIP Response Codes — 4xx overzicht", "text": "..."}
        assert chunk_matches_expected("sip response codes", chunk) is True

    def test_matches_on_body_text_when_specific_enough(self):
        chunk = {
            "title": "Onbekende titel",
            "text": "404 | Not Found | Gebruiker/toestel bestaat niet, of extensie niet gevonden",
        }
        assert (
            chunk_matches_expected(
                "Gebruiker/toestel bestaat niet, of extensie niet gevonden", chunk
            )
            is True
        )

    def test_does_not_match_unrelated_chunk(self):
        chunk = {"title": "FreePBX Uitgaand Bellen", "text": "Outbound routes..."}
        assert (
            chunk_matches_expected(
                "Gebruiker/toestel bestaat niet, of extensie niet gevonden", chunk
            )
            is False
        )

    def test_short_generic_marker_does_not_match_body_only(self):
        """Mirrors ragas_runner's _canary_allows_body_match guard: short
        markers must hit a strong field (title/url), not any body text —
        otherwise near-every chunk containing the word "SIP" would match."""
        chunk = {"title": "Onbekend", "text": "dit gaat over SIP en meer SIP-dingen"}
        assert chunk_matches_expected("SIP", chunk) is False


class TestSummarizeCanarySamples:
    def test_all_pass(self):
        summary = summarize_canary_samples("id-1", [True, True, True])
        assert summary["pass_rate"] == 1.0
        assert summary["passed"] == 3
        assert summary["total"] == 3
        assert summary["majority_pass"] is True

    def test_majority_pass_with_mixed_results(self):
        summary = summarize_canary_samples("id-1", [True, False, True])
        assert summary["pass_rate"] == pytest.approx(2 / 3)
        assert summary["majority_pass"] is True

    def test_majority_fail(self):
        summary = summarize_canary_samples("id-1", [False, False, True])
        assert summary["majority_pass"] is False

    def test_empty_samples_raises(self):
        with pytest.raises(ValueError, match="at least one sample"):
            summarize_canary_samples("id-1", [])


class TestCorrespondenceCanaryDataclass:
    def test_defaults_expected_chunks_to_empty_list(self):
        canary = CorrespondenceCanary(
            id="x", org_zitadel_id="1", query="q", expected_chunks=[]
        )
        assert canary.expected_chunks == []


class TestAnswerShapeExpectation:
    def test_positive_canary_requires_satisfied_contract(self):
        canary = CorrespondenceCanary(
            id="x",
            org_zitadel_id="1",
            query="q",
            expected_chunks=["marker"],
            expected_answer_sections=[
                "sender_statements",
                "kb_evidence",
                "open_questions",
                "verify_first",
            ],
        )

        assert answer_shape_matches_expectation(
            canary, {"answer_contract": {"satisfied": True}}
        )
        assert not answer_shape_matches_expectation(
            canary, {"answer_contract": {"satisfied": False}}
        )

    def test_negative_control_requires_contract_to_be_absent(self):
        canary = CorrespondenceCanary(
            id="control",
            org_zitadel_id="1",
            query="q",
            expected_chunks=["marker"],
            expected_answer_sections=[],
        )

        assert answer_shape_matches_expectation(canary, {})
        assert not answer_shape_matches_expectation(
            canary, {"answer_contract": {"satisfied": True}}
        )
        assert not answer_shape_matches_expectation(
            canary,
            {},
            raw_answer="[[KLAI_CORRESPONDENCE_SENDER_STATEMENTS]] leaked",
        )
