"""Tests for coreference resolution service."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from retrieval_api.services.coreference import resolve


class TestCoreference:
    @pytest.mark.asyncio
    async def test_empty_history_returns_original(self):
        """No LLM call should be made when history is empty."""
        result = await resolve("What is the refund policy?", [])
        assert result == "What is the refund policy?"

    @pytest.mark.asyncio
    async def test_empty_list_history_returns_original(self):
        result = await resolve("Tell me more", [])
        assert result == "Tell me more"

    @pytest.mark.asyncio
    async def test_normal_resolution(self):
        """LLM resolves coreference successfully."""
        with patch(
            "retrieval_api.services.coreference._call_llm",
            new_callable=AsyncMock,
            return_value="What is Klai's refund policy?",
        ):
            result = await resolve(
                "What is their policy?",
                [
                    {"role": "user", "content": "Tell me about Klai"},
                    {"role": "assistant", "content": "Klai is an AI platform."},
                ],
            )
            assert result == "What is Klai's refund policy?"

    @pytest.mark.asyncio
    async def test_timeout_returns_original(self):
        """When LLM times out, original query is returned."""
        import asyncio

        async def slow_llm(*args, **kwargs):
            await asyncio.sleep(10)
            return "resolved"

        with patch(
            "retrieval_api.services.coreference._call_llm",
            side_effect=slow_llm,
        ):
            with patch("retrieval_api.services.coreference.settings") as mock_settings:
                mock_settings.coreference_timeout = 0.01
                mock_settings.litellm_url = "http://test:4000"
                mock_settings.litellm_api_key = ""
                result = await resolve(
                    "Tell me more",
                    [{"role": "user", "content": "hi"}],
                )
                assert result == "Tell me more"

    @pytest.mark.asyncio
    async def test_llm_error_returns_original(self):
        """When LLM raises an exception, original query is returned."""
        with patch(
            "retrieval_api.services.coreference._call_llm",
            new_callable=AsyncMock,
            side_effect=Exception("LLM unavailable"),
        ):
            result = await resolve(
                "What about that?",
                [{"role": "user", "content": "hi"}],
            )
            assert result == "What about that?"
