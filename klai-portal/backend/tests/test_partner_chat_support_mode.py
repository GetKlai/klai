"""Tests for the helpdesk / public-widget support mode.

Two seams are covered:

* ``partner_chat`` prompt + refusal selection — the SUPPORT_CHAT profile and
  the helpdesk refusal must be chosen only when ``support_mode`` is on, and
  every other caller must keep the exact GROUNDED / "kennisbronnen" behaviour.
* ``partner.py`` flag plumbing — the widget_config ``support_mode`` flag and
  the ``tone_register`` field are read for widget JWT callers only and
  threaded into retrieve_context and the completion functions.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from helpers import FakeKB, FakeResult, make_partner_auth
from klai_chat_prompts import (
    GROUNDED_CHAT_SYSTEM_PROMPT,
    SUPPORT_BROAD_CHAT_SYSTEM_PROMPT,
    SUPPORT_CHAT_SYSTEM_PROMPT,
    SUPPORT_EXPRESSIVE_CHAT_SYSTEM_PROMPT,
    no_citable_sources_message,
)

from app.services.partner_chat import (
    _build_system_prompt,
    _compose_backend_managed_answer,
)

# Signature phrases that exist in exactly one of the profiles, so a
# prompt built with the wrong base is caught immediately.
_GROUNDED_ONLY = "senior colleague"
_SUPPORT_ONLY = "AI support assistant"
_SUPPORT_RESTRAINED_ONLY = "Customer-friendly but businesslike"
_SUPPORT_EXPRESSIVE_ONLY = "Expressive register"

# Derived from the single source of truth rather than copied: a hardcoded copy
# here silently pins the wording and fails the day the brand voice changes,
# which is exactly what happened when this text was rewritten.
# Derived from the single source of truth rather than copied, per the
# rendered language CODES ("nl"/"en") — the helper no longer guesses.
_HELPDESK_DUTCH = no_citable_sources_message("nl", helpdesk=True)
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


# ─── tone_register profile selection ────────────────────────────────────


def test_build_system_prompt_expressive_register_uses_expressive_profile():
    prompt = _build_system_prompt([], support_mode=True, tone_register="expressive")
    assert prompt.startswith(SUPPORT_EXPRESSIVE_CHAT_SYSTEM_PROMPT)
    assert _SUPPORT_EXPRESSIVE_ONLY in prompt
    # Both support profiles introduce the bot the same way; the swap must
    # not silently drop the support identity for the expressive register.
    assert _SUPPORT_ONLY in prompt


def test_build_system_prompt_restrained_register_keeps_support_profile():
    prompt = _build_system_prompt([], support_mode=True, tone_register="restrained")
    assert prompt.startswith(SUPPORT_CHAT_SYSTEM_PROMPT)
    assert _SUPPORT_RESTRAINED_ONLY in prompt
    assert _SUPPORT_EXPRESSIVE_ONLY not in prompt


def test_build_system_prompt_register_defaults_to_restrained():
    # The key regression: widget configs written before the field existed
    # carry no tone_register at all and must keep the exact current prompt.
    prompt = _build_system_prompt([], support_mode=True)
    assert prompt.startswith(SUPPORT_CHAT_SYSTEM_PROMPT)
    assert _SUPPORT_EXPRESSIVE_ONLY not in prompt


def test_build_system_prompt_register_ignored_without_support_mode():
    # An internal widget has no visitor to sound expressive for: the
    # register must not reach the GROUNDED profile.
    prompt = _build_system_prompt([], tone_register="expressive")
    assert _GROUNDED_ONLY in prompt
    assert _SUPPORT_EXPRESSIVE_ONLY not in prompt


def test_build_system_prompt_expressive_does_not_override_caller_system():
    prompt = _build_system_prompt([], "caller override", support_mode=True, tone_register="expressive")
    assert "caller override" in prompt
    assert _SUPPORT_EXPRESSIVE_ONLY not in prompt


def test_build_system_prompt_expressive_does_not_override_broad_profile():
    # The consented general-knowledge fallback has one profile for both
    # registers; the visitor's tone choice stops at the strict answer.
    prompt = _build_system_prompt([], support_mode=True, broad_mode=True, tone_register="expressive")
    assert prompt.startswith(SUPPORT_BROAD_CHAT_SYSTEM_PROMPT)
    assert _SUPPORT_EXPRESSIVE_ONLY not in prompt


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
    assert "appointment" in text.lower()


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


# ─── _widget_tone_register flag reader ──────────────────────────────────


@pytest.mark.asyncio
async def test_widget_tone_register_reads_expressive():
    from app.api.partner import _widget_tone_register

    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult(rows=[{"support_mode": True, "tone_register": "expressive"}]))
    auth = make_partner_auth()
    auth.key_id = "wgt_abc123"

    assert await _widget_tone_register(auth, db) == "expressive"


@pytest.mark.asyncio
async def test_widget_tone_register_defaults_restrained():
    from app.api.partner import _widget_tone_register

    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult(rows=[{"support_mode": True}]))
    auth = make_partner_auth()
    auth.key_id = "wgt_abc123"

    assert await _widget_tone_register(auth, db) == "restrained"


@pytest.mark.asyncio
async def test_widget_tone_register_falls_back_on_unrecognised_value():
    # A stale or hand-edited config can never switch a live widget to the
    # expressive voice by accident; only the exact value selects it.
    from app.api.partner import _widget_tone_register

    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult(rows=[{"tone_register": "boisterous"}]))
    auth = make_partner_auth()
    auth.key_id = "wgt_abc123"

    assert await _widget_tone_register(auth, db) == "restrained"


@pytest.mark.asyncio
async def test_widget_tone_register_ignored_for_partner_keys():
    from app.api.partner import _widget_tone_register

    db = AsyncMock()
    assert await _widget_tone_register(make_partner_auth(), db) == "restrained"
    db.execute.assert_not_called()


# ─── chat_completions plumbing ──────────────────────────────────────────


async def _run_chat_completions(*, support_mode_flag: bool, stream: bool, tone_register_flag: str = "restrained"):
    """Call chat_completions with a widget auth and the flag readers stubbed."""
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

    tone_reader = AsyncMock(return_value=tone_register_flag)
    with (
        patch("app.api.partner.retrieve_context", return_value=([], "prompt", [], False)) as mock_retrieve,
        patch("app.api.partner._widget_page_context_enabled", new=AsyncMock(return_value=False)),
        patch(
            "app.api.partner._widget_support_mode_enabled",
            new=AsyncMock(return_value=support_mode_flag),
        ),
        patch(
            "app.api.partner._widget_tone_register",
            new=tone_reader,
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

    return mock_retrieve, (chat_stream if stream else chat_nonstream), tone_reader


@pytest.mark.asyncio
async def test_widget_support_mode_threaded_into_retrieval_and_streaming():
    mock_retrieve, chat_stream, _tone_reader = await _run_chat_completions(support_mode_flag=True, stream=True)
    assert mock_retrieve.call_args.kwargs["support_mode"] is True
    assert chat_stream.call_args.kwargs["support_mode"] is True
    # No tone_register configured on the widget → the restrained default is
    # what reaches retrieval; the completion functions never see the field
    # (they only render the already-built prompt).
    assert mock_retrieve.call_args.kwargs["tone_register"] == "restrained"
    assert "tone_register" not in chat_stream.call_args.kwargs


@pytest.mark.asyncio
async def test_widget_support_mode_threaded_into_non_streaming():
    mock_retrieve, chat_nonstream, _tone_reader = await _run_chat_completions(support_mode_flag=True, stream=False)
    assert mock_retrieve.call_args.kwargs["support_mode"] is True
    assert chat_nonstream.call_args.kwargs["support_mode"] is True
    assert mock_retrieve.call_args.kwargs["tone_register"] == "restrained"
    assert "tone_register" not in chat_nonstream.call_args.kwargs


@pytest.mark.asyncio
async def test_widget_support_mode_defaults_off():
    mock_retrieve, chat_stream, _tone_reader = await _run_chat_completions(support_mode_flag=False, stream=True)
    assert mock_retrieve.call_args.kwargs["support_mode"] is False
    assert chat_stream.call_args.kwargs["support_mode"] is False


@pytest.mark.asyncio
async def test_widget_expressive_register_threaded_into_retrieval():
    mock_retrieve, _chat_stream, _tone_reader = await _run_chat_completions(
        support_mode_flag=True, stream=True, tone_register_flag="expressive"
    )
    assert mock_retrieve.call_args.kwargs["support_mode"] is True
    assert mock_retrieve.call_args.kwargs["tone_register"] == "expressive"


@pytest.mark.asyncio
async def test_widget_tone_register_not_read_while_support_mode_off():
    # An expressive flag on an internal widget must not reach the prompt:
    # the reader is not even consulted when support mode is off, so the
    # default restrained value is what retrieve_context receives.
    mock_retrieve, _chat_stream, tone_reader = await _run_chat_completions(
        support_mode_flag=False, stream=True, tone_register_flag="expressive"
    )
    tone_reader.assert_not_called()
    assert mock_retrieve.call_args.kwargs["tone_register"] == "restrained"
