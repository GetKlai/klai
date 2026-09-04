"""Broad mode: consented general-knowledge fallback for the helpdesk widget.

The help-page bot answers strictly from help articles. When retrieval came up
empty or weak, the visitor can explicitly consent ("kijk breder") and the bot
then answers from general domain knowledge — labelled, never citing articles,
never for company-specific claims (the boundary lives in the SUPPORT_BROAD
prompt profile, tested in klai-libs/chat-prompts).

Covered seams:

* ``_broad_mode_active`` — the single predicate that turns consent + a real
  retrieval gap into a broad turn (and nothing else).
* ``retrieve_context`` — profile swap + source clearing, and the invariant
  that the knowledge-gap event still fires on a broad answer.
* ``_compose_backend_managed_answer`` — the marker is applied here (never by
  the model), and the offer signal tags only public helpdesk refusals.
* ``_chat_completion_streaming_with_composed_citations`` — the widget-facing
  ``delta.broad_mode`` frames ("offer" / "answer"), including suppression on
  safety-blocked output.
* ``chat_completion_non_streaming`` — ``message.broad_mode`` on the marker
  path.
* ``partner.py`` plumbing — the request-level consent flag and the
  no-chunks handoff on a broad turn.

The byte-identity of the no-consent path is guarded by the existing suites;
these tests only pin the new behaviour.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from klai_chat_prompts import (
    SUPPORT_BROAD_CHAT_SYSTEM_PROMPT,
    SUPPORT_CHAT_SYSTEM_PROMPT,
    broad_mode_answer_marker,
    no_citable_sources_message,
)

from app.services import partner_chat
from app.services.partner_chat import (
    _broad_mode_active,
    _chat_completion_streaming_with_composed_citations,
    _compose_backend_managed_answer,
)

_HELPDESK_REFUSAL_NL = no_citable_sources_message("de", helpdesk=True)


def _http_request_stub():
    req = MagicMock()
    req.headers = {}
    req.client = MagicMock(host="127.0.0.1")
    return req


def _good_chunk() -> dict[str, Any]:
    return {
        "chunk_id": "c1",
        "text": "Je reset het wachtwoord via Instellingen > Beveiliging.",
        "source_url": "https://example.com/reset",
        "reranker_score": 0.9,
    }


def _weak_chunk() -> dict[str, Any]:
    return {
        "chunk_id": "c9",
        "text": "Ontrelevant stukje tekst over iets anders.",
        "source_url": "https://example.com/other",
        "reranker_score": 0.05,
    }


def _parse_frames(chunks: list[bytes]) -> list[dict]:
    out: list[dict] = []
    for raw in chunks:
        text = raw.decode()
        assert text.startswith("data: ")
        payload = text[6:].strip()
        if payload and payload != "[DONE]":
            out.append(json.loads(payload))
    return out


def _delta_values(frames: list[dict], key: str) -> list[Any]:
    values: list[Any] = []
    for frame in frames:
        for choice in frame.get("choices") or []:
            delta = choice.get("delta") or {}
            if key in delta:
                values.append(delta[key])
    return values


# ─── _broad_mode_active: the predicate ───────────────────────────────────


def test_broad_needs_support_mode_and_consent_and_a_gap():
    assert _broad_mode_active([], support_mode=False, broad_consent=True) is False
    assert _broad_mode_active([], support_mode=True, broad_consent=False) is False
    assert _broad_mode_active([], support_mode=True, broad_consent=True) is True


def test_broad_fires_on_hard_gaps_only():
    """Consent is permission to fall back, not an instruction to stop using the
    articles. A soft gap means retrieval DID return something — weak, but it can
    still carry a grounded answer through the rescue thresholds — so it stays
    grounded. Treating soft as broad made one "what is DECT?" turn every later
    question into general knowledge with the chunks thrown away."""
    assert _broad_mode_active([], support_mode=True, broad_consent=True) is True  # hard
    assert _broad_mode_active([_weak_chunk()], support_mode=True, broad_consent=True) is False  # soft
    assert _broad_mode_active([_good_chunk()], support_mode=True, broad_consent=True) is False


# ─── composer: marker applied here, offer tags public refusals ───────────


def test_compose_broad_answer_prefixes_marker_and_drops_sources():
    text, sources, decision = _compose_backend_managed_answer(
        "Een SIP trunk is een virtuele telefoonlijn.",
        [{"title": "Art", "url": "https://example.com/a"}],  # ignored: broad never cites
        [_good_chunk()],
        "wat is een sip trunk?",
        helpdesk=True,
        broad=True,
    )
    marker = broad_mode_answer_marker("wat is een sip trunk?")
    assert text == f"{marker}\n\nEen SIP trunk is een virtuele telefoonlijn."
    assert sources == []
    assert decision["reason"] == "broad_mode_answer"
    assert decision["broad_mode"] == "answer"


def test_compose_broad_answer_english_marker_for_english_query():
    text, _, _ = _compose_backend_managed_answer(
        "A SIP trunk is a virtual phone line.",
        [],
        [],
        "what is a sip trunk",
        helpdesk=True,
        broad=True,
    )
    assert text.startswith("General knowledge — not from our help articles.")


def test_compose_broad_empty_output_falls_back_to_refusal_without_signals():
    # Model produced nothing even with broad consent: honest helpdesk refusal,
    # unlabelled (nothing was claimed as general knowledge) and no offer
    # re-pitch (consent already happened this turn).
    text, sources, decision = _compose_backend_managed_answer(
        "   ", [], [], "wat is een sip trunk?", helpdesk=True, broad=True
    )
    assert text == no_citable_sources_message("wat is een sip trunk?", helpdesk=True)
    assert sources == []
    assert "broad_mode" not in decision


def test_compose_helpdesk_refusal_tags_offer():
    # No usable evidence + helpdesk wording ⇒ the widget may now offer broad
    # mode. The refusal text itself stays byte-identical (widget_outcome rule 1
    # matches on it verbatim).
    text, sources, decision = _compose_backend_managed_answer(
        "Ik weet het ook niet.",  # model text, but nothing citable supports it
        [],
        [],
        "wat kost het abonnement?",
        helpdesk=True,
    )
    assert text == _HELPDESK_REFUSAL_NL  # byte-identical canned refusal
    assert sources == []
    assert decision.get("broad_mode") == "offer"


def test_compose_partner_refusal_does_not_tag_offer():
    _, _, decision = _compose_backend_managed_answer(
        "Whatever the model said.", [], [], "what is the price", helpdesk=False
    )
    assert "broad_mode" not in decision


def test_compose_grounded_helpdesk_answer_carries_no_broad_signal():
    chunk = _good_chunk()
    answer = "Je reset het wachtwoord via Instellingen > Beveiliging. [1]"
    _text, sources, decision = _compose_backend_managed_answer(
        answer,
        [{"label": "1", "title": "Reset", "url": "https://example.com/reset", "evidence_ids": ["c1"]}],
        [chunk],
        "hoe reset ik mijn wachtwoord",
        helpdesk=True,
    )
    assert "broad_mode" not in decision
    assert sources


# ─── retrieve_context: profile swap + gap-event coexistence ──────────────


def _stub_gap_writer(monkeypatch) -> AsyncMock:
    """Silence the fire-and-forget gap writer (real path needs a live DB
    session); returns the mock so tests can assert on it."""
    mock = AsyncMock()
    monkeypatch.setattr("app.services.partner_chat.record_gap_event", mock)
    return mock


def _patch_retrieve(monkeypatch, payload: dict[str, Any]) -> None:
    class _MockResp:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    class _MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, url, json=None, headers=None):
            return _MockResp()

    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", lambda timeout: _MockClient())


def _fake_settings() -> MagicMock:
    s = MagicMock()
    s.knowledge_retrieve_url = "http://retrieval-api:8040"
    s.retrieval_api_internal_secret = "secret"
    s.internal_secret = "fallback"
    return s


async def _retrieve(**overrides: Any):
    kwargs: dict[str, Any] = {
        "org_id": 42,
        "zitadel_org_id": "zit-org-1",
        "kb_slugs": ["kb-alpha"],
        "messages": [{"role": "user", "content": "wat is een sip trunk?"}],
        "settings": _fake_settings(),
    }
    kwargs.update(overrides)
    return await partner_chat.retrieve_context(**kwargs)


_EVIDENCE_PACK_NL = {
    "evidence_pack": {
        "items": [
            {
                "chunk_id": "c1",
                "text": "Een SIP trunk is een virtuele telefoonlijn via het internet.",
                "source_url": "https://example.com/sip",
                "reranker_score": 0.9,
            }
        ],
        "sources": [
            {
                "source_id": "s1",
                "title": "SIP uitleg",
                "source_url": "https://example.com/sip",
                "evidence_ids": ["c1"],
            }
        ],
    }
}

_EVIDENCE_PACK_WEAK = {
    "evidence_pack": {
        "items": [
            {
                "chunk_id": "c9",
                "text": "Onzeker stukje over iets anders.",
                "source_url": "https://example.com/other",
                "score": None,  # never reranked → soft gap via dense fallback
            }
        ],
        "sources": [
            {
                "source_id": "s9",
                "title": "Iets anders",
                "source_url": "https://example.com/other",
                "evidence_ids": ["c9"],
            }
        ],
    }
}


@pytest.mark.asyncio
async def test_retrieve_consent_plus_gap_swaps_profile_and_clears_sources(monkeypatch):
    _patch_retrieve(monkeypatch, {"chunks": []})
    _stub_gap_writer(monkeypatch)

    chunks, prompt, trusted_sources, broad = await _retrieve(support_mode=True, broad_mode=True)

    assert broad is True
    assert chunks == []
    # Nothing downstream may cite on a broad turn.
    assert trusted_sources == []
    # The model saw the broad profile, not the strict SUPPORT one.
    assert SUPPORT_BROAD_CHAT_SYSTEM_PROMPT in prompt
    assert "Broad mode" in prompt  # phrase unique to the broad profile


@pytest.mark.asyncio
async def test_retrieve_consent_soft_gap_stays_grounded(monkeypatch):
    """Weak-but-present retrieval (soft gap) stays GROUNDED even with consent
    given. The articles did return something; the rescue thresholds downstream
    decide whether it can carry an answer. Consent is permission to fall back
    when there is nothing, not a switch that discards what retrieval found."""
    _patch_retrieve(monkeypatch, _EVIDENCE_PACK_WEAK)
    _stub_gap_writer(monkeypatch)

    chunks, prompt, trusted_sources, broad = await _retrieve(support_mode=True, broad_mode=True)

    assert broad is False
    assert len(chunks) == 1
    assert trusted_sources != []  # the weak chunk is still a candidate source
    assert SUPPORT_BROAD_CHAT_SYSTEM_PROMPT not in prompt


@pytest.mark.asyncio
async def test_retrieve_gap_event_still_fires_on_broad_answer(monkeypatch):
    """A broad answer is still a knowledge gap: consent must not silence the
    gap registration that the strict refusal path produces."""
    _patch_retrieve(monkeypatch, {"chunks": []})
    mock_record = _stub_gap_writer(monkeypatch)

    _, _, _, broad = await _retrieve(support_mode=True, broad_mode=True)
    tasks = list(partner_chat._pending_gap_tasks)
    if tasks:
        await asyncio.gather(*tasks)

    assert broad is True
    mock_record.assert_awaited_once()
    assert mock_record.await_args.kwargs["gap_type"] == "hard"


@pytest.mark.asyncio
async def test_retrieve_strong_results_win_over_consent(monkeypatch):
    """Consent only opens the *fallback*: when the articles answer, the strict
    profile is kept and sources flow normally."""
    _patch_retrieve(monkeypatch, _EVIDENCE_PACK_NL)
    _stub_gap_writer(monkeypatch)

    chunks, prompt, trusted_sources, broad = await _retrieve(support_mode=True, broad_mode=True)

    assert broad is False
    assert len(chunks) == 1
    assert trusted_sources  # untouched on grounded turns
    assert SUPPORT_CHAT_SYSTEM_PROMPT in prompt
    assert "Broad mode" not in prompt
    assert "Een SIP trunk is een virtuele telefoonlijn" in prompt


@pytest.mark.asyncio
async def test_retrieve_without_consent_never_broad(monkeypatch):
    _patch_retrieve(monkeypatch, {"chunks": []})
    _stub_gap_writer(monkeypatch)

    _, prompt, _, broad = await _retrieve(support_mode=True, broad_mode=False)

    assert broad is False
    assert SUPPORT_CHAT_SYSTEM_PROMPT in prompt
    assert "Broad mode" not in prompt


@pytest.mark.asyncio
async def test_retrieve_consent_ignored_for_partner_keys(monkeypatch):
    """broad_mode is widget-only: a partner API key request must keep the
    GROUNDED profile even with the flag set."""
    _patch_retrieve(monkeypatch, {"chunks": []})
    _stub_gap_writer(monkeypatch)

    _, prompt, _, broad = await _retrieve(support_mode=False, broad_mode=True)

    assert broad is False
    assert "Broad mode" not in prompt


@pytest.mark.asyncio
async def test_retrieve_early_returns_never_broad(monkeypatch):
    """Consent without an actual retrieval attempt stays strict (documented):
    retrieval disabled → broad=False even on a support widget."""

    class _NoClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, *_, **__):
            raise AssertionError("retrieval-api must not be called")

    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", lambda timeout: _NoClient())

    _, prompt, _, broad = await _retrieve(support_mode=True, broad_mode=True, retrieval_enabled=False)
    assert broad is False
    assert "Broad mode" not in prompt


# ─── streaming frames: widget-facing signal ──────────────────────────────


def _stream_patches(monkeypatch, model_text: str):
    """Stand in for the LiteLLM streaming call with a canned model output."""

    class _MockResp:
        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            payload = json.dumps({"choices": [{"index": 0, "delta": {"content": model_text}}]})
            yield f"data: {payload}"
            yield "data: [DONE]"

    class _StreamCtx:
        async def __aenter__(self):
            return _MockResp()

        async def __aexit__(self, *_):
            return None

    class _MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        def stream(self, *_, **__):
            return _StreamCtx()

    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", lambda timeout: _MockClient())


async def _collect(**kwargs) -> list[bytes]:
    return [frame async for frame in _chat_completion_streaming_with_composed_citations(**kwargs)]


@pytest.mark.asyncio
async def test_stream_broad_answer_emits_answer_frame_with_marker(monkeypatch):
    _stream_patches(monkeypatch, "Een DECT-telefoon is een draadloze telefoon.")
    settings = MagicMock()
    settings.litellm_base_url = "http://litellm:4000"
    settings.litellm_master_key = "key"

    frames = await _collect(
        augmented_messages=[{"role": "user", "content": "wat is dect"}],
        model="klai-primary",
        temperature=0.7,
        settings=settings,
        org_id=42,
        user_query="wat is dect?",
        trusted_sources=[],
        citation_chunks=[],
        support_mode=True,
        broad_mode=True,
    )

    parsed = _parse_frames(frames)
    assert _delta_values(parsed, "broad_mode") == ["answer"]
    content = "".join(_delta_values(parsed, "content"))
    assert content.startswith(broad_mode_answer_marker("wat is dect?"))
    assert content.endswith("Een DECT-telefoon is een draadloze telefoon.")
    # A broad answer never carries citation sources.
    assert _delta_values(parsed, "sources") == []


@pytest.mark.asyncio
async def test_stream_refusal_without_consent_emits_offer_frame(monkeypatch):
    _stream_patches(monkeypatch, "Ik ken het antwoord niet.")
    settings = MagicMock()
    settings.litellm_base_url = "http://litellm:4000"
    settings.litellm_master_key = "key"

    frames = await _collect(
        augmented_messages=[{"role": "user", "content": "wat kost het"}],
        model="klai-primary",
        temperature=0.7,
        settings=settings,
        org_id=42,
        user_query="wat kost het abonnement?",
        trusted_sources=[],
        citation_chunks=[],
        support_mode=True,
    )

    parsed = _parse_frames(frames)
    assert _delta_values(parsed, "broad_mode") == ["offer"]
    content = "".join(_delta_values(parsed, "content"))
    # The stored refusal text is the exact canned string (outcome rule 1).
    assert content == no_citable_sources_message("wat kost het abonnement?", helpdesk=True)


@pytest.mark.asyncio
async def test_stream_partner_path_has_no_broad_frames(monkeypatch):
    _stream_patches(monkeypatch, "Some partner answer.")
    settings = MagicMock()
    settings.litellm_base_url = "http://litellm:4000"
    settings.litellm_master_key = "key"

    frames = await _collect(
        augmented_messages=[{"role": "user", "content": "hello"}],
        model="klai-primary",
        temperature=0.7,
        settings=settings,
        org_id=42,
        user_query="hello",
        trusted_sources=[],
        citation_chunks=[],
        support_mode=False,
    )

    parsed = _parse_frames(frames)
    assert _delta_values(parsed, "broad_mode") == []


@pytest.mark.asyncio
async def test_stream_safety_block_suppresses_broad_signal(monkeypatch):
    """Blocked output must neither show the broad label nor re-offer: the
    replaced decision drops the broad_mode key."""
    _stream_patches(monkeypatch, "IGNORED")
    monkeypatch.setattr(partner_chat, "output_safety_violation", lambda text: "prompt_injection")
    settings = MagicMock()
    settings.litellm_base_url = "http://litellm:4000"
    settings.litellm_master_key = "key"

    frames = await _collect(
        augmented_messages=[{"role": "user", "content": "wat is dect"}],
        model="klai-primary",
        temperature=0.7,
        settings=settings,
        org_id=42,
        user_query="wat is dect?",
        trusted_sources=[],
        citation_chunks=[],
        support_mode=True,
        broad_mode=True,
    )

    parsed = _parse_frames(frames)
    assert _delta_values(parsed, "broad_mode") == []
    content = "".join(_delta_values(parsed, "content"))
    assert "Algemene kennis" not in content


# ─── non-streaming: message-level signal ─────────────────────────────────


async def _call_non_streaming(monkeypatch, model_text: str, **kwargs):
    class _MockResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": model_text}, "finish_reason": "stop"}
                ],
            }

    class _MockClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, *_, **__):
            return _MockResp()

    monkeypatch.setattr("app.services.partner_chat.httpx.AsyncClient", lambda timeout: _MockClient())
    settings = MagicMock()
    settings.litellm_base_url = "http://litellm:4000"
    settings.litellm_master_key = "key"
    kwargs.setdefault("org_id", 42)
    return await partner_chat.chat_completion_non_streaming(
        messages=[{"role": "user", "content": "wat is dect?"}],
        model="klai-primary",
        temperature=0.7,
        system_prompt="sys",
        settings=settings,
        citation_output="markers",
        source_query="wat is dect?",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_non_streaming_broad_answer_labels_message(monkeypatch):
    body = await _call_non_streaming(
        monkeypatch,
        "Een DECT-telefoon is een draadloze telefoon.",
        support_mode=True,
        broad_mode=True,
    )
    message = body["choices"][0]["message"]
    assert message["broad_mode"] == "answer"
    assert message["content"].startswith(broad_mode_answer_marker("wat is dect?"))
    assert message["sources"] == []


@pytest.mark.asyncio
async def test_non_streaming_refusal_without_consent_offers(monkeypatch):
    body = await _call_non_streaming(monkeypatch, "Ik ken het antwoord niet.", support_mode=True)
    message = body["choices"][0]["message"]
    assert message["broad_mode"] == "offer"
    assert message["content"] == no_citable_sources_message("wat is dect?", helpdesk=True)


@pytest.mark.asyncio
async def test_non_streaming_partner_has_no_broad_key(monkeypatch):
    body = await _call_non_streaming(monkeypatch, "Partner answer.")
    message = body["choices"][0]["message"]
    assert "broad_mode" not in message


# ─── partner.py plumbing ─────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("consent", "broad_flag"),
    [(True, True), (True, False), (False, False)],
    ids=["consent-and-gap", "consent-no-gap", "no-consent"],
)
async def test_api_threads_consent_and_clears_chunks_on_broad_turn(consent: bool, broad_flag: bool):
    from helpers import FakeKB, FakeResult, make_partner_auth

    from app.api.partner import ChatCompletionsRequest, chat_completions

    weak = [_weak_chunk()]
    req = ChatCompletionsRequest(
        messages=[{"role": "user", "content": "wat is een sip trunk?"}],
        model="klai-primary",
        stream=True,
        broad_mode=consent,
    )
    db = AsyncMock()
    db.execute = AsyncMock(return_value=FakeResult(rows=[FakeKB(id=10, name="KB Alpha", slug="kb-alpha", org_id=42)]))
    auth = make_partner_auth(kb_access={10: "read"})
    auth.key_id = "wgt_901"

    async def mock_streaming_gen():
        yield b"data: [DONE]\n\n"

    with (
        patch(
            "app.api.partner.retrieve_context",
            return_value=(weak if not broad_flag else [], "prompt", [], broad_flag),
        ),
        patch("app.api.partner._widget_page_context_enabled", new=AsyncMock(return_value=False)),
        patch("app.api.partner._widget_support_mode_enabled", new=AsyncMock(return_value=True)),
        patch("app.api.partner.chat_completion_streaming", return_value=mock_streaming_gen()) as chat_stream,
        patch("app.api.partner.asyncio"),
        patch("app.api.partner.write_retrieval_log", new=AsyncMock()),
    ):
        await chat_completions(request=req, http_request=_http_request_stub(), auth=auth, db=db)

    assert chat_stream.call_args.kwargs["broad_mode"] is broad_flag
    # On a broad turn the completion functions must receive no citation chunks
    # at all, so no source frame or "passages gevonden" activity can appear.
    expected_chunks = [] if broad_flag else weak
    assert chat_stream.call_args.kwargs["citation_chunks"] == expected_chunks


def test_request_model_broad_mode_defaults_false():
    from app.api.partner import ChatCompletionsRequest

    req = ChatCompletionsRequest(messages=[{"role": "user", "content": "hi"}])
    assert req.broad_mode is False
