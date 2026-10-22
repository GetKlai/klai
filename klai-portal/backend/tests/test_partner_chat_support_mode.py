"""Tests for the one path every widget and partner turn runs.

Three seams are covered:

* ``answer_compose`` prompt + refusal selection — a widget or partner turn gets
  the SUPPORT_CHAT profile in its register, and the helpdesk refusal only
  where the caller asks for it.
* ``partner.py`` plumbing — the widget_config ``tone_register`` field is read
  for widget JWT callers only and threaded into retrieve_context.
* ``chat_completions`` routing — a partner key and every widget reach
  ``widget_turn``; only the internal chat has its own leg.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from helpers import FakeKB, FakeResult, make_partner_auth
from klai_chat_prompts import (
    SUPPORT_BROAD_CHAT_SYSTEM_PROMPT,
    SUPPORT_CHAT_SYSTEM_PROMPT,
    SUPPORT_EXPRESSIVE_CHAT_SYSTEM_PROMPT,
    no_citable_sources_message,
)

from app.services.answer_compose import _compose_answer
from app.services.case_file import Case
from app.services.retrieval_context import KnowledgeTurn, _build_system_prompt
from app.services.widget_reply import Reply

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


def test_build_system_prompt_uses_support_profile():
    prompt = _build_system_prompt([])
    assert prompt.startswith(SUPPORT_CHAT_SYSTEM_PROMPT)
    assert _GROUNDED_ONLY not in prompt
    # The safety-hierarchy block is appended unchanged regardless of profile.
    assert "[Instruction hierarchy and safety]" in prompt


def test_build_system_prompt_does_not_replace_caller_system():
    # An explicit system message still wins over the profile default.
    prompt = _build_system_prompt([], "caller override")
    assert "caller override" in prompt
    assert _SUPPORT_ONLY not in prompt
    assert _GROUNDED_ONLY not in prompt


def test_build_system_prompt_keeps_widget_instructions_and_chunks():
    chunks = [{"chunk_id": "c1", "text": "Reset password via Settings.", "source_url": "https://x.example/help"}]
    prompt = _build_system_prompt(chunks, widget_system_prompt="Be brief.")
    assert _SUPPORT_ONLY in prompt
    assert "Be brief." in prompt
    assert "Reset password via Settings." in prompt
    assert "Do not write URLs" in prompt


# ─── tone_register profile selection ────────────────────────────────────


def test_build_system_prompt_expressive_register_uses_expressive_profile():
    prompt = _build_system_prompt([], tone_register="expressive")
    assert prompt.startswith(SUPPORT_EXPRESSIVE_CHAT_SYSTEM_PROMPT)
    assert _SUPPORT_EXPRESSIVE_ONLY in prompt
    # Both support profiles introduce the bot the same way; the swap must
    # not silently drop the support identity for the expressive register.
    assert _SUPPORT_ONLY in prompt


def test_build_system_prompt_restrained_register_keeps_support_profile():
    prompt = _build_system_prompt([], tone_register="restrained")
    assert prompt.startswith(SUPPORT_CHAT_SYSTEM_PROMPT)
    assert _SUPPORT_RESTRAINED_ONLY in prompt
    assert _SUPPORT_EXPRESSIVE_ONLY not in prompt


def test_build_system_prompt_expressive_does_not_override_caller_system():
    prompt = _build_system_prompt([], "caller override", tone_register="expressive")
    assert "caller override" in prompt
    assert _SUPPORT_EXPRESSIVE_ONLY not in prompt


def test_build_system_prompt_expressive_does_not_override_broad_profile():
    # The consented general-knowledge fallback has one profile for both
    # registers; the visitor's tone choice stops at the strict answer.
    prompt = _build_system_prompt([], broad_mode=True, tone_register="expressive")
    assert prompt.startswith(SUPPORT_BROAD_CHAT_SYSTEM_PROMPT)
    assert _SUPPORT_EXPRESSIVE_ONLY not in prompt


# ─── _compose_answer refusal language ───────────────────


def test_compose_refusal_helpdesk_wording_when_support():
    # No trusted sources/chunks -> nothing citable -> canned refusal.
    text, sources, _decision = _compose_answer(
        "Een antwoord zonder bronnen.",
        [],
        [],
        "Waarom lukt dit niet?",
        helpdesk=True,
        response_language="nl",
    )
    assert sources == []
    assert text == _HELPDESK_DUTCH
    assert "kennisbronnen" not in text


def test_compose_refusal_default_uses_internal_wording():
    text, sources, _decision = _compose_answer(
        "Een antwoord zonder bronnen.",
        [],
        [],
        "Waarom lukt dit niet?",
        response_language="nl",
    )
    assert sources == []
    assert text == _INTERNAL_DUTCH


def test_compose_refusal_helpdesk_english_for_non_dutch_query():
    text, _sources, _decision = _compose_answer(
        "An answer with no sources.",
        [],
        [],
        "Why does this not work?",
        helpdesk=True,
        response_language="en",
    )
    assert "help articles" in text
    assert "appointment" in text.lower()


# ─── tone_register in the widget settings ──────────────────────────────────


@pytest.mark.asyncio
async def test_widget_tone_register_reads_expressive():
    from app.api.partner import _widget_settings

    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult(rows=[{"tone_register": "expressive"}]))
    auth = make_partner_auth()
    auth.key_id = "wgt_abc123"

    assert (await _widget_settings(auth, db)).tone_register == "expressive"


@pytest.mark.asyncio
async def test_widget_tone_register_defaults_restrained():
    from app.api.partner import _widget_settings

    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult(rows=[{"show_sources": True}]))
    auth = make_partner_auth()
    auth.key_id = "wgt_abc123"

    assert (await _widget_settings(auth, db)).tone_register == "restrained"


@pytest.mark.asyncio
async def test_widget_tone_register_falls_back_on_unrecognised_value():
    # A stale or hand-edited config can never switch a live widget to the
    # expressive voice by accident; only the exact value selects it.
    from app.api.partner import _widget_settings

    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult(rows=[{"tone_register": "boisterous"}]))
    auth = make_partner_auth()
    auth.key_id = "wgt_abc123"

    assert (await _widget_settings(auth, db)).tone_register == "restrained"


@pytest.mark.asyncio
async def test_widget_tone_register_ignored_for_partner_keys():
    from app.api.partner import _widget_settings

    db = AsyncMock()
    assert (await _widget_settings(make_partner_auth(), db)).tone_register == "restrained"
    db.execute.assert_not_called()


# ─── chat_completions plumbing ──────────────────────────────────────────


async def _run_chat_completions(*, tone_register_flag: str = "restrained"):
    """Call chat_completions with a widget auth and the flag readers stubbed."""
    from app.api.partner import ChatCompletionsRequest, WidgetSettings, chat_completions

    fake_kbs = [FakeKB(id=10, name="KB Alpha", slug="kb-alpha", org_id=42)]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult(rows=fake_kbs))
    auth = make_partner_auth(kb_access={10: "read"})
    auth.key_id = "wgt_901"

    req = ChatCompletionsRequest(
        messages=[{"role": "user", "content": "Hoe reset ik mijn wachtwoord?"}],
        model="klai-primary",
        stream=True,
    )

    written = Reply(text="", sources=[], signals={}, language=None, turn=KnowledgeTurn())
    with (
        patch("app.services.widget_turn.retrieve_context", return_value=([], "prompt", [], False)) as mock_retrieve,
        patch(
            "app.api.partner._widget_settings",
            new=AsyncMock(return_value=WidgetSettings(tone_register=tone_register_flag)),
        ),
        patch("app.services.widget_turn.write_reply", new=AsyncMock(return_value=written)) as write,
        patch("app.api.partner.asyncio"),
        patch("app.services.widget_turn.write_retrieval_log", new=AsyncMock()),
    ):
        await chat_completions(request=req, http_request=_http_request_stub(), auth=auth, db=db)

    return mock_retrieve, write


@pytest.mark.asyncio
async def test_widget_tone_register_threaded_into_retrieval():
    mock_retrieve, write = await _run_chat_completions()
    write.assert_awaited_once()
    # No tone_register configured on the widget → the restrained default is
    # what reaches retrieval; the writer never sees the field (it only renders
    # the already-built prompt).
    assert mock_retrieve.call_args.kwargs["tone_register"] == "restrained"
    assert "tone_register" not in write.call_args.kwargs


@pytest.mark.asyncio
async def test_widget_expressive_register_threaded_into_retrieval():
    mock_retrieve, _write = await _run_chat_completions(tone_register_flag="expressive")
    assert mock_retrieve.call_args.kwargs["tone_register"] == "expressive"


# ---------------------------------------------------------------------------
# SPEC-RAG-ANSWER-TIERS-001 REQ-1 — the wiring, not just the composer
# ---------------------------------------------------------------------------


async def _run_with_scope(*, chunks, scope_result):
    """Drive the real endpoint with retrieval and the classifier controlled."""
    from app.api.partner import ChatCompletionsRequest, chat_completions

    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult(rows=[FakeKB(id=10, name="KB", slug="kb-a", org_id=42)]))
    auth = make_partner_auth(kb_access={10: "read"})
    auth.key_id = "wgt_901"

    req = ChatCompletionsRequest(
        messages=[{"role": "user", "content": "Can I also talk english?"}],
        model="klai-primary",
        stream=False,
    )

    case = Case(
        problem="",
        product="",
        variant="",
        tried=[],
        wants_quote="",
        wants="chat" if scope_result == "conversation" else "knowledge",
        out_of_scope_quote="",
        out_of_scope=False,
        sentiment="neutral",
        language="",
        queries=[],
    )
    classifier = AsyncMock(return_value=case)
    with (
        patch("app.services.widget_turn.retrieve_context", return_value=(chunks, "SUPPORT PROFILE", [], False)),
        patch("app.api.partner.case_file.update_case", new=classifier),
        patch(
            "app.services.widget_turn.write_reply",
            new=AsyncMock(return_value=Reply(text="", sources=[], signals={}, language=None, turn=KnowledgeTurn())),
        ) as chat_call,
        patch("app.api.partner.asyncio"),
        patch("app.services.widget_turn.write_retrieval_log", new=AsyncMock()),
    ):
        await chat_completions(request=req, http_request=_http_request_stub(), auth=auth, db=db)

    return classifier, chat_call


@pytest.mark.asyncio
async def test_conversational_class_reaches_the_prompt_and_the_composer():
    """The reported turn, through the endpoint rather than the composer alone.

    The composer-level tests stay green even when the classifier is never
    called or its result is never threaded, so on their own they do not prove
    the reported user path.
    """
    classifier, chat_call = await _run_with_scope(chunks=[], scope_result="conversation")

    classifier.assert_awaited_once()
    assert chat_call.call_args.kwargs["conversational"] is True
    # The model must also stop being told to refuse; the composer never reads
    # the words, so a self-written refusal would otherwise pass through.
    assert "[This turn]" in chat_call.call_args.kwargs["system_prompt"]
    assert "about this conversation itself" in chat_call.call_args.kwargs["system_prompt"]


@pytest.mark.asyncio
async def test_a_knowledge_turn_is_not_marked_conversational():
    classifier, chat_call = await _run_with_scope(chunks=[], scope_result="organisation")

    classifier.assert_awaited_once()
    assert chat_call.call_args.kwargs["conversational"] is False
    assert (
        "[This turn] The visitor's message is about this conversation"
        not in (chat_call.call_args.kwargs["system_prompt"])
    )


@pytest.mark.asyncio
async def test_a_grounded_turn_is_never_conversational():
    """Only a retrieval gap leaves the knowledge pipeline; with usable chunks the composer answers."""
    good_chunk = {"chunk_id": "c1", "text": "Ga naar Beheer.", "reranker_score": 0.9}
    classifier, chat_call = await _run_with_scope(chunks=[good_chunk], scope_result="conversation")

    classifier.assert_awaited_once()
    assert chat_call.call_args.kwargs["conversational"] is False


# ---------------------------------------------------------------------------
# One path: every turn that is not the internal chat runs through widget_turn
# ---------------------------------------------------------------------------


async def _one_path_body(auth):
    from app.api.partner import ChatCompletionsRequest, chat_completions

    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult(rows=[FakeKB(id=10, name="KB", slug="kb-a", org_id=42)]))
    req = ChatCompletionsRequest(
        messages=[{"role": "user", "content": "Hoe reset ik mijn wachtwoord?"}],
        model="klai-primary",
        stream=False,
    )
    written = Reply(text="Via Instellingen.", sources=[], signals={}, language="nl", turn=KnowledgeTurn())
    with (
        patch("app.services.widget_turn.retrieve_context", return_value=([], "prompt", [], False)),
        patch("app.services.widget_turn.case_file.update_case", new=AsyncMock(return_value=None)),
        patch("app.services.widget_turn.write_reply", new=AsyncMock(return_value=written)) as write,
        patch("app.services.widget_turn.write_retrieval_log", new=AsyncMock()),
        patch("app.api.partner.asyncio"),
    ):
        body = await chat_completions(request=req, http_request=_http_request_stub(), auth=auth, db=db)
    write.assert_awaited_once()
    return body


@pytest.mark.asyncio
async def test_a_partner_key_turn_gets_the_widget_turn_reply():
    body = await _one_path_body(make_partner_auth(kb_access={10: "read"}))

    assert body["choices"][0]["message"]["content"] == "Via Instellingen."


@pytest.mark.asyncio
async def test_a_widget_without_the_support_toggle_gets_the_widget_turn_reply():
    auth = make_partner_auth(kb_access={10: "read"})
    auth.key_id = "wgt_902"

    body = await _one_path_body(auth)

    assert body["choices"][0]["message"]["content"] == "Via Instellingen."


@pytest.mark.asyncio
async def test_a_partner_key_sending_tools_is_refused_with_a_400():
    from fastapi import HTTPException

    from app.api.partner import ChatCompletionsRequest, chat_completions

    req = ChatCompletionsRequest(
        messages=[{"role": "user", "content": "q"}],
        stream=False,
        tools=[{"type": "function", "function": {"name": "lookup"}}],
    )

    with pytest.raises(HTTPException) as raised:
        await chat_completions(request=req, http_request=_http_request_stub(), auth=make_partner_auth(), db=AsyncMock())

    assert raised.value.status_code == 400
    assert "tools" in str(raised.value.detail)
