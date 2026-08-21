"""Graph edges must cite the document, not a truncated fact.

SPEC-RAG-GRAPH-CITE-001 made graph edges citable by resolving their
artifact_id. That alone is not enough: an edge carries no title and no URL,
so ``evidence_pack._title()`` falls through to ``text[:80]`` and the source
list renders a chopped-off sentence like

    "De paginamap identificeert de Voys-app als een applicatie die kan gebruik"

instead of the help-centre article it came from. Observed in production on
2026-08-21 right after the citation change shipped.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from retrieval_api.api.retrieve import _label_graph_results
from retrieval_api.models import RetrieveRequest
from retrieval_api.services import search
from retrieval_api.services.evidence_pack import build_evidence_pack, chunk_source_key


def _point(artifact_id: str, **payload):
    return SimpleNamespace(id="c1", score=0.9, payload={"artifact_id": artifact_id, **payload})


def _graph_chunk(artifact_id: str | None, text: str = "Nummerbehoud kan bij overstap"):
    return {
        "chunk_id": "graph:e1",
        "text": text,
        "score": 0.9,
        "artifact_id": artifact_id,
        "content_type": "graph_edge",
        "context_prefix": None,
        "scope": "org",
        "valid_at": None,
        "invalid_at": None,
    }


@pytest.fixture(autouse=True)
def reset_client():
    search._client = None
    yield
    search._client = None


class TestArtifactDisplayMetadata:
    @pytest.mark.asyncio
    async def test_returns_title_and_url_per_artifact(self):
        mock_client = AsyncMock()
        mock_client.scroll.return_value = (
            [
                _point(
                    "artifact-abc",
                    title="Nummerbehoud aanvragen",
                    source_url="https://help.voys.nl/nummerbehoud-aanvragen",
                    source_label="help.voys.nl",
                )
            ],
            None,
        )

        with patch.object(search, "_get_client", return_value=mock_client):
            req = RetrieveRequest(query="q", org_id="org-1", scope="org")
            meta = await search.fetch_artifact_display_metadata(["artifact-abc"], req)

        assert meta["artifact-abc"]["title"] == "Nummerbehoud aanvragen"
        assert meta["artifact-abc"]["source_url"] == "https://help.voys.nl/nummerbehoud-aanvragen"

    @pytest.mark.asyncio
    async def test_lookup_is_tenant_scoped(self):
        """TENANT ISOLATION: a title can be as sensitive as the text.

        The lookup must go through _scope_filter like every other read, so the
        org condition and the private-visibility rules apply. Resolving labels
        must never become a side channel that names documents the caller
        cannot open.
        """
        mock_client = AsyncMock()
        mock_client.scroll.return_value = ([], None)

        with patch.object(search, "_get_client", return_value=mock_client):
            req = RetrieveRequest(query="q", org_id="org-1", scope="org")
            await search.fetch_artifact_display_metadata(["artifact-abc"], req)

        scroll_filter = mock_client.scroll.await_args.kwargs["scroll_filter"]
        rendered = repr(scroll_filter)
        assert "org_id" in rendered and "org-1" in rendered
        assert "visibility" in rendered, "private-visibility rules must apply to labels too"

    @pytest.mark.asyncio
    async def test_no_artifact_ids_skips_the_round_trip(self):
        mock_client = AsyncMock()
        with patch.object(search, "_get_client", return_value=mock_client):
            req = RetrieveRequest(query="q", org_id="org-1", scope="org")
            assert await search.fetch_artifact_display_metadata([], req) == {}
        mock_client.scroll.assert_not_called()

    @pytest.mark.asyncio
    async def test_qdrant_failure_degrades_to_no_labels(self):
        mock_client = AsyncMock()
        mock_client.scroll.side_effect = RuntimeError("qdrant down")

        with patch.object(search, "_get_client", return_value=mock_client):
            req = RetrieveRequest(query="q", org_id="org-1", scope="org")
            assert await search.fetch_artifact_display_metadata(["a1"], req) == {}


class TestGraphResultLabelling:
    @pytest.mark.asyncio
    async def test_graph_fact_cites_the_document_not_the_sentence(self):
        chunks = [_graph_chunk("artifact-abc")]
        meta = {
            "artifact-abc": {
                "title": "Nummerbehoud aanvragen",
                "source_url": "https://help.voys.nl/nummerbehoud-aanvragen",
                "source_label": "help.voys.nl",
                "original_filename": None,
            }
        }

        with patch.object(
            search, "fetch_artifact_display_metadata", new=AsyncMock(return_value=meta)
        ):
            req = RetrieveRequest(query="q", org_id="org-1", scope="org")
            await _label_graph_results(chunks, req)

        pack = build_evidence_pack(chunks)
        assert pack.sources[0].title == "Nummerbehoud aanvragen"
        assert pack.sources[0].source_url == "https://help.voys.nl/nummerbehoud-aanvragen"
        assert "Nummerbehoud kan bij overstap" not in pack.sources[0].title

    def test_without_labels_the_source_is_the_truncated_fact(self):
        """Locks the defect this module exists to prevent."""
        pack = build_evidence_pack([_graph_chunk("artifact-abc")])
        assert pack.sources[0].title.startswith("Nummerbehoud kan bij overstap")

    @pytest.mark.asyncio
    async def test_graph_fact_and_chunk_from_one_document_collapse_to_one_source(self):
        """source_url keying means a fact no longer duplicates its own document."""
        graph = _graph_chunk("artifact-abc")
        ordinary = {
            "chunk_id": "c-1",
            "text": "Nummerbehoud aanvragen doe je zo",
            "score": 0.8,
            "artifact_id": "artifact-abc",
            "content_type": "kb_article",
            "source_url": "https://help.voys.nl/nummerbehoud-aanvragen",
            "title": "Nummerbehoud aanvragen",
            "scope": "org",
        }
        meta = {
            "artifact-abc": {
                "title": "Nummerbehoud aanvragen",
                "source_url": "https://help.voys.nl/nummerbehoud-aanvragen",
                "source_label": "help.voys.nl",
                "original_filename": None,
            }
        }

        with patch.object(
            search, "fetch_artifact_display_metadata", new=AsyncMock(return_value=meta)
        ):
            req = RetrieveRequest(query="q", org_id="org-1", scope="org")
            await _label_graph_results([graph], req)

        assert chunk_source_key(graph) == chunk_source_key(ordinary)
        pack = build_evidence_pack([graph, ordinary])
        assert len(pack.sources) == 1
        assert len(pack.items) == 2

    @pytest.mark.asyncio
    async def test_unresolved_artifact_keeps_previous_behaviour(self):
        chunks = [_graph_chunk("artifact-abc")]
        with patch.object(
            search, "fetch_artifact_display_metadata", new=AsyncMock(return_value={})
        ):
            req = RetrieveRequest(query="q", org_id="org-1", scope="org")
            await _label_graph_results(chunks, req)
        assert "title" not in chunks[0]
