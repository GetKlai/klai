"""
Unit tests for summarizer module.
Uses mocked LiteLLM responses -- no real HTTP calls.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.services.summarizer import extract_facts, summarize_meeting, synthesize_summary

SAMPLE_FACTS = {
    "speakers_present": ["Alice", "Bob"],
    "topics": ["Q3 roadmap", "budget approval"],
    "decisions": ["Proceed with migration in Q3"],
    "action_items": [{"owner": "Alice", "task": "Send updated budget by Friday"}],
    "open_questions": ["What is the timeline for Phase 2?"],
    "next_steps": ["Alice sends budget", "Bob schedules follow-up"],
}


@pytest.mark.asyncio
async def test_extract_facts_with_segments() -> None:
    segments = [
        {"speaker": "Alice", "text": "Let's discuss the Q3 roadmap.", "start": 0.0, "end": 5.0},
        {"speaker": "Bob", "text": "I think we should proceed.", "start": 6.0, "end": 9.0},
    ]
    mock_response = json.dumps(SAMPLE_FACTS)

    with patch("app.services.summarizer._call_llm", new=AsyncMock(return_value=mock_response)):
        result = await extract_facts("", segments, "en")

    assert result["speakers_present"] == ["Alice", "Bob"]
    assert "Q3 roadmap" in result["topics"]


@pytest.mark.asyncio
async def test_extract_facts_flat_text() -> None:
    mock_response = json.dumps(SAMPLE_FACTS)
    with patch("app.services.summarizer._call_llm", new=AsyncMock(return_value=mock_response)):
        result = await extract_facts("Flat transcript text", None, "nl")
    assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_extract_facts_strips_code_fences() -> None:
    mock_response = f"```json\n{json.dumps(SAMPLE_FACTS)}\n```"
    with patch("app.services.summarizer._call_llm", new=AsyncMock(return_value=mock_response)):
        result = await extract_facts("text", None, "en")
    assert isinstance(result, dict)


@pytest.mark.asyncio
async def test_synthesize_summary() -> None:
    with patch("app.services.summarizer._call_llm", new=AsyncMock(return_value="## Summary\n\nGreat meeting.")):
        result = await synthesize_summary(SAMPLE_FACTS, "en")
    assert "Summary" in result


@pytest.mark.asyncio
async def test_summarize_meeting_full_pipeline() -> None:
    with patch("app.services.summarizer._call_llm", new=AsyncMock(side_effect=[
        json.dumps(SAMPLE_FACTS),
        "## Samenvatting\n\nProductieve vergadering.",
    ])):
        result = await summarize_meeting("transcript", None, "nl")

    assert "markdown" in result
    assert "structured" in result
    assert result["structured"]["speakers"] == ["Alice", "Bob"]
    assert result["structured"]["action_items"][0]["owner"] == "Alice"


@pytest.mark.asyncio
async def test_summarize_meeting_llm_failure_propagates() -> None:
    import httpx

    with patch("app.services.summarizer._call_llm", new=AsyncMock(side_effect=httpx.TimeoutException("timeout"))):
        with pytest.raises(httpx.TimeoutException):
            await summarize_meeting("transcript", None, "en")
