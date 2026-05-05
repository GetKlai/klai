"""Pin the SPEC-RAG-EVAL-001 config defaults + alias resolution.

Regression cover for the post-merge production smoke test where:
- the default `retrieval_api_url` pointed at a non-existent hostname
- `retrieval_internal_secret` only honoured RETRIEVAL_INTERNAL_SECRET, not the
  production-side RETRIEVAL_API_INTERNAL_SECRET
"""

from __future__ import annotations

import os

import pytest

from knowledge_ingest.config import Settings


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip the two secret env vars so each test starts from a clean slate."""
    monkeypatch.delenv("RETRIEVAL_INTERNAL_SECRET", raising=False)
    monkeypatch.delenv("RETRIEVAL_API_INTERNAL_SECRET", raising=False)
    monkeypatch.delenv("RETRIEVAL_API_URL", raising=False)
    # KNOWLEDGE_INGEST_SECRET is required by the service-wide validator.
    monkeypatch.setenv("KNOWLEDGE_INGEST_SECRET", "test-secret-for-config-tests")


def test_retrieval_api_url_default_matches_production() -> None:
    """Default URL must match the production hostname:port (verified 2026-05-04)."""
    settings = Settings(_env_file=None)
    assert settings.retrieval_api_url == "http://retrieval-api:8040"


def test_rag_eval_judge_model_default() -> None:
    """Light-metrics + answer-generation default to klai-fast (Mistral Small)."""
    settings = Settings(_env_file=None)
    assert settings.rag_eval_judge_model == "klai-fast"


def test_rag_eval_faithfulness_model_default() -> None:
    """Faithfulness defaults to klai-eval-judge (Mistral Large) to avoid the
    Mistral Small max-tokens truncation that left ~28/30 rows NaN at v1.
    """
    settings = Settings(_env_file=None)
    assert settings.rag_eval_faithfulness_model == "klai-eval-judge"


def test_rag_eval_embeddings_model_default() -> None:
    """AnswerRelevancy embeddings default to klai-embeddings (BGE-M3 via TEI)."""
    settings = Settings(_env_file=None)
    assert settings.rag_eval_embeddings_model == "klai-embeddings"


def test_retrieval_internal_secret_via_canonical_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RETRIEVAL_INTERNAL_SECRET wins when set."""
    monkeypatch.setenv("RETRIEVAL_INTERNAL_SECRET", "sec-canonical")
    settings = Settings(_env_file=None)
    assert settings.retrieval_internal_secret == "sec-canonical"


def test_retrieval_internal_secret_via_production_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RETRIEVAL_API_INTERNAL_SECRET (production SOPS name) is also honoured."""
    monkeypatch.setenv("RETRIEVAL_API_INTERNAL_SECRET", "sec-prod-alias")
    settings = Settings(_env_file=None)
    assert settings.retrieval_internal_secret == "sec-prod-alias"


def test_retrieval_internal_secret_canonical_wins_over_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When both are set, the canonical name takes precedence (deterministic)."""
    monkeypatch.setenv("RETRIEVAL_INTERNAL_SECRET", "sec-canonical")
    monkeypatch.setenv("RETRIEVAL_API_INTERNAL_SECRET", "sec-prod-alias")
    settings = Settings(_env_file=None)
    assert settings.retrieval_internal_secret == "sec-canonical"


def test_retrieval_internal_secret_empty_when_neither_set() -> None:
    """No env var set: defaults to empty string (warn-on-empty at startup)."""
    assert "RETRIEVAL_INTERNAL_SECRET" not in os.environ
    assert "RETRIEVAL_API_INTERNAL_SECRET" not in os.environ
    settings = Settings(_env_file=None)
    assert settings.retrieval_internal_secret == ""
