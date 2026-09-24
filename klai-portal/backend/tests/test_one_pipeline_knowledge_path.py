"""One-chat-pipeline slice 4: the knowledge path does for the internal
profile what the LiteLLM hook does today (modes, scope with identity,
retrieval parameters, sub-question fan-out, query rewrite, trivial gate,
Strict-risk model choice), while a widget single-question turn stays
byte-identical.

Every test drives ``chat_completions`` end to end with one HTTP double that
records each POST by URL, so an assertion reads what actually left portal-api:
the ``/retrieve`` body and the messages sent to LiteLLM.

synthetic-data: generator=hand-written seed=0 (fictional org "Acme Telecom",
example.com URLs, invented KB slugs).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.partner_dependencies import PartnerAuthContext
from app.services.chat_profile import ChatProfile

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "widget_knowledge_golden.json"
ANSWER = "Je reset je wachtwoord via de inlogpagina."


def _evidence_pack(n_items: int = 1, *, score: float = 0.82) -> dict:
    return {
        "items": [
            {
                "chunk_id": f"chunk-{i}",
                "evidence_id": f"ev-{i}",
                "text": "Je reset je wachtwoord via de inlogpagina onder 'Wachtwoord vergeten'.",
                "title": "Wachtwoord resetten",
                "source_url": f"https://help.example.com/wachtwoord-{i}",
                "reranker_score": score,
                "final_score": score,
            }
            for i in range(n_items)
        ],
        "sources": [
            {"url": f"https://help.example.com/wachtwoord-{i}", "title": "Wachtwoord resetten"} for i in range(n_items)
        ],
    }


class _Resp:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _Recorder:
    """httpx.AsyncClient double: records POST bodies by URL suffix.

    The query rewrite also posts to LiteLLM, without a ``stream`` field; it is
    kept apart so ``model_bodies`` holds only the answer call. Its reply is
    ``rewrite_text`` (empty = the rewrite falls back to the raw question).
    """

    def __init__(self, retrieve_payload: dict | Exception, model_text: str = ANSWER) -> None:
        self.retrieve_payload = retrieve_payload
        self.model_text = model_text
        self.rewrite_text = ""
        self.retrieve_bodies: list[dict] = []
        self.model_bodies: list[dict] = []
        self.rewrite_bodies: list[dict] = []

    def factory(self, *_: Any, **__: Any) -> _Recorder:
        return self

    async def __aenter__(self) -> _Recorder:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None

    async def post(self, url: str, *, json: dict, headers: dict | None = None) -> _Resp:
        if url.endswith("/retrieve"):
            self.retrieve_bodies.append(json)
            if isinstance(self.retrieve_payload, Exception):
                raise self.retrieve_payload
            return _Resp(self.retrieve_payload)
        if "stream" not in json:
            self.rewrite_bodies.append(json)
            return _Resp({"choices": [{"message": {"content": self.rewrite_text}}]})
        self.model_bodies.append(json)
        return _Resp(
            {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "model": json["model"],
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": self.model_text}, "finish_reason": "stop"}
                ],
            }
        )


def _auth(*, key_id: str, permissions: dict | None = None, kb_access: dict | None = None) -> PartnerAuthContext:
    return PartnerAuthContext(
        key_id=key_id,
        org_id=7,
        zitadel_org_id="zorg-acme",
        permissions=permissions or {"chat": True},
        kb_access=kb_access if kb_access is not None else {10: "read"},
        rate_limit_rpm=60,
    )


@pytest.fixture
def pipeline(monkeypatch):
    """Patch everything around chat_completions that is not the knowledge path."""
    import app.api.partner as partner
    import app.services.partner_chat as partner_chat

    monkeypatch.setattr(partner.settings, "knowledge_retrieve_url", "http://retrieval.example.com")
    monkeypatch.setattr(partner.settings, "litellm_base_url", "http://litellm.example.com")
    monkeypatch.setattr(partner, "_resolve_kb_slugs", AsyncMock(return_value=["handboek"]))
    monkeypatch.setattr(partner, "_widget_system_prompt", AsyncMock(return_value=None))
    monkeypatch.setattr(partner, "_widget_page_context_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(partner, "_widget_support_mode_enabled", AsyncMock(return_value=False))
    monkeypatch.setattr(partner, "write_retrieval_log", AsyncMock())
    monkeypatch.setattr(partner_chat, "_schedule_gap_event", MagicMock())

    def run(recorder: _Recorder):
        monkeypatch.setattr(partner_chat.httpx, "AsyncClient", recorder.factory)
        return recorder

    return run


async def _chat(
    auth: PartnerAuthContext,
    profile: ChatProfile,
    messages: list[dict],
    *,
    model: str = "klai-primary",
) -> Any:
    from app.api.partner import ChatCompletionsRequest, chat_completions

    db = AsyncMock()
    widget_row = MagicMock()
    widget_row.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=widget_row)
    request = ChatCompletionsRequest(messages=messages, model=model, stream=False)
    return await chat_completions(request=request, http_request=MagicMock(), auth=auth, db=db, profile=profile)


def _system_prompt(recorder: _Recorder) -> str:
    return recorder.model_bodies[-1]["messages"][0]["content"]


# --- widget stays byte-identical ------------------------------------------------


@pytest.mark.asyncio
async def test_widget_single_question_retrieve_body_and_prompt_match_the_golden(pipeline):
    recorder = pipeline(_Recorder({"evidence_pack": _evidence_pack(), "confidence_band": "high"}))

    await _chat(
        _auth(key_id="wgt_golden"),
        ChatProfile(surface="widget"),
        [{"role": "user", "content": "Hoe reset ik mijn wachtwoord?"}],
    )

    from klai_chat_prompts import GROUNDED_CHAT_SYSTEM_PROMPT

    golden = json.loads(GOLDEN_PATH.read_text())
    assert recorder.retrieve_bodies == [golden["retrieve_body"]]
    assert _system_prompt(recorder) == golden["system_prompt"].replace(
        "{GROUNDED_CHAT_SYSTEM_PROMPT}", GROUNDED_CHAT_SYSTEM_PROMPT
    )


# --- internal profile ------------------------------------------------------------

INTERNAL_KEY = {"chat": True, "internal_chat": True}


def _internal(**overrides: Any) -> ChatProfile:
    values: dict[str, Any] = {
        "surface": "internal",
        "kb_mode": "strict",
        "kb_scope": "org",
        "kb_slugs": ("handboek",),
        "user_id": "sub-employee",
    }
    values.update(overrides)
    return ChatProfile(**values)


@pytest.fixture
def internal_pipeline(pipeline, monkeypatch):
    import app.api.partner as partner

    monkeypatch.setattr(partner, "internal_turn_settings", AsyncMock(return_value=([], "shadow")))
    return pipeline


@pytest.mark.asyncio
async def test_strict_employee_with_personal_kb_searches_both_as_themselves_in_the_keys_org(internal_pipeline):
    recorder = internal_pipeline(_Recorder({"evidence_pack": _evidence_pack(), "confidence_band": "high"}))

    await _chat(
        _auth(key_id="key-internal", permissions=INTERNAL_KEY, kb_access={}),
        _internal(kb_scope="both"),
        [{"role": "user", "content": "Hoe vraag ik verlof aan?"}],
    )

    body = recorder.retrieve_bodies[0]
    assert body["scope"] == "both"
    assert body["user_id"] == "sub-employee"
    assert body["kb_slugs"] == ["handboek"]
    assert body["org_id"] == "zorg-acme"
    assert body["top_k"] == 20
    assert body["kb_narrow"] is True


@pytest.mark.asyncio
async def test_org_sent_to_retrieval_is_the_keys_even_when_the_profile_names_another_orgs_user(internal_pipeline):
    recorder = internal_pipeline(_Recorder({"evidence_pack": _evidence_pack(), "confidence_band": "high"}))

    await _chat(
        _auth(key_id="key-internal", permissions=INTERNAL_KEY, kb_access={}),
        _internal(user_id="sub-of-globex", kb_slugs=("globex-handboek",)),
        [{"role": "user", "content": "Hoe vraag ik verlof aan?"}],
    )

    assert [body["org_id"] for body in recorder.retrieve_bodies] == ["zorg-acme"]


_TWO_QUESTIONS = "Wat is de opzegtermijn?\nHoe zeg ik op namens een klant?"
_PASTED_TWO_QUESTIONS = (
    "Kun je dit beantwoorden?\n\n"
    "Van: Jan Jansen <jan@example.com>\n"
    "Verzonden: vrijdag 14 augustus 2026 21:22\n"
    "Aan: support@example.com\n"
    "Onderwerp: Opzeggen\n\n"
    "Wat is de opzegtermijn?\nHoe zeg ik op namens een klant?"
)


@pytest.mark.parametrize(
    ("key_id", "permissions", "profile"),
    [
        ("wgt_golden", {"chat": True}, ChatProfile(surface="widget")),
        ("key-partner", {"chat": True}, ChatProfile(surface="partner")),
        ("key-internal", INTERNAL_KEY, _internal()),
    ],
)
@pytest.mark.asyncio
async def test_multi_question_turn_fans_out_on_every_surface(internal_pipeline, key_id, permissions, profile):
    recorder = internal_pipeline(_Recorder({"evidence_pack": _evidence_pack(), "confidence_band": "high"}))

    await _chat(_auth(key_id=key_id, permissions=permissions), profile, [{"role": "user", "content": _TWO_QUESTIONS}])

    assert recorder.retrieve_bodies[0]["sub_queries"] == [
        "Wat is de opzegtermijn?",
        "Hoe zeg ik op namens een klant?",
    ]


@pytest.mark.asyncio
async def test_split_internal_turn_reports_its_sub_questions_in_the_footer(internal_pipeline):
    pack = _evidence_pack()
    pack["items"][0]["text"] = "De opzegtermijn is een maand. Je zegt op namens een klant via het klantportaal."
    internal_pipeline(
        _Recorder(
            {"evidence_pack": pack, "confidence_band": "high"},
            model_text="De opzegtermijn is een maand. Je zegt op namens een klant via het klantportaal.",
        )
    )

    result = await _chat(
        _auth(key_id="key-internal", permissions=INTERNAL_KEY),
        _internal(),
        [{"role": "user", "content": _TWO_QUESTIONS}],
    )

    assert "- Deelvragen: 2 apart gezocht." in result["choices"][0]["message"]["content"]


@pytest.mark.asyncio
async def test_pasted_correspondence_in_the_latest_turn_is_not_split(internal_pipeline):
    recorder = internal_pipeline(_Recorder({"evidence_pack": _evidence_pack(), "confidence_band": "high"}))

    await _chat(
        _auth(key_id="key-internal", permissions=INTERNAL_KEY),
        _internal(),
        [{"role": "user", "content": _PASTED_TWO_QUESTIONS}],
    )

    assert "sub_queries" not in recorder.retrieve_bodies[0]


@pytest.mark.asyncio
async def test_strict_with_nothing_to_search_refuses_without_retrieval_or_model(internal_pipeline):
    from klai_chat_prompts import no_citable_sources_message

    recorder = internal_pipeline(_Recorder({"evidence_pack": _evidence_pack()}))

    result = await _chat(
        _auth(key_id="key-internal", permissions=INTERNAL_KEY),
        _internal(kb_slugs=()),
        [{"role": "user", "content": "Hoe vraag ik verlof aan?"}],
    )

    assert result["choices"][0]["message"]["content"] == no_citable_sources_message("nl", suggest_open_mode=True)
    assert recorder.retrieve_bodies == []
    assert recorder.model_bodies == []


@pytest.mark.asyncio
async def test_general_mode_searches_nothing_and_uses_the_general_prompt(internal_pipeline):
    from klai_chat_prompts import GENERAL_CHAT_SYSTEM_PROMPT

    recorder = internal_pipeline(_Recorder({"evidence_pack": _evidence_pack()}))

    await _chat(
        _auth(key_id="key-internal", permissions=INTERNAL_KEY),
        _internal(kb_mode="general", kb_slugs=None),
        [{"role": "user", "content": "Schrijf een korte uitnodiging voor de borrel."}],
    )

    assert recorder.retrieve_bodies == []
    assert _system_prompt(recorder).startswith(GENERAL_CHAT_SYSTEM_PROMPT)


@pytest.mark.asyncio
async def test_meta_question_gets_the_meta_prompt(internal_pipeline):
    from klai_chat_prompts import META_CHAT_SYSTEM_PROMPT

    recorder = internal_pipeline(_Recorder({"evidence_pack": _evidence_pack()}))

    await _chat(
        _auth(key_id="key-internal", permissions=INTERNAL_KEY),
        _internal(),
        [{"role": "user", "content": "Wat kan ik hier?"}],
    )

    assert recorder.retrieve_bodies == []
    assert _system_prompt(recorder).startswith(META_CHAT_SYSTEM_PROMPT)


@pytest.mark.asyncio
async def test_strict_turn_on_weak_evidence_is_answered_by_klai_medium(internal_pipeline):
    # Band "low" and no salient word of the question in the chunk: the
    # conditions of the 2026-08-17 fabricated-webhook-timeout incident.
    recorder = internal_pipeline(_Recorder({"evidence_pack": _evidence_pack(score=0.4), "confidence_band": "low"}))

    await _chat(
        _auth(key_id="key-internal", permissions=INTERNAL_KEY),
        _internal(),
        [{"role": "user", "content": "Welke timeout hanteert de webhookkoppeling?"}],
    )

    assert recorder.model_bodies[-1]["model"] == "klai-medium"


@pytest.mark.asyncio
async def test_strict_retrieval_failure_refuses_without_a_model_call(internal_pipeline):
    import httpx

    recorder = internal_pipeline(_Recorder(httpx.ConnectTimeout("retrieval down")))

    result = await _chat(
        _auth(key_id="key-internal", permissions=INTERNAL_KEY),
        _internal(),
        [{"role": "user", "content": "Hoe vraag ik verlof aan?"}],
    )

    assert "tijdelijk niet bereikbaar" in result["choices"][0]["message"]["content"]
    assert recorder.model_bodies == []


@pytest.mark.asyncio
async def test_open_retrieval_failure_answers_with_the_unavailable_notice(internal_pipeline):
    import httpx

    recorder = internal_pipeline(_Recorder(httpx.ConnectTimeout("retrieval down")))

    await _chat(
        _auth(key_id="key-internal", permissions=INTERNAL_KEY),
        _internal(kb_mode="open"),
        [{"role": "user", "content": "Hoe vraag ik verlof aan?"}],
    )

    assert "TEMPORARILY UNAVAILABLE" in _system_prompt(recorder)


@pytest.mark.asyncio
async def test_internal_prompt_is_foundation_then_templates_then_librechats_own_system(internal_pipeline, monkeypatch):
    from klai_chat_prompts import GROUNDED_CHAT_SYSTEM_PROMPT

    import app.api.partner as partner

    monkeypatch.setattr(
        partner,
        "internal_turn_settings",
        AsyncMock(return_value=([{"source": "template", "name": "Formeel", "text": "Schrijf formeel."}], "full")),
    )
    recorder = internal_pipeline(_Recorder({"evidence_pack": _evidence_pack(), "confidence_band": "high"}))

    await _chat(
        _auth(key_id="key-internal", permissions=INTERNAL_KEY),
        _internal(),
        [
            {"role": "system", "content": "LIBRECHAT AGENT INSTRUCTIONS"},
            {"role": "user", "content": "Hoe vraag ik verlof aan?"},
        ],
    )

    prompt = _system_prompt(recorder)
    assert prompt.startswith(GROUNDED_CHAT_SYSTEM_PROMPT)
    assert prompt.index("[Formeel]\nSchrijf formeel.") < prompt.index("Evidence E1")
    assert prompt.endswith("\n\nLIBRECHAT AGENT INSTRUCTIONS")
    assert recorder.retrieve_bodies[0]["telemetry_level"] == "full"


@pytest.mark.asyncio
async def test_internal_query_is_rewritten_and_the_raw_question_travels_beside_it(internal_pipeline):
    recorder = _Recorder({"evidence_pack": _evidence_pack(), "confidence_band": "high"})
    recorder.rewrite_text = "Hoe vraag ik verlof aan in het verlofsysteem?"
    internal_pipeline(recorder)

    await _chat(
        _auth(key_id="key-internal", permissions=INTERNAL_KEY),
        _internal(),
        [
            {"role": "user", "content": "Hoe werkt het verlofsysteem?"},
            {"role": "assistant", "content": "Je vraagt verlof aan in het verlofsysteem."},
            {"role": "user", "content": "Hoe vraag ik verlof aan?"},
        ],
    )

    body = recorder.retrieve_bodies[0]
    assert body["query"] == "Hoe vraag ik verlof aan in het verlofsysteem?"
    assert body["raw_query"] == "Hoe vraag ik verlof aan?"
    assert body["coreference_resolved"] is True


# --- one trivial gate --------------------------------------------------------------


@pytest.mark.asyncio
async def test_thank_you_searches_nothing(pipeline):
    recorder = pipeline(_Recorder({"evidence_pack": _evidence_pack()}))

    await _chat(_auth(key_id="wgt_golden"), ChatProfile(surface="widget"), [{"role": "user", "content": "bedankt!"}])

    assert recorder.retrieve_bodies == []


@pytest.mark.asyncio
async def test_yes_to_the_assistants_question_still_searches(pipeline):
    recorder = pipeline(_Recorder({"evidence_pack": _evidence_pack(), "confidence_band": "high"}))

    await _chat(
        _auth(key_id="wgt_golden"),
        ChatProfile(surface="widget"),
        [
            {"role": "user", "content": "Mijn toestel belt niet uit."},
            {"role": "assistant", "content": "Gaat het om een vaste lijn?"},
            {"role": "user", "content": "ja"},
        ],
    )

    assert len(recorder.retrieve_bodies) == 1
