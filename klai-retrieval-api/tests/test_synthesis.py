"""Tests for synthesis service."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch


from retrieval_api.services.synthesis import (
    _build_citations,
    _build_context,
    _extract_citation_indices,
    synthesize,
)


class TestBuildContext:
    def test_basic_formatting(self):
        chunks = [
            {"context_prefix": "Policy: ", "text": "Refund within 30 days."},
            {"context_prefix": "", "text": "Contact support for help."},
        ]
        result = _build_context(chunks)
        assert "[1] Policy: Refund within 30 days." in result
        assert "[2] Contact support for help." in result
        assert "\n\n" in result

    def test_truncation_at_max_chars(self):
        big_text = "x" * 20_000
        chunks = [
            {"context_prefix": "", "text": big_text},
            {"context_prefix": "", "text": big_text},
        ]
        result = _build_context(chunks)
        assert "[1]" in result
        assert "[2]" not in result

    def test_empty_input(self):
        result = _build_context([])
        assert result == ""

    def test_none_prefix_treated_as_empty(self):
        chunks = [{"context_prefix": None, "text": "hello"}]
        result = _build_context(chunks)
        assert "[1] hello" in result


class TestExtractCitationIndices:
    def test_basic_extraction(self):
        text = "According to [1] and [3], the policy states..."
        result = _extract_citation_indices(text)
        assert result == [1, 3]

    def test_dedup_and_sort(self):
        text = "See [3] and [1] and [3] again."
        result = _extract_citation_indices(text)
        assert result == [1, 3]

    def test_no_citations(self):
        text = "No citations here."
        result = _extract_citation_indices(text)
        assert result == []

    def test_multiple_digits(self):
        text = "Chunk [12] is relevant."
        result = _extract_citation_indices(text)
        assert result == [12]


class TestBuildCitations:
    def test_valid_index(self):
        chunks = [
            {
                "artifact_id": "a1",
                "context_prefix": "Policy: ",
                "text": "text1",
                "chunk_id": "c1",
                "reranker_score": 0.95,
                "score": 0.8,
            },
        ]
        result = _build_citations([1], chunks)
        assert len(result) == 1
        assert result[0]["index"] == 1
        assert result[0]["artifact_id"] == "a1"
        assert result[0]["title"] == "Policy: "
        assert result[0]["chunk_ids"] == ["c1"]
        assert result[0]["relevance_score"] == 0.95

    def test_out_of_range_skipped(self):
        chunks = [
            {
                "artifact_id": "a1",
                "context_prefix": "P",
                "text": "t",
                "chunk_id": "c1",
                "score": 0.5,
            }
        ]
        result = _build_citations([1, 5, 10], chunks)
        assert len(result) == 1
        assert result[0]["index"] == 1

    def test_uses_text_fallback_when_no_prefix(self):
        chunks = [
            {
                "artifact_id": "a1",
                "context_prefix": None,
                "text": "Fallback text here",
                "chunk_id": "c1",
                "score": 0.7,
            },
        ]
        result = _build_citations([1], chunks)
        assert result[0]["title"] == "Fallback text here"

    def test_prefers_reranker_score_over_score(self):
        chunks = [
            {
                "artifact_id": "a1",
                "context_prefix": "P",
                "text": "t",
                "chunk_id": "c1",
                "reranker_score": 0.99,
                "score": 0.5,
            },
        ]
        result = _build_citations([1], chunks)
        assert result[0]["relevance_score"] == 0.99

    def test_falls_back_to_score_when_no_reranker(self):
        chunks = [
            {
                "artifact_id": "a1",
                "context_prefix": "P",
                "text": "t",
                "chunk_id": "c1",
                "score": 0.75,
            },
        ]
        result = _build_citations([1], chunks)
        assert result[0]["relevance_score"] == 0.75

    def test_title_truncated_to_80_chars(self):
        long_prefix = "A" * 200
        chunks = [
            {
                "artifact_id": "a1",
                "context_prefix": long_prefix,
                "text": "t",
                "chunk_id": "c1",
                "score": 0.5,
            },
        ]
        result = _build_citations([1], chunks)
        assert len(result[0]["title"]) == 80


class TestSynthesize:
    @patch("retrieval_api.services.synthesis.httpx.AsyncClient")
    async def test_stream_tokens_then_done(self, mock_client_cls):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        sse_lines = [
            "data: " + json.dumps({"choices": [{"delta": {"content": "Hello"}}]}),
            "data: " + json.dumps({"choices": [{"delta": {"content": " [1]"}}]}),
            "data: [DONE]",
        ]

        mock_stream_resp = AsyncMock()
        mock_stream_resp.__aenter__ = AsyncMock(return_value=mock_stream_resp)
        mock_stream_resp.__aexit__ = AsyncMock(return_value=False)

        async def fake_aiter_lines():
            for line in sse_lines:
                yield line

        mock_stream_resp.aiter_lines = fake_aiter_lines
        mock_client.stream = MagicMock(return_value=mock_stream_resp)

        chunks = [
            {
                "chunk_id": "c1",
                "text": "refund policy text",
                "artifact_id": "a1",
                "context_prefix": "Policy: ",
                "score": 0.85,
                "reranker_score": 0.92,
            },
        ]

        items = []
        async for item in synthesize("What is the refund policy?", chunks, []):
            items.append(item)

        str_items = [i for i in items if isinstance(i, str)]
        dict_items = [i for i in items if isinstance(i, dict)]
        assert len(str_items) == 2
        assert str_items[0] == "Hello"
        assert str_items[1] == " [1]"
        assert len(dict_items) == 1
        assert dict_items[0]["retrieval_bypassed"] is False
        assert len(dict_items[0]["citations"]) == 1
        assert dict_items[0]["citations"][0]["index"] == 1

    @patch("retrieval_api.services.synthesis.httpx.AsyncClient")
    async def test_with_history(self, mock_client_cls):
        """History[-3:] should be included in messages sent to LLM."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        sse_lines = [
            "data: " + json.dumps({"choices": [{"delta": {"content": "Answer"}}]}),
            "data: [DONE]",
        ]

        mock_stream_resp = AsyncMock()
        mock_stream_resp.__aenter__ = AsyncMock(return_value=mock_stream_resp)
        mock_stream_resp.__aexit__ = AsyncMock(return_value=False)

        async def fake_aiter_lines():
            for line in sse_lines:
                yield line

        mock_stream_resp.aiter_lines = fake_aiter_lines
        mock_client.stream = MagicMock(return_value=mock_stream_resp)

        history = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "resp1"},
            {"role": "user", "content": "msg2"},
            {"role": "assistant", "content": "resp2"},
        ]

        items = []
        async for item in synthesize("follow up?", [], history):
            items.append(item)

        call_args = mock_client.stream.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")
        messages = body["messages"]
        # system + 3 history messages (last 3) + user question = 5
        assert len(messages) == 5
        assert messages[0]["role"] == "system"
        # Last 3 from history: resp1, msg2, resp2
        assert messages[1]["content"] == "resp1"
        assert messages[2]["content"] == "msg2"
        assert messages[3]["content"] == "resp2"

    @patch("retrieval_api.services.synthesis.httpx.AsyncClient")
    async def test_no_content_delta_skipped(self, mock_client_cls):
        """Lines with empty delta.content should not yield tokens."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        sse_lines = [
            "data: " + json.dumps({"choices": [{"delta": {}}]}),
            "data: " + json.dumps({"choices": [{"delta": {"content": "token"}}]}),
            "data: [DONE]",
        ]

        mock_stream_resp = AsyncMock()
        mock_stream_resp.__aenter__ = AsyncMock(return_value=mock_stream_resp)
        mock_stream_resp.__aexit__ = AsyncMock(return_value=False)

        async def fake_aiter_lines():
            for line in sse_lines:
                yield line

        mock_stream_resp.aiter_lines = fake_aiter_lines
        mock_client.stream = MagicMock(return_value=mock_stream_resp)

        items = []
        async for item in synthesize("q", [], []):
            items.append(item)

        str_items = [i for i in items if isinstance(i, str)]
        assert len(str_items) == 1
        assert str_items[0] == "token"
