"""Tests for TEI embedding service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch


from retrieval_api.services.tei import embed_batch, embed_single


class TestEmbedSingle:
    @patch("retrieval_api.services.tei.httpx.AsyncClient")
    async def test_nested_response_unwrapped(self, mock_client_cls):
        """TEI returns [[float,...]] for single input -- should unwrap to [float,...]."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [[0.1, 0.2, 0.3]]
        mock_client.post = AsyncMock(return_value=mock_resp)

        result = await embed_single("test text")
        assert result == [0.1, 0.2, 0.3]

        # Verify correct payload was sent
        call_args = mock_client.post.call_args
        assert "/embed" in call_args[0][0]
        sent_json = call_args[1]["json"]
        assert sent_json["inputs"] == "test text"
        assert sent_json["normalize"] is True

    @patch("retrieval_api.services.tei.httpx.AsyncClient")
    async def test_flat_response_passthrough(self, mock_client_cls):
        """If TEI returns [float,...] directly, pass through unchanged."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [0.4, 0.5, 0.6]
        mock_client.post = AsyncMock(return_value=mock_resp)

        result = await embed_single("test text")
        assert result == [0.4, 0.5, 0.6]


class TestEmbedBatch:
    @patch("retrieval_api.services.tei.httpx.AsyncClient")
    async def test_returns_list_of_vectors(self, mock_client_cls):
        """embed_batch returns list of vectors."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [[0.1, 0.2], [0.3, 0.4]]
        mock_client.post = AsyncMock(return_value=mock_resp)

        result = await embed_batch(["text1", "text2"])
        assert result == [[0.1, 0.2], [0.3, 0.4]]

    @patch("retrieval_api.services.tei.httpx.AsyncClient")
    async def test_correct_inputs_sent(self, mock_client_cls):
        """Verify the correct inputs are sent to TEI."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [[0.1], [0.2], [0.3]]
        mock_client.post = AsyncMock(return_value=mock_resp)

        texts = ["alpha", "beta", "gamma"]
        await embed_batch(texts)

        call_args = mock_client.post.call_args
        sent_json = call_args[1]["json"]
        assert sent_json["inputs"] == ["alpha", "beta", "gamma"]
        assert sent_json["normalize"] is True
