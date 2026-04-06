"""Tests for content_labeler -- blind keyword generation before taxonomy (SPEC-KB-023)."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.content_labeler import generate_content_label


def _mock_litellm_response(keywords: list[str]) -> AsyncMock:
    """Build a mock httpx client returning a LiteLLM-style response."""
    response_json = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({"keywords": keywords})
                }
            }
        ]
    }
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json = MagicMock(return_value=response_json)

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.post = AsyncMock(return_value=mock_resp)
    return mock_client


class TestGenerateContentLabel:
    @pytest.mark.asyncio
    async def test_returns_keywords_on_success(self):
        mock_client = _mock_litellm_response(["sip-trunk", "provider-portability", "telefooncentrale"])
        with patch("knowledge_ingest.content_labeler.httpx.AsyncClient", return_value=mock_client):
            labels = await generate_content_label("Fanvil Opties", "Hardware setup guide...")
        assert labels == ["sip-trunk", "provider-portability", "telefooncentrale"]

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_timeout(self):
        with patch(
            "knowledge_ingest.content_labeler._call_litellm",
            side_effect=asyncio.TimeoutError(),
        ):
            labels = await generate_content_label("Title", "Content")
        assert labels == []

    @pytest.mark.asyncio
    async def test_returns_empty_list_on_http_error(self):
        with patch(
            "knowledge_ingest.content_labeler._call_litellm",
            side_effect=Exception("connection refused"),
        ):
            labels = await generate_content_label("Title", "Content")
        assert labels == []

    @pytest.mark.asyncio
    async def test_keywords_are_lowercased(self):
        mock_client = _mock_litellm_response(["SIP-Trunk", "VOIP", "Telefonie"])
        with patch("knowledge_ingest.content_labeler.httpx.AsyncClient", return_value=mock_client):
            labels = await generate_content_label("Title", "Content")
        assert labels == ["sip-trunk", "voip", "telefonie"]

    @pytest.mark.asyncio
    async def test_keywords_are_deduplicated(self):
        mock_client = _mock_litellm_response(["voip", "voip", "sip"])
        with patch("knowledge_ingest.content_labeler.httpx.AsyncClient", return_value=mock_client):
            labels = await generate_content_label("Title", "Content")
        assert labels == ["voip", "sip"]

    @pytest.mark.asyncio
    async def test_clamped_to_5_keywords(self):
        mock_client = _mock_litellm_response(["a", "b", "c", "d", "e", "f", "g"])
        with patch("knowledge_ingest.content_labeler.httpx.AsyncClient", return_value=mock_client):
            labels = await generate_content_label("Title", "Content")
        assert len(labels) <= 5

    @pytest.mark.asyncio
    async def test_content_preview_truncated_to_500_chars(self):
        """Verify only the first 500 chars of content are sent to the LLM."""
        captured_messages = []

        async def _capture_call(user_message: str) -> dict:
            captured_messages.append(user_message)
            return {"keywords": ["test"]}

        with patch("knowledge_ingest.content_labeler._call_litellm", side_effect=_capture_call):
            long_content = "x" * 2000
            await generate_content_label("Title", long_content)

        assert len(captured_messages) == 1
        # The user message should contain at most 500 x's
        assert "x" * 501 not in captured_messages[0]
        assert "x" * 500 in captured_messages[0]

    @pytest.mark.asyncio
    async def test_empty_keywords_from_llm_returns_empty(self):
        mock_client = _mock_litellm_response([])
        with patch("knowledge_ingest.content_labeler.httpx.AsyncClient", return_value=mock_client):
            labels = await generate_content_label("Title", "Content")
        assert labels == []

    @pytest.mark.asyncio
    async def test_non_string_keywords_are_skipped(self):
        """LLM occasionally returns non-string values — they must be filtered."""
        mock_client = _mock_litellm_response([42, "valid-keyword", None])  # type: ignore[list-item]
        with patch("knowledge_ingest.content_labeler.httpx.AsyncClient", return_value=mock_client):
            labels = await generate_content_label("Title", "Content")
        assert labels == ["valid-keyword"]

    @pytest.mark.asyncio
    async def test_uses_klai_fast_model(self):
        """Verify the LLM call uses the klai-fast model alias (model policy)."""
        captured_payloads: list[dict] = []

        async def _capture_call(user_message: str) -> dict:
            return {"keywords": ["test"]}

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={
            "choices": [{"message": {"content": '{"keywords": ["test"]}'}}]
        })

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        async def _post(url: str, **kwargs: object) -> MagicMock:
            captured_payloads.append(kwargs.get("json", {}))
            return mock_resp

        mock_client.post = _post

        with patch("knowledge_ingest.content_labeler.httpx.AsyncClient", return_value=mock_client):
            await generate_content_label("Title", "Content")

        assert len(captured_payloads) == 1
        assert captured_payloads[0]["model"] == "klai-fast"

    @pytest.mark.asyncio
    async def test_system_prompt_has_no_taxonomy_reference(self):
        """System prompt must not reference taxonomy (no list of categories is injected)."""
        from knowledge_ingest.content_labeler import _SYSTEM_PROMPT
        assert "taxonomy" not in _SYSTEM_PROMPT.lower()
        # "category names" is allowed — it instructs the LLM NOT to use them
        # What must be absent: any taxonomy node data injected into the prompt
        assert "node_id" not in _SYSTEM_PROMPT
        assert "Available taxonomy" not in _SYSTEM_PROMPT
