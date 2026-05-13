"""Tests for knowledge_ingest.contextual (SPEC-RAG-CONTEXTUAL-001)."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(**overrides):
    from unittest.mock import MagicMock

    s = MagicMock()
    s.litellm_url = overrides.get("litellm_url", "http://litellm:4000")
    s.litellm_api_key = overrides.get("litellm_api_key", "test-key")
    s.enrichment_model = overrides.get("enrichment_model", "klai-fast")
    s.enrichment_max_document_tokens = overrides.get("enrichment_max_document_tokens", 4000)
    return s


class _MockTransport(httpx.AsyncBaseTransport):
    def __init__(self, status_code: int, json_body: dict | None = None) -> None:
        self._status_code = status_code
        self._json_body = json_body or {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        import json

        return httpx.Response(
            status_code=self._status_code,
            headers={"content-type": "application/json"},
            content=json.dumps(self._json_body).encode(),
            request=request,
        )


_DUTCH_PARAGRAPH = (
    "Voys is een telefoonbedrijf dat zakelijke klanten ondersteunt met "
    "VoIP-oplossingen en bijbehorende software. De klantenservice helpt "
    "bij portering, integraties met CRM-systemen zoals Pipedrive en "
    "Monday, en troubleshoot van Bubble en Yealink-toestellen."
)
_ENGLISH_PARAGRAPH = (
    "Voys is a Dutch business telephony provider supporting VoIP "
    "deployments for SMB customers. Customer service handles number "
    "porting, integrations with CRMs such as Pipedrive and Monday, and "
    "troubleshooting for the Bubble browser plugin and Yealink phones."
)


# ---------------------------------------------------------------------------
# detect_language
# ---------------------------------------------------------------------------


def test_detect_language_returns_nl_for_dutch() -> None:
    from knowledge_ingest.contextual import detect_language

    assert detect_language(_DUTCH_PARAGRAPH) == "nl"


def test_detect_language_returns_en_for_english() -> None:
    from knowledge_ingest.contextual import detect_language

    assert detect_language(_ENGLISH_PARAGRAPH) == "en"


def test_detect_language_short_text_falls_back_to_default() -> None:
    """Below 30-char threshold the detector cannot be reliable — return default."""
    from knowledge_ingest.contextual import DEFAULT_PROMPT_LANGUAGE, detect_language

    assert detect_language("hi") == DEFAULT_PROMPT_LANGUAGE
    assert detect_language("") == DEFAULT_PROMPT_LANGUAGE


def test_detect_language_unknown_lang_falls_back_to_default() -> None:
    """German content should fall back to default (only nl/en are supported)."""
    from knowledge_ingest.contextual import DEFAULT_PROMPT_LANGUAGE, detect_language

    german = (
        "Voys ist ein niederländisches Telefonieunternehmen das Geschäftskunden "
        "unterstützt. Der Kundenservice hilft bei Portierungen und Integrationen "
        "mit CRM-Systemen sowie bei der Fehlersuche für Yealink-Telefone."
    )
    assert detect_language(german) == DEFAULT_PROMPT_LANGUAGE


# ---------------------------------------------------------------------------
# generate_document_summary
# ---------------------------------------------------------------------------


_SUMMARY_CONTENT_NL = (
    "Dit document beschrijft de Voys customer-service "
    "procedures voor portering en integraties."
)
_SUMMARY_200_BODY = {
    "choices": [
        {
            "message": {
                "role": "assistant",
                "content": _SUMMARY_CONTENT_NL,
            }
        }
    ]
}


@pytest.mark.asyncio
async def test_generate_document_summary_returns_string_on_success() -> None:
    from knowledge_ingest.contextual import generate_document_summary

    transport = _MockTransport(status_code=200, json_body=_SUMMARY_200_BODY)
    settings = _make_settings()

    with patch("knowledge_ingest.contextual.settings", settings):
        result = await generate_document_summary(
            text=_DUTCH_PARAGRAPH,
            title="Voys customer-service handleiding",
            language="nl",
            _transport=transport,
        )

    assert isinstance(result, str)
    assert len(result) > 0
    assert "Voys" in result


@pytest.mark.asyncio
async def test_generate_document_summary_returns_empty_on_http_error() -> None:
    """500 from LiteLLM proxy: returns empty string, no raise (fail-open per REQ-2)."""
    from knowledge_ingest.contextual import generate_document_summary

    transport = _MockTransport(status_code=500, json_body={"error": "boom"})
    settings = _make_settings()

    with patch("knowledge_ingest.contextual.settings", settings):
        result = await generate_document_summary(
            text=_DUTCH_PARAGRAPH,
            title="Test",
            language="nl",
            _transport=transport,
        )

    assert result == ""


@pytest.mark.asyncio
async def test_generate_document_summary_returns_empty_on_blank_input() -> None:
    """Empty/whitespace-only document text short-circuits to empty summary."""
    from knowledge_ingest.contextual import generate_document_summary

    settings = _make_settings()
    with patch("knowledge_ingest.contextual.settings", settings):
        assert await generate_document_summary(text="", title="x", language="nl") == ""
        assert await generate_document_summary(text="   ", title="x", language="nl") == ""


@pytest.mark.asyncio
async def test_generate_document_summary_auto_detects_language() -> None:
    """When language is None, generator auto-detects via detect_language()."""
    from knowledge_ingest.contextual import generate_document_summary

    transport = _MockTransport(status_code=200, json_body=_SUMMARY_200_BODY)
    settings = _make_settings()

    with patch("knowledge_ingest.contextual.settings", settings):
        result = await generate_document_summary(
            text=_ENGLISH_PARAGRAPH,
            title="English doc",
            language=None,  # forces auto-detect
            _transport=transport,
        )

    assert result != ""


# ---------------------------------------------------------------------------
# Prompt template selection
# ---------------------------------------------------------------------------


def test_build_summary_prompt_uses_nl_template_for_nl() -> None:
    from knowledge_ingest.contextual import _build_summary_prompt

    prompt = _build_summary_prompt(text=_DUTCH_PARAGRAPH, title="Test", language="nl")
    assert "Schrijf een Nederlandse samenvatting" in prompt


def test_build_summary_prompt_uses_en_template_for_en() -> None:
    from knowledge_ingest.contextual import _build_summary_prompt

    prompt = _build_summary_prompt(text=_ENGLISH_PARAGRAPH, title="Test", language="en")
    assert "Write an English summary" in prompt


def test_build_summary_prompt_unknown_lang_uses_en() -> None:
    """Any non-nl language falls through to the English template."""
    from knowledge_ingest.contextual import _build_summary_prompt

    prompt = _build_summary_prompt(text="Some text " * 5, title="t", language="de")
    assert "Write an English summary" in prompt
