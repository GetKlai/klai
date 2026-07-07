"""SPEC-RAG-EVIDENCE-INTEGRITY-001 REQ-RANK — ranking-contract unit tests.

Covers the helpers that carry the contract:
- ``ranking_score`` — the single owner of the score precedence (REQ-RANK-01),
  including the shadow-preserving fallback semantics.
- ``_set_final_rank_scores`` — active-mode stamping (reranker fallback score).
- ``_ranking_contract_snapshot`` — the shadow-diff projection, which must
  mirror ``build_evidence_pack``'s DISTINCT source-slot assignment.
- Settings validation of ``ranking_contract_mode``.
"""

import pytest

from retrieval_api.api.retrieve import (
    _ranking_contract_snapshot,
    _set_final_rank_scores,
)
from retrieval_api.config import Settings
from retrieval_api.util.scores import ranking_score


class TestRankingScore:
    def test_prefers_final_rank_score(self) -> None:
        chunk = {"final_rank_score": 0.9, "reranker_score": 0.5, "score": 0.1}
        assert ranking_score(chunk, "reranker_score", "score") == 0.9

    def test_falls_back_in_caller_order_when_final_absent(self) -> None:
        chunk = {"reranker_score": 0.5, "score": 0.1}
        assert ranking_score(chunk, "reranker_score", "score") == 0.5
        assert ranking_score(chunk, "score") == 0.1

    def test_zero_is_a_valid_score_not_a_missing_one(self) -> None:
        """isinstance-based on purpose: 0.0 must NOT fall through like the
        old ``or``-chains did."""
        chunk = {"final_rank_score": 0.0, "reranker_score": 0.9}
        assert ranking_score(chunk, "reranker_score") == 0.0

    def test_none_and_missing_fall_through_to_default(self) -> None:
        assert ranking_score({"reranker_score": None}, "reranker_score") == 0.0
        assert ranking_score({}, "reranker_score", "score") == 0.0


class TestSetFinalRankScores:
    def test_stamps_reranker_score_when_present(self) -> None:
        chunks = [{"reranker_score": 0.8, "score": 0.02}]
        _set_final_rank_scores(chunks)
        assert chunks[0]["final_rank_score"] == 0.8

    def test_falls_back_to_score_on_reranker_fallback(self) -> None:
        """REQ-RANK-05: reranker failure → final_rank_score = score, so the
        active ordering equals the current score ordering."""
        chunks = [{"reranker_score": None, "score": 0.02}]
        _set_final_rank_scores(chunks)
        assert chunks[0]["final_rank_score"] == 0.02


class TestRankingContractSnapshot:
    def test_source_keys_are_distinct_and_capped_at_three(self) -> None:
        """Two chunks of the same source must occupy ONE source slot —
        first-N raw source_urls would double-count multi-chunk sources."""
        chunks = [
            {"chunk_id": "c1", "source_url": "https://help.voys.nl/integraties"},
            {"chunk_id": "c2", "source_url": "https://help.voys.nl/integraties"},
            {"chunk_id": "c3", "source_url": "https://wiki.redcactus.cloud/nl/x"},
            {"chunk_id": "c4", "source_url": "https://help.voys.nl/webhooks"},
            {"chunk_id": "c5", "source_url": "https://example.com/never-reached"},
            {"chunk_id": "c6", "source_url": "https://example.com/other"},
        ]
        snapshot = _ranking_contract_snapshot(chunks)
        assert snapshot["top5_chunk_ids"] == ["c1", "c2", "c3", "c4", "c5"]
        assert len(snapshot["evidence_source_keys"]) == 3
        assert len(set(snapshot["evidence_source_keys"])) == 3

    def test_url_less_upload_counts_via_artifact_key(self) -> None:
        """Private uploads have no URL; they must still occupy a source slot
        (mirrors build_evidence_pack's artifact fallback)."""
        chunks = [
            {"chunk_id": "c1", "artifact_id": "doc-123"},
            {"chunk_id": "c2", "source_url": "https://help.voys.nl/integraties"},
        ]
        snapshot = _ranking_contract_snapshot(chunks)
        assert snapshot["evidence_source_keys"][0] == "artifact:doc-123"
        assert len(snapshot["evidence_source_keys"]) == 2


class TestRankingContractModeSetting:
    def test_default_is_shadow(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RANKING_CONTRACT_MODE", raising=False)
        assert Settings().ranking_contract_mode == "shadow"

    def test_active_is_normalized(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RANKING_CONTRACT_MODE", " Active ")
        assert Settings().ranking_contract_mode == "active"

    def test_invalid_value_fails_loud_at_boot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A typo must never silently degrade to shadow — that would defeat
        an intentional activation (validator-env-parity discipline)."""
        monkeypatch.setenv("RANKING_CONTRACT_MODE", "actief")
        with pytest.raises(ValueError, match="ranking_contract_mode"):
            Settings()
