"""Tests for description_generator -- taxonomy node description generation."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.description_generator import generate_node_description


def _mock_litellm_response(description: str) -> AsyncMock:
    """Build a mock httpx response for description generation."""
    response_json = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({"description": description})
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


class TestGenerateNodeDescription:
    @pytest.mark.asyncio
    async def test_returns_description(self):
        mock_client = _mock_litellm_response("Questions about invoices and payments")
        with patch("knowledge_ingest.description_generator.httpx.AsyncClient", return_value=mock_client):
            desc = await generate_node_description("Billing", None, ["Invoice FAQ"])
        assert desc == "Questions about invoices and payments"

    @pytest.mark.asyncio
    async def test_truncates_to_200_chars(self):
        long_desc = "x" * 300
        mock_client = _mock_litellm_response(long_desc)
        with patch("knowledge_ingest.description_generator.httpx.AsyncClient", return_value=mock_client):
            desc = await generate_node_description("Billing", None, [])
        assert len(desc) <= 200

    @pytest.mark.asyncio
    async def test_returns_empty_on_timeout(self):
        with patch(
            "knowledge_ingest.description_generator._call_litellm",
            side_effect=asyncio.TimeoutError(),
        ):
            desc = await generate_node_description("Billing", None, [])
        assert desc == ""

    @pytest.mark.asyncio
    async def test_returns_empty_on_error(self):
        with patch(
            "knowledge_ingest.description_generator._call_litellm",
            side_effect=Exception("connection refused"),
        ):
            desc = await generate_node_description("Billing", None, [])
        assert desc == ""

    @pytest.mark.asyncio
    async def test_includes_parent_name_in_prompt(self):
        captured = []

        async def _capture(msg: str) -> dict:
            captured.append(msg)
            return {"description": "test"}

        with patch("knowledge_ingest.description_generator._call_litellm", side_effect=_capture):
            await generate_node_description("Subscriptions", "Billing", ["Pricing FAQ"])

        assert "Parent category: Billing" in captured[0]
        assert "Pricing FAQ" in captured[0]
