"""Multi-part retrieval fan-out: per-sub-question retrieval + merged evidence.

One retrieval pass over an 11-question message cannot cover 11 topics — the
2026-08-17 incident showed the model then interpolates. With ``sub_queries``
the endpoint runs the full pipeline per sub-question and returns a merged,
referentially-intact evidence pack whose items carry ``sub_query_index``,
plus per-question coverage (``sub_results``). Failed sub-questions surface as
errors, never as "not in the knowledge base".
"""

from __future__ import annotations

from types import SimpleNamespace
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
    *,
    retrieval_bypassed: bool = False,
) -> RetrieveResponse:
    return RetrieveResponse(
        query_resolved="q",
        retrieval_bypassed=retrieval_bypassed,
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
    async def test_sub_query_uses_bounded_top_k_and_lets_coreference_run(self, monkeypatch):
        """Round-3 Fix 1: coreference_resolved is None (not True) so
        retrieval-api's own coreference step runs per sub-question against
        the carried conversation_history — a sub-question like "En hoe lang
        duurt het?" needs that resolution to know what "het" refers to."""
        captured = []

        async def fake_retrieve(sub_req, request, _auth=None):
            captured.append(sub_req)
            return _response("medium", [], [])

        monkeypatch.setattr(retrieve_module, "retrieve", fake_retrieve)

        await retrieve_module._retrieve_sub_queries(
            _request(["vraag een", "vraag twee"]), MagicMock(), MagicMock()
        )

        assert all(sub_req.sub_queries is None for sub_req in captured)
        assert all(sub_req.coreference_resolved is None for sub_req in captured)
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
    async def test_sub_query_carries_retrieval_bypassed_flag(self, monkeypatch):
        """Fix B: a gate-bypassed sub-question (Open mode, gate decided no KB
        lookup needed) must be distinguishable from a genuinely empty
        result — never conflated with 'not in the knowledge base'."""

        async def fake_retrieve(sub_req, request, _auth=None):
            if sub_req.query == "meta vraag":
                return _response("unknown", [], [], retrieval_bypassed=True)
            return _response("medium", [_item("E1", "c1")], [_source("S1", ["E1"])])

        monkeypatch.setattr(retrieve_module, "retrieve", fake_retrieve)

        result = await retrieve_module._retrieve_sub_queries(
            _request(["gewone vraag", "meta vraag"]), MagicMock(), MagicMock()
        )

        assert result.sub_results[0].retrieval_bypassed is False
        assert result.sub_results[1].retrieval_bypassed is True

    @pytest.mark.asyncio
    async def test_parent_retrieval_bypassed_true_when_all_sub_queries_bypassed(self, monkeypatch):
        """Round-3 Fix 3: the merged RetrieveResponse's own retrieval_bypassed
        must aggregate the sub-responses instead of being hardcoded False —
        a fully gate-skipped fan-out (Open mode, no sub-question needed KB
        lookup) must report bypassed=True at the parent level too."""

        async def fake_retrieve(sub_req, request, _auth=None):
            return _response("unknown", [], [], retrieval_bypassed=True)

        monkeypatch.setattr(retrieve_module, "retrieve", fake_retrieve)

        result = await retrieve_module._retrieve_sub_queries(
            _request(["vraag een", "vraag twee"]), MagicMock(), MagicMock()
        )

        assert result.retrieval_bypassed is True

    @pytest.mark.asyncio
    async def test_parent_retrieval_bypassed_false_when_mixed(self, monkeypatch):
        async def fake_retrieve(sub_req, request, _auth=None):
            bypassed = sub_req.query == "meta vraag"
            return _response(
                "unknown" if bypassed else "medium", [], [], retrieval_bypassed=bypassed
            )

        monkeypatch.setattr(retrieve_module, "retrieve", fake_retrieve)

        result = await retrieve_module._retrieve_sub_queries(
            _request(["gewone vraag", "meta vraag"]), MagicMock(), MagicMock()
        )

        assert result.retrieval_bypassed is False

    @pytest.mark.asyncio
    async def test_parent_retrieval_bypassed_false_when_none_bypassed(self, monkeypatch):
        async def fake_retrieve(sub_req, request, _auth=None):
            return _response("medium", [], [], retrieval_bypassed=False)

        monkeypatch.setattr(retrieve_module, "retrieve", fake_retrieve)

        result = await retrieve_module._retrieve_sub_queries(
            _request(["vraag een", "vraag twee"]), MagicMock(), MagicMock()
        )

        assert result.retrieval_bypassed is False

    @pytest.mark.asyncio
    async def test_parent_retrieval_bypassed_false_when_one_sub_query_failed(self, monkeypatch):
        """A failed sub-question is not a 'successful bypass' — the parent
        must not report bypassed=True just because the only SUCCESSFUL
        sub-response happened to be bypassed while another one errored."""

        async def fake_retrieve(sub_req, request, _auth=None):
            if sub_req.query == "kapotte vraag":
                raise RuntimeError("down")
            return _response("unknown", [], [], retrieval_bypassed=True)

        monkeypatch.setattr(retrieve_module, "retrieve", fake_retrieve)

        result = await retrieve_module._retrieve_sub_queries(
            _request(["goede vraag", "kapotte vraag"]), MagicMock(), MagicMock()
        )

        # Only one successful sub-response and it WAS bypassed — per the
        # spec (>=1 success AND all successes bypassed), this is True.
        assert result.retrieval_bypassed is True


class TestSubQueryFourXXPassthrough:
    @pytest.mark.asyncio
    async def test_4xx_from_sub_call_is_re_raised_not_masked_as_502(self, monkeypatch):
        """Fix C.2: an auth/validation failure inside a sub-question call is
        a failure of the WHOLE request, not a per-question retrieval miss —
        it must surface with its real status code, not a misleading 502."""
        from fastapi import HTTPException

        async def fake_retrieve(sub_req, request, _auth=None):
            if sub_req.query == "verboden vraag":
                raise HTTPException(status_code=403, detail={"error": "user_mismatch"})
            return _response("medium", [_item("E1", "c1")], [_source("S1", ["E1"])])

        monkeypatch.setattr(retrieve_module, "retrieve", fake_retrieve)

        with pytest.raises(HTTPException) as exc:
            await retrieve_module._retrieve_sub_queries(
                _request(["goede vraag", "verboden vraag"]), MagicMock(), MagicMock()
            )
        assert exc.value.status_code == 403
        assert exc.value.detail == {"error": "user_mismatch"}

    @pytest.mark.asyncio
    async def test_5xx_from_sub_call_still_counts_as_per_question_failure(self, monkeypatch):
        """Server errors keep the existing per-question-failure behaviour —
        only 4xx auth/validation failures escalate to a whole-request raise."""
        from fastapi import HTTPException

        async def fake_retrieve(sub_req, request, _auth=None):
            if sub_req.query == "kapotte vraag":
                raise HTTPException(status_code=503, detail="upstream down")
            return _response("medium", [_item("E1", "c1")], [_source("S1", ["E1"])])

        monkeypatch.setattr(retrieve_module, "retrieve", fake_retrieve)

        result = await retrieve_module._retrieve_sub_queries(
            _request(["goede vraag", "kapotte vraag"]), MagicMock(), MagicMock()
        )
        assert result.sub_results[1].error == "HTTPException"

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

    def test_sub_query_over_500_chars_is_rejected(self):
        """Fix 7: each sub_queries entry is a standalone question, not a
        pasted document — bounded independently of conversation_history."""
        with pytest.raises(ValueError):
            RetrieveRequest(
                query="q",
                org_id="1",
                sub_queries=["a" * 501],
            )

    def test_sub_query_at_exactly_500_chars_is_accepted(self):
        req = RetrieveRequest(
            query="q",
            org_id="1",
            sub_queries=["a" * 500],
        )
        assert req.sub_queries == ["a" * 500]


class TestSubQueryFanoutEmitsSingleEvent:
    @pytest.mark.asyncio
    async def test_emits_exactly_one_knowledge_queried_event(self, monkeypatch):
        """Fix 3: one knowledge.queried product event for the ORIGINAL
        question, not one per sub-question — the nested ``retrieve()`` calls
        see ``klai_sub_query_internal=True`` and must skip their own emit."""
        emitted: list[tuple[str, dict]] = []

        def fake_emit_event(event_type, **kwargs):
            emitted.append((event_type, kwargs))

        monkeypatch.setattr(retrieve_module, "emit_event", fake_emit_event)

        internal_flag_seen: list[bool] = []

        async def fake_retrieve(sub_req, request, _auth=None):
            internal_flag_seen.append(getattr(request.state, "klai_sub_query_internal", False))
            chunk_id = "c-a" if "een" in sub_req.query else "c-b"
            return _response(
                "medium",
                [_item("E1", chunk_id)],
                [_source("S1", ["E1"])],
            )

        monkeypatch.setattr(retrieve_module, "retrieve", fake_retrieve)

        request = MagicMock()
        request.state = SimpleNamespace(
            verified_caller=SimpleNamespace(org_id="org-1", user_id="user-1")
        )

        await retrieve_module._retrieve_sub_queries(
            _request(["vraag een", "vraag twee"]), request, MagicMock()
        )

        # Both nested sub-question calls saw the internal-call flag set.
        assert internal_flag_seen == [True, True]
        # Flag is reset after the fan-out completes (try/finally).
        assert request.state.klai_sub_query_internal is False

        # Exactly one event for the merged original question — not 2.
        assert len(emitted) == 1
        event_type, kwargs = emitted[0]
        assert event_type == "knowledge.queried"
        assert kwargs["tenant_id"] == "org-1"
        assert kwargs["user_id"] == "user-1"
        assert kwargs["properties"]["had_results"] is True
        assert kwargs["properties"]["result_count"] == 2

    @pytest.mark.asyncio
    async def test_skips_event_when_no_verified_identity(self, monkeypatch):
        emitted: list[tuple[str, dict]] = []

        def fake_emit_event(event_type, **kwargs):
            emitted.append((event_type, kwargs))

        monkeypatch.setattr(retrieve_module, "emit_event", fake_emit_event)

        async def fake_retrieve(sub_req, request, _auth=None):
            return _response("medium", [], [])

        monkeypatch.setattr(retrieve_module, "retrieve", fake_retrieve)

        request = MagicMock()
        request.state = SimpleNamespace()  # no verified_caller / verified_tenant
        request.url.path = "/retrieve"

        await retrieve_module._retrieve_sub_queries(
            _request(["vraag een", "vraag twee"]), request, MagicMock()
        )

        assert emitted == []


class TestSubQueryTopKRespectsOne:
    @pytest.mark.asyncio
    async def test_top_k_one_is_not_bumped_to_two(self, monkeypatch):
        """Fix 8: max(1, min(...)) — a caller explicitly asking for top_k=1
        per sub-question must not be silently doubled to 2."""
        captured = []

        async def fake_retrieve(sub_req, request, _auth=None):
            captured.append(sub_req)
            return _response("medium", [], [])

        monkeypatch.setattr(retrieve_module, "retrieve", fake_retrieve)
        monkeypatch.setattr(retrieve_module.settings, "sub_query_top_k", 1)

        await retrieve_module._retrieve_sub_queries(
            _request(["vraag een", "vraag twee"]), MagicMock(), MagicMock()
        )

        assert all(sub_req.top_k == 1 for sub_req in captured)


class TestFanoutRunsAfterIdentityCheck:
    def test_cross_user_mismatch_with_subqueries_returns_403_not_502(self):
        """Fix C.1: the fan-out branch is positioned AFTER
        verify_body_identity, so an identity mismatch surfaces as its real
        403 — it must never reach ``_retrieve_sub_queries`` and come back
        masked as a fan-out 502 'all sub-query retrievals failed'."""
        from fastapi.testclient import TestClient

        from retrieval_api.main import app
        from tests.test_auth import _make_jwt_payload, _patch_jwt

        client = TestClient(app)
        payload = _make_jwt_payload(sub="user_a", resourceowner="org_x")
        with _patch_jwt(payload):
            resp = client.post(
                "/retrieve",
                json={
                    "query": "gecombineerde vraag",
                    "org_id": "org_x",
                    "user_id": "user_b",
                    "scope": "personal",
                    "sub_queries": ["vraag een?", "vraag twee?"],
                },
                headers={"Authorization": "Bearer valid"},
            )
        assert resp.status_code == 403
        assert resp.json()["detail"] == {"error": "user_mismatch"}
