"""Tests for the helpdesk / public-widget support mode.

Two seams are covered:

* ``partner_chat`` prompt + refusal selection — the SUPPORT_CHAT profile and
  the helpdesk refusal must be chosen only when ``support_mode`` is on, and
  every other caller must keep the exact GROUNDED / "kennisbronnen" behaviour.
* ``partner.py`` flag plumbing — the widget_config ``support_mode`` flag is
  read for widget JWT callers only and threaded into retrieve_context and the
  completion functions.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from helpers import FakeKB, FakeResult, make_partner_auth
from klai_chat_prompts import (
    GROUNDED_CHAT_SYSTEM_PROMPT,
    SUPPORT_CHAT_SYSTEM_PROMPT,
)

from app.services.partner_chat import (
    _build_system_prompt,
    _compose_backend_managed_answer,
)

# Signature phrases that exist in exactly one of the two profiles, so a
# prompt built with the wrong base is caught immediately.
_GROUNDED_ONLY = "senior colleague"
_SUPPORT_ONLY = "AI support assistant"

_HELPDESK_DUTCH = (
    "Ik kan dit niet betrouwbaar beantwoorden op basis van onze helpartikelen. "
    "Neem voor een vast antwoord contact op met de support."
)
_INTERNAL_DUTCH = "Ik kan dit niet betrouwbaar beantwoorden op basis van de beschikbare kennisbronnen."


def _http_request_stub():
    from unittest.mock import MagicMock

    req = MagicMock()
    req.headers = {}
    req.client = MagicMock(host="127.0.0.1")
    return req


# ─── _build_system_prompt profile selection ─────────────────────────────


def test_build_system_prompt_default_uses_grounded():
    prompt = _build_system_prompt([])
    assert _GROUNDED_ONLY in prompt
    assert _SUPPORT_ONLY not in prompt
    # The safety-hierarchy block is appended unchanged regardless of mode.
    assert "[Instruction hierarchy and safety]" in prompt


def test_build_system_prompt_support_mode_uses_support_profile():
    prompt = _build_system_prompt([], support_mode=True)
    assert _SUPPORT_ONLY in prompt
    assert _GROUNDED_ONLY not in prompt
    # SUPPORT shares the language-detection preamble verbatim with GROUNDED.
    assert prompt.startswith(SUPPORT_CHAT_SYSTEM_PROMPT)
    preamble = GROUNDED_CHAT_SYSTEM_PROMPT[: GROUNDED_CHAT_SYSTEM_PROMPT.find("\n\nYou are Klai AI")]
    assert prompt.startswith(preamble)


def test_build_system_prompt_support_mode_does_not_replace_caller_system():
    # An explicit system message still wins over the profile default, exactly
    # as in GROUNDED mode — support_mode only chooses the fallback profile.
    prompt = _build_system_prompt([], "caller override", support_mode=True)
    assert "caller override" in prompt
    assert _SUPPORT_ONLY not in prompt
    assert _GROUNDED_ONLY not in prompt


def test_build_system_prompt_support_mode_keeps_widget_instructions_and_chunks():
    chunks = [{"chunk_id": "c1", "text": "Reset password via Settings.", "source_url": "https://x.example/help"}]
    prompt = _build_system_prompt(
        chunks,
        widget_system_prompt="Be brief.",
        support_mode=True,
        backend_managed_citations=True,
    )
    assert _SUPPORT_ONLY in prompt
    assert "Be brief." in prompt
    assert "Reset password via Settings." in prompt
    # Backend-managed source rule is unchanged by support_mode.
    assert "Do not write URLs" in prompt


# ─── _compose_backend_managed_answer refusal language ───────────────────


def test_compose_refusal_helpdesk_wording_when_support():
    # No trusted sources/chunks -> nothing citable -> canned refusal.
    text, sources, _decision = _compose_backend_managed_answer(
        "Een antwoord zonder bronnen.",
        [],
        [],
        "Waarom lukt dit niet?",
        helpdesk=True,
    )
    assert sources == []
    assert text == _HELPDESK_DUTCH
    assert "kennisbronnen" not in text


def test_compose_refusal_default_uses_internal_wording():
    text, sources, _decision = _compose_backend_managed_answer(
        "Een antwoord zonder bronnen.",
        [],
        [],
        "Waarom lukt dit niet?",
    )
    assert sources == []
    assert text == _INTERNAL_DUTCH


def test_compose_refusal_helpdesk_english_for_non_dutch_query():
    text, _sources, _decision = _compose_backend_managed_answer(
        "An answer with no sources.",
        [],
        [],
        "Why does this not work?",
        helpdesk=True,
    )
    assert "help articles" in text
    assert "support" in text.lower()


# ─── _widget_support_mode_enabled flag reader ───────────────────────────


@pytest.mark.asyncio
async def test_widget_support_mode_enabled_reads_flag():
    from app.api.partner import _widget_support_mode_enabled

    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult(rows=[{"support_mode": True}]))
    auth = make_partner_auth()
    auth.key_id = "wgt_abc123"

    assert await _widget_support_mode_enabled(auth, db) is True


@pytest.mark.asyncio
async def test_widget_support_mode_disabled_by_default():
    from app.api.partner import _widget_support_mode_enabled

    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult(rows=[{"show_sources": True}]))
    auth = make_partner_auth()
    auth.key_id = "wgt_abc123"

    assert await _widget_support_mode_enabled(auth, db) is False


@pytest.mark.asyncio
async def test_widget_support_mode_ignored_for_partner_keys():
    from app.api.partner import _widget_support_mode_enabled

    db = AsyncMock()
    assert await _widget_support_mode_enabled(make_partner_auth(), db) is False
    db.execute.assert_not_called()


# ─── chat_completions plumbing ──────────────────────────────────────────


async def _run_chat_completions(*, support_mode_flag: bool, stream: bool):
    """Call chat_completions with a widget auth and the flag reader stubbed."""
    from app.api.partner import ChatCompletionsRequest, chat_completions

    fake_kbs = [FakeKB(id=10, name="KB Alpha", slug="kb-alpha", org_id=42)]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult(rows=fake_kbs))
    auth = make_partner_auth(kb_access={10: "read"})
    auth.key_id = "wgt_901"

    req = ChatCompletionsRequest(
        messages=[{"role": "user", "content": "Hoe reset ik mijn wachtwoord?"}],
        model="klai-primary",
        stream=stream,
    )

    async def mock_streaming_gen():
        yield b"data: [DONE]\n\n"

    with (
        patch("app.api.partner.retrieve_context", return_value=([], "prompt", [])) as mock_retrieve,
        patch("app.api.partner._widget_page_context_enabled", new=AsyncMock(return_value=False)),
        patch(
            "app.api.partner._widget_support_mode_enabled",
            new=AsyncMock(return_value=support_mode_flag),
        ),
        patch(
            "app.api.partner.chat_completion_streaming",
            return_value=mock_streaming_gen(),
        ) as chat_stream,
        patch(
            "app.api.partner.chat_completion_non_streaming",
            new=AsyncMock(return_value={"choices": []}),
        ) as chat_nonstream,
        patch("app.api.partner.asyncio"),
        patch("app.api.partner.write_retrieval_log", new=AsyncMock()),
    ):
        await chat_completions(request=req, http_request=_http_request_stub(), auth=auth, db=db)

    return mock_retrieve, (chat_stream if stream else chat_nonstream)


@pytest.mark.asyncio
async def test_widget_support_mode_threaded_into_retrieval_and_streaming():
    mock_retrieve, chat_stream = await _run_chat_completions(support_mode_flag=True, stream=True)
    assert mock_retrieve.call_args.kwargs["support_mode"] is True
    assert chat_stream.call_args.kwargs["support_mode"] is True


@pytest.mark.asyncio
async def test_widget_support_mode_threaded_into_non_streaming():
    mock_retrieve, chat_nonstream = await _run_chat_completions(support_mode_flag=True, stream=False)
    assert mock_retrieve.call_args.kwargs["support_mode"] is True
    assert chat_nonstream.call_args.kwargs["support_mode"] is True


@pytest.mark.asyncio
async def test_widget_support_mode_defaults_off():
    mock_retrieve, chat_stream = await _run_chat_completions(support_mode_flag=False, stream=True)
    assert mock_retrieve.call_args.kwargs["support_mode"] is False
    assert chat_stream.call_args.kwargs["support_mode"] is False
