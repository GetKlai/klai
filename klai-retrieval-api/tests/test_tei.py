"""Tests for TEI embedding service.

The production code calls the OpenAI-compatible ``/v1/embeddings`` endpoint
(served by Infinity at port 7997 on gpu-01) — NOT the raw HuggingFace TEI
``/embed`` endpoint. Request shape: ``{"input": str|list, "model": "BAAI/bge-m3"}``.
Response shape: ``{"data": [{"embedding": [...], "index": int}]}``.

Earlier versions of these tests targeted the HF TEI shape and broke silently
when the code migrated to the OpenAI-compat client. Refreshed to match.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from retrieval_api.services.tei import embed_batch, embed_single


def _openai_embedding_response(vectors: list[list[float]]) -> dict:
    """Build an OpenAI-compatible /v1/embeddings response body."""
    return {
        "data": [{"embedding": vec, "index": i} for i, vec in enumerate(vectors)],
        "model": "BAAI/bge-m3",
        "object": "list",
    }


class TestEmbedSingle:
    @patch("retrieval_api.services.tei.httpx.AsyncClient")
    async def test_returns_first_embedding_from_data_array(self, mock_client_cls):
        """``embed_single`` returns the first vector from the OpenAI-compat
        ``data[0].embedding`` shape."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _openai_embedding_response([[0.1, 0.2, 0.3]])
        mock_client.post = AsyncMock(return_value=mock_resp)

        result = await embed_single("test text")
        assert result == [0.1, 0.2, 0.3]

    @patch("retrieval_api.services.tei.httpx.AsyncClient")
    async def test_correct_payload_sent(self, mock_client_cls):
        """Request payload uses the OpenAI-compat ``input`` + ``model`` keys."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _openai_embedding_response([[0.1, 0.2, 0.3]])
        mock_client.post = AsyncMock(return_value=mock_resp)

        await embed_single("test text")

        call_args = mock_client.post.call_args
        # URL targets the OpenAI-compat endpoint, not the HF TEI /embed endpoint.
        assert "/v1/embeddings" in call_args[0][0]
        sent_json = call_args[1]["json"]
        assert sent_json["input"] == "test text"
        assert sent_json["model"] == "BAAI/bge-m3"


class TestEmbedBatch:
    @patch("retrieval_api.services.tei.httpx.AsyncClient")
    async def test_returns_list_of_vectors(self, mock_client_cls):
        """``embed_batch`` returns one vector per input from ``data[*].embedding``."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _openai_embedding_response([[0.1, 0.2], [0.3, 0.4]])
        mock_client.post = AsyncMock(return_value=mock_resp)

        result = await embed_batch(["text1", "text2"])
        assert result == [[0.1, 0.2], [0.3, 0.4]]

    @patch("retrieval_api.services.tei.httpx.AsyncClient")
    async def test_preserves_input_order_when_data_returned_unsorted(self, mock_client_cls):
        """OpenAI servers may return ``data`` items out of order; ``embed_batch``
        sorts on ``index`` before extracting embeddings (production contract)."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        # Deliberately scrambled order — index field is the source of truth.
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "data": [
                {"embedding": [0.3], "index": 2},
                {"embedding": [0.1], "index": 0},
                {"embedding": [0.2], "index": 1},
            ],
            "model": "BAAI/bge-m3",
            "object": "list",
        }
        mock_client.post = AsyncMock(return_value=mock_resp)

        result = await embed_batch(["alpha", "beta", "gamma"])
        # Output must follow the original input order (index 0 → 1 → 2).
        assert result == [[0.1], [0.2], [0.3]]

    @patch("retrieval_api.services.tei.httpx.AsyncClient")
    async def test_correct_payload_sent(self, mock_client_cls):
        """Batch payload uses ``input`` (list) + ``model`` keys."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = _openai_embedding_response([[0.1], [0.2], [0.3]])
        mock_client.post = AsyncMock(return_value=mock_resp)

        await embed_batch(["alpha", "beta", "gamma"])

        call_args = mock_client.post.call_args
        assert "/v1/embeddings" in call_args[0][0]
        sent_json = call_args[1]["json"]
        assert sent_json["input"] == ["alpha", "beta", "gamma"]
        assert sent_json["model"] == "BAAI/bge-m3"
