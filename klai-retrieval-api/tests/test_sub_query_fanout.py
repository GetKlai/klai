"""Multi-part retrieval fan-out: per-sub-question retrieval + merged evidence.

One retrieval pass over an 11-question message cannot cover 11 topics — the
2026-08-17 incident showed the model then interpolates. With ``sub_queries``
the endpoint runs the full pipeline per sub-question and returns a merged,
referentially-intact evidence pack whose items carry ``sub_query_index``,
plus per-question coverage (``sub_results``). Failed sub-questions surface as
errors, never as "not in the knowledge base".
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from retrieval_api.api import retrieve as retrieve_module
from retrieval_api.models import (
    ChunkResult,
    EvidenceItem,
    EvidencePack,
    EvidenceSource,
    RetrieveMetadata,
    RetrieveRequest,
    RetrieveResponse,
)
from retrieval_api.services.evidence_pack import merge_evidence_packs


def _item(
    evidence_id: str, chunk_id: str, url: str | None = "https://example.test/a"
) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        chunk_id=chunk_id,
        text=f"tekst {chunk_id}",
        title="Voorbeeldbron",
        source_url=url,
        score=0.5,
        reranker_score=0.5,
    )


def _source(
    source_id: str, evidence_ids: list[str], url: str | None = "https://example.test/a"
) -> EvidenceSource:
    return EvidenceSource(
        source_id=source_id,
        title="Voorbeeldbron",
        source_url=url,
        evidence_ids=evidence_ids,
        relevance_score=0.5,
    )


class TestMergeEvidencePacks:
    def test_namespaces_ids_and_tags_sub_query_index(self):
        pack1 = EvidencePack(items=[_item("E1", "c1")], sources=[_source("S1", ["E1"])])
        pack2 = EvidencePack(
            items=[_item("E1", "c2", url="https://example.test/b")],
            sources=[_source("S1", ["E1"], url="https://example.test/b")],
        )

        merged = merge_evidence_packs([(1, pack1), (2, pack2)])

        assert [item.evidence_id for item in merged.items] == ["Q1E1", "Q2E1"]
        assert [item.sub_query_index for item in merged.items] == [1, 2]
        assert [source.source_id for source in merged.sources] == ["Q1S1", "Q2S1"]
        assert merged.sources[0].evidence_ids == ["Q1E1"]
        assert merged.sources[1].evidence_ids == ["Q2E1"]
        assert merged.no_citable_reason is None

    def test_same_source_across_sub_queries_is_deduplicated(self):
        pack1 = EvidencePack(items=[_item("E1", "c1")], sources=[_source("S1", ["E1"])])
        pack2 = EvidencePack(items=[_item("E1", "c2")], sources=[_source("S1", ["E1"])])

        merged = merge_evidence_packs([(1, pack1), (2, pack2)])

        assert len(merged.sources) == 1
        assert merged.sources[0].evidence_ids == ["Q1E1", "Q2E1"]

    def test_empty_input_yields_no_evidence(self):
        merged = merge_evidence_packs([(1, None), (2, EvidencePack())])
        assert merged.items == []
        assert merged.no_citable_reason == "no_evidence"


def _request(sub_queries: list[str]) -> RetrieveRequest:
    return RetrieveRequest(
        query="volledige meerdelige vraag",
        org_id="42",
        sub_queries=sub_queries,
        kb_narrow=True,
    )


def _response(
    band: str | None,
    items: list[EvidenceItem],
    sources: list[EvidenceSource],
) -> RetrieveResponse:
    return RetrieveResponse(
        query_resolved="q",
        retrieval_bypassed=False,
        chunks=[
            ChunkResult(chunk_id=item.chunk_id, text=item.text, score=item.score) for item in items
        ],
        metadata=RetrieveMetadata(candidates_retrieved=10, reranked_to=4, retrieval_ms=12.0),
        confidence_band=band,
        evidence_pack=EvidencePack(items=items, sources=sources),
    )


class TestRetrieveSubQueries:
    @pytest.mark.asyncio
    async def test_fans_out_and_reports_per_question_coverage(self, monkeypatch):
        responses = {
            "vraag over meldingen": _response(
                "high", [_item("E1", "c-meldingen")], [_source("S1", ["E1"])]
            ),
            "vraag over responstijd": _response("low", [], []),
        }

        async def fake_retrieve(sub_req, request, _auth=None):
            return responses[sub_req.query]

        monkeypatch.setattr(retrieve_module, "retrieve", fake_retrieve)

        result = await retrieve_module._retrieve_sub_queries(
            _request(["vraag over meldingen", "vraag over responstijd"]),
            MagicMock(),
            MagicMock(),
        )

        assert [sub.index for sub in result.sub_results] == [1, 2]
        assert result.sub_results[0].confidence_band == "high"
        assert result.sub_results[0].evidence_count == 1
        assert result.sub_results[1].confidence_band == "low"
        assert result.sub_results[1].evidence_count == 0
        assert result.confidence_band == "high"
        assert [item.evidence_id for item in result.evidence_pack.items] == ["Q1E1"]
        assert result.evidence_pack.items[0].sub_query_index == 1
        assert result.retrieval_bypassed is False

    @pytest.mark.asyncio
    async def test_sub_query_uses_bounded_top_k_and_standalone_flags(self, monkeypatch):
        captured = []

        async def fake_retrieve(sub_req, request, _auth=None):
            captured.append(sub_req)
            return _response("medium", [], [])

        monkeypatch.setattr(retrieve_module, "retrieve", fake_retrieve)

        await retrieve_module._retrieve_sub_queries(
            _request(["vraag een", "vraag twee"]), MagicMock(), MagicMock()
        )

        assert all(sub_req.sub_queries is None for sub_req in captured)
        assert all(sub_req.coreference_resolved is True for sub_req in captured)
        assert all(sub_req.raw_query is None for sub_req in captured)
        assert all(
            sub_req.top_k == retrieve_module.settings.sub_query_top_k for sub_req in captured
        )

    @pytest.mark.asyncio
    async def test_failed_sub_query_is_error_not_missing_knowledge(self, monkeypatch):
        calls = {"n": 0}

        async def fake_retrieve(sub_req, request, _auth=None):
            calls["n"] += 1
            if sub_req.query == "kapotte vraag":
                raise RuntimeError("qdrant down")
            return _response("medium", [_item("E1", "c1")], [_source("S1", ["E1"])])

        monkeypatch.setattr(retrieve_module, "retrieve", fake_retrieve)

        result = await retrieve_module._retrieve_sub_queries(
            _request(["goede vraag", "kapotte vraag"]), MagicMock(), MagicMock()
        )

        assert result.sub_results[0].error is None
        assert result.sub_results[1].error == "RuntimeError"
        assert result.sub_results[1].confidence_band is None
        assert len(result.evidence_pack.items) == 1

    @pytest.mark.asyncio
    async def test_all_failures_raise_502(self, monkeypatch):
        async def fake_retrieve(sub_req, request, _auth=None):
            raise RuntimeError("down")

        monkeypatch.setattr(retrieve_module, "retrieve", fake_retrieve)

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await retrieve_module._retrieve_sub_queries(
                _request(["vraag een", "vraag twee"]), MagicMock(), MagicMock()
            )
        assert exc.value.status_code == 502

    @pytest.mark.asyncio
    async def test_duplicate_chunks_across_sub_queries_deduplicated(self, monkeypatch):
        shared = _item("E1", "c-shared")

        async def fake_retrieve(sub_req, request, _auth=None):
            return _response("medium", [shared], [_source("S1", ["E1"])])

        monkeypatch.setattr(retrieve_module, "retrieve", fake_retrieve)

        result = await retrieve_module._retrieve_sub_queries(
            _request(["vraag een", "vraag twee"]), MagicMock(), MagicMock()
        )

        assert len(result.chunks) == 1


class TestRequestModelBounds:
    def test_sub_queries_capped_at_six(self):
        with pytest.raises(ValueError):
            RetrieveRequest(query="q", org_id="1", sub_queries=[f"vraag {i}?" for i in range(7)])

    def test_sub_queries_optional_and_absent_by_default(self):
        req = RetrieveRequest(query="q", org_id="1")
        assert req.sub_queries is None
