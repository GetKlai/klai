"""The internal chat measures how much of an answer the articles support.

SPEC-RAG-ANSWER-JUDGES-001. The widget path decides with this check; here it
only measures, so the two paths can be compared on the same words before the
repair is worth porting. What must hold: the check sees the answer the user
gets, it never delays that answer, and a failing check costs nothing.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from tests.klai_module_reset import reset_klai_kb_modules


@pytest.fixture(autouse=True)
def _fresh_modules():
    reset_klai_kb_modules()
    yield
    reset_klai_kb_modules()


def _kb_meta() -> dict:
    # The key production writes is chat_retrieval_prompt_mode (klai_kb_answer_policy);
    # an earlier version of this gate read a key that only tests set, so it would
    # never have measured a real internal chat.
    return {
        "chat_retrieval_prompt_mode": "strict_kb",
        "user_query": "Hoe stel ik een wachtrij in?",
        "org_id": "8",
        "user_id": "u1",
        "request_id": "r1",
    }


@pytest.mark.asyncio
async def test_the_check_reads_the_answer_the_user_gets_and_the_articles_behind_it(monkeypatch):
    import klai_answer_grounding
    import klai_kb_citation_render as render

    seen: dict = {}

    async def _fake(*, user_query, draft, citation_chunks, kb_meta):
        seen.update(query=user_query, draft=draft, chunks=citation_chunks)

    monkeypatch.setattr(render, "log_answer_grounding", _fake)
    chunks = [{"title": "Wachtrij", "text": "Ga naar Belplan en voeg de wachtrijmodule toe."}]

    render._measure_answer_grounding("Ga naar Belplan.", chunks, _kb_meta())
    await asyncio.sleep(0)

    assert seen["draft"] == "Ga naar Belplan."
    assert seen["query"] == "Hoe stel ik een wachtrij in?"
    assert seen["chunks"] == chunks


@pytest.mark.asyncio
async def test_a_crashing_check_stays_inside_its_own_task(monkeypatch, caplog):
    """The answer is already rendered when this runs; a crash may not reach the
    event loop as an unhandled exception."""
    import klai_kb_citation_render as render

    async def _boom(**_kwargs):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(render, "log_answer_grounding", _boom)
    loop_errors: list[dict] = []
    asyncio.get_running_loop().set_exception_handler(lambda _loop, context: loop_errors.append(context))

    with caplog.at_level(logging.WARNING):
        render._measure_answer_grounding("Ga naar Belplan.", [], _kb_meta())
        await asyncio.sleep(0.01)

    assert any("kb_answer_grounding_task_failed" in r.getMessage() for r in caplog.records)
    assert loop_errors == []


@pytest.mark.asyncio
async def test_the_answer_does_not_wait_for_the_check(monkeypatch):
    """The check may not add a second to the user's turn."""
    import klai_kb_citation_render as render

    started = asyncio.Event()
    release = asyncio.Event()

    async def _slow(**_kwargs):
        started.set()
        await release.wait()

    monkeypatch.setattr(render, "log_answer_grounding", _slow)

    render._measure_answer_grounding("Ga naar Belplan.", [], _kb_meta())
    # Control is back here immediately; the check has not even started yet.
    assert not started.is_set()
    await asyncio.sleep(0)
    assert started.is_set()
    release.set()


@pytest.mark.asyncio
async def test_only_the_strict_mode_is_measured(monkeypatch):
    import klai_kb_citation_render as render

    called = False

    async def _fake(**_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(render, "log_answer_grounding", _fake)

    render._measure_answer_grounding(
        "Een antwoord.", [], {**_kb_meta(), "chat_retrieval_prompt_mode": "open_kb", "kb_narrow": False}
    )
    await asyncio.sleep(0)

    assert called is False


@pytest.mark.asyncio
async def test_the_measurement_logs_what_the_articles_do_not_support(monkeypatch, caplog):
    import klai_answer_grounding as grounding

    class _Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            content = (
                '{"statements": ['
                '{"statement": "Ga naar Belplan.", "evidence": "Ga naar Belplan", "support": "supported"},'
                '{"statement": "Bel 020-1234567.", "evidence": "", "support": "not_in_articles"},'
                '{"statement": "Dat kost 5 euro.", "evidence": "", "support": "contradicted"}'
                "]}"
            )
            return {"choices": [{"message": {"content": content}}]}

    async def _post(payload, headers, client_kwargs, timeout):
        return _Response()

    monkeypatch.setattr(grounding, "ANSWER_GROUNDING_API_KEY", "key")
    monkeypatch.setattr(grounding, "_post_to_rewrite_model", _post)

    with caplog.at_level(logging.WARNING):
        await grounding.log_answer_grounding(
            user_query="Hoe stel ik een wachtrij in?",
            draft="Ga naar Belplan. Bel 020-1234567. Dat kost 5 euro.",
            citation_chunks=[{"title": "Wachtrij", "text": "Ga naar Belplan"}],
            kb_meta=_kb_meta(),
        )

    (record,) = [r for r in caplog.records if "kb_answer_grounding " in r.getMessage()]
    assert "statements=3" in record.getMessage()
    assert "unsupported=2" in record.getMessage()
    assert "worth_repairing=True" in record.getMessage()


@pytest.mark.asyncio
async def test_both_paths_hand_the_checker_the_same_text(monkeypatch):
    """The internal payload must be what the shared builder produces.

    The two paths used to write these three labels separately, which is how a
    checker starts answering differently on one path than the other while both
    look correct in review. Their numbers are compared against each other
    (86% internal against 64% on the widget, 2026-09-18), so the input has to
    be identical by construction, not by inspection.
    """
    import klai_answer_grounding as grounding
    from klai_chat_prompts import grounding_check_user_content

    sent: dict = {}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": '{"statements": []}'}}]}

    async def _capture(payload, headers, _opts, _timeout):
        sent.update(payload)
        return _Resp()

    monkeypatch.setattr(grounding, "_post_to_rewrite_model", _capture)
    monkeypatch.setattr(grounding, "ANSWER_GROUNDING_API_KEY", "k")

    chunks = [{"title": "Wachtrij", "text": "Ga naar Belplan."}, {"title": "Leeg", "text": "  "}]
    await grounding.log_answer_grounding(
        user_query="Hoe stel ik een wachtrij in?",
        draft="Ga naar Belplan.",
        citation_chunks=chunks,
        kb_meta={"org_id": "8"},
    )

    assert sent["messages"][1]["content"] == grounding_check_user_content(
        question="Hoe stel ik een wachtrij in?",
        articles=[("Wachtrij", "Ga naar Belplan.")],
        draft="Ga naar Belplan.",
    )


@pytest.mark.asyncio
async def test_the_check_hands_back_its_verdict_and_counts_contradictions(monkeypatch, caplog):
    """Two contracts the caller and the operator report depend on.

    The verdict is returned so a caller can act on it without asking again, and
    the contradiction count is logged apart from the rest because on real
    internal answers those are 8% where any unsupported statement is 84%
    (fifty answers from a customer's tenant, 2026-09-18). Warning on one in
    twelve answers and warning on seven in ten are different products, so the
    split has to survive a refactor.
    """
    import klai_answer_grounding as grounding

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"statements": ['
                                '{"statement": "Ga naar Belplan.", "evidence": "Ga naar Belplan.",'
                                ' "support": "supported"},'
                                '{"statement": "Bel 020-1234567.", "evidence": "",'
                                ' "support": "not_in_articles"},'
                                '{"statement": "Dat kost 5 euro.", "evidence": "",'
                                ' "support": "contradicted"}'
                                "]}"
                            )
                        }
                    }
                ]
            }

    async def _fake(_payload, _headers, _opts, _timeout):
        return _Resp()

    monkeypatch.setattr(grounding, "_post_to_rewrite_model", _fake)
    monkeypatch.setattr(grounding, "ANSWER_GROUNDING_API_KEY", "k")

    with caplog.at_level(logging.WARNING):
        check = await grounding.log_answer_grounding(
            user_query="Hoe stel ik een wachtrij in?",
            draft="Ga naar Belplan.",
            citation_chunks=[{"title": "Wachtrij", "text": "Ga naar Belplan."}],
            kb_meta={"org_id": "8"},
        )

    assert check is not None
    assert [item.statement for item in check.unsupported] == [
        "Bel 020-1234567.",
        "Dat kost 5 euro.",
    ]
    assert check.worth_repairing is True
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "contradicted=1" in logged
    assert "unsupported=2" in logged


@pytest.mark.asyncio
async def test_a_failed_check_hands_back_nothing(monkeypatch):
    """A caller must be able to tell "not checked" from "checked and clean"."""
    import klai_answer_grounding as grounding

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(grounding, "_post_to_rewrite_model", _boom)
    monkeypatch.setattr(grounding, "ANSWER_GROUNDING_API_KEY", "k")

    assert await grounding.log_answer_grounding(
        user_query="Q", draft="D", citation_chunks=[], kb_meta={"org_id": "8"}
    ) is None


@pytest.mark.asyncio
async def test_a_non_streaming_answer_is_repaired(monkeypatch):
    """The internal path acts where it can, not only where it is cheap.

    Fifty real answers from a customer's own tenant on 2026-09-18: 86% state
    something the articles do not carry against 64% on the widget, and 70%
    reach the repair threshold against 40%. Measuring that and doing nothing
    with it was the gap.
    """
    import klai_kb_citation_render as render

    async def _repair(*, user_query, draft, citation_chunks, kb_meta):
        assert draft == "Ga naar Belplan en bel 020-1234567."
        return "Ga naar Belplan."

    monkeypatch.setattr(render, "repair_answer", _repair)

    monkeypatch.setattr(render, "_render_kb_citation_content", lambda text, **_kw: (text, [], False, {}))

    result = await render._repair_or_measure(
        "Ga naar Belplan en bel 020-1234567.",
        [{"title": "Wachtrij", "text": "Ga naar Belplan."}],
        _kb_meta(),
        stream=False,
        allowed_image_urls=set(),
        trusted_sources=[],
        no_citable_sources=False,
    )

    assert result == "Ga naar Belplan."


@pytest.mark.asyncio
async def test_a_streamed_answer_is_only_measured(monkeypatch):
    """Text the user has already read cannot be taken back.

    Every internal turn streamed in the week to 2026-09-18 (41 of 41), so this
    is the live path; the repair reaches it only by turning streaming off with
    KLAI_KB_CHAT_RENDER_MODE=deterministic_non_streaming.
    """
    import klai_kb_citation_render as render

    async def _never(**_kwargs):
        raise AssertionError("a streamed answer may not be rewritten")

    measured: list[str] = []
    monkeypatch.setattr(render, "repair_answer", _never)
    monkeypatch.setattr(render, "_measure_answer_grounding", lambda text, _c, _m: measured.append(text))

    result = await render._repair_or_measure(
        "Ga naar Belplan.",
        [],
        _kb_meta(),
        stream=True,
        allowed_image_urls=set(),
        trusted_sources=[],
        no_citable_sources=False,
    )

    assert result == "Ga naar Belplan."
    assert measured == ["Ga naar Belplan."]


@pytest.mark.asyncio
async def test_a_crashing_repair_leaves_the_answer_alone(monkeypatch):
    """The user's answer may never be the casualty of this check."""
    import klai_kb_citation_render as render

    async def _boom(**_kwargs):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(render, "repair_answer", _boom)

    result = await render._repair_or_measure(
        "Ga naar Belplan.",
        [],
        _kb_meta(),
        stream=False,
        allowed_image_urls=set(),
        trusted_sources=[],
        no_citable_sources=False,
    )

    assert result == "Ga naar Belplan."


@pytest.mark.asyncio
async def test_the_repaired_text_goes_back_through_the_link_guard(monkeypatch):
    """Free model text again, so it can put back what the renderer removed.

    The widget path re-applies its stripper to repaired text for this reason,
    and there it was reproduced: a reply carrying a link the retrieval never
    supplied reached the visitor untouched.
    """
    import klai_kb_citation_render as render

    async def _repair(**_kwargs):
        return "Ga naar Belplan. Zie https://evil.example.com/phish"

    seen: dict = {}

    def _guard(text, **_kw):
        seen["text"] = text
        return ("Ga naar Belplan.", [], False, {})

    monkeypatch.setattr(render, "repair_answer", _repair)
    monkeypatch.setattr(render, "_render_kb_citation_content", _guard)

    result = await render._repair_or_measure(
        "Ga naar Belplan en bel 020-1234567.",
        [{"title": "Wachtrij", "text": "Ga naar Belplan."}],
        _kb_meta(),
        stream=False,
        allowed_image_urls=set(),
        trusted_sources=[],
        no_citable_sources=False,
    )

    assert "evil.example.com" in seen["text"], "the guard has to see what the repair produced"
    assert result == "Ga naar Belplan."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kb_extra",
    [
        {"allow_uncited_user_content": True},
        {"suppress_kb_citations": True},
        {"user_provided_content_context": True},
        {"pasted_correspondence_detected": True},
    ],
)
async def test_an_answer_resting_on_what_the_user_pasted_is_not_repaired(monkeypatch, kb_extra):
    """The renderer lets these through without KB citations on purpose.

    Judging them against the articles alone reads two correct observations from
    a screenshot as two unsupported statements, which is exactly the threshold
    that deletes them.
    """
    import klai_kb_citation_render as render

    async def _never(**_kwargs):
        raise AssertionError("an answer about the user's own material may not be repaired")

    monkeypatch.setattr(render, "repair_answer", _never)
    monkeypatch.setattr(render, "_measure_answer_grounding", lambda *_a: None)

    result = await render._repair_or_measure(
        "Op je screenshot staat de extensie op 201.",
        [],
        {**_kb_meta(), **kb_extra},
        stream=False,
        allowed_image_urls=set(),
        trusted_sources=[],
        no_citable_sources=False,
    )

    assert result == "Op je screenshot staat de extensie op 201."


@pytest.mark.asyncio
async def test_a_fixed_refusal_is_not_sent_through_the_checker(monkeypatch):
    """It states nothing to repair, and the call would add up to twelve seconds."""
    import klai_kb_citation_render as render

    async def _never(**_kwargs):
        raise AssertionError("a fixed refusal may not cost a model call")

    monkeypatch.setattr(render, "repair_answer", _never)
    monkeypatch.setattr(render, "_measure_answer_grounding", lambda *_a: None)

    result = await render._repair_or_measure(
        "Dit staat niet in onze helpartikelen.",
        [],
        _kb_meta(),
        stream=False,
        allowed_image_urls=set(),
        trusted_sources=[],
        no_citable_sources=True,
    )

    assert result == "Dit staat niet in onze helpartikelen."


@pytest.mark.asyncio
async def test_a_held_strict_stream_is_repaired_before_the_reader_sees_it(monkeypatch):
    """The live path, and the only one that matters.

    Every internal turn streams (41 of 41 in the week to 2026-09-18), so a
    repair that skips streams reaches nothing. A Strict stream is held back in
    full: the reader gets empty deltas until the flush, where the whole answer
    is still in hand. This asserts the delta the reader finally receives carries
    the repaired text, not the draft.
    """
    import klai_kb_citation_render as render

    async def _repair(*, user_query, draft, citation_chunks, kb_meta):
        assert "020-1234567" in draft, "the repair has to see the whole held answer"
        return "Ga naar Belplan."

    monkeypatch.setattr(render, "repair_answer", _repair)
    monkeypatch.setattr(render, "_render_kb_citation_content_guard_passthrough", None, raising=False)

    kb_meta = {
        "chat_retrieval_prompt_mode": "strict_kb",
        "kb_narrow": True,
        "allowed_image_urls": [],
        "citation_chunks": [
            {
                "chunk_id": "c1",
                "title": "Wachtrij",
                "text": "Ga naar Belplan en voeg de wachtrijmodule toe.",
                "source_url": "https://help.example.com/wachtrij",
            }
        ],
        "trusted_sources": [
            {"title": "Wachtrij", "url": "https://help.example.com/wachtrij", "chunk_id": "c1"}
        ],
        "user_query": "Hoe stel ik een wachtrij in?",
        "response_language_target": "nl",
    }

    first = {"choices": [{"delta": {"content": "Ga naar Belplan "}, "finish_reason": None}]}
    final = {"choices": [{"delta": {"content": "en bel 020-1234567.", }, "finish_reason": "stop"}]}

    await render.compose_streaming_kb_response(first, kb_meta)
    # Nothing readable has gone out yet: the held delta carries no content.
    assert not (first["choices"][0]["delta"].get("content") or "")

    await render.compose_streaming_kb_response(final, kb_meta, flush_stream=True)

    delivered = final["choices"][0]["delta"].get("content") or ""
    assert "020-1234567" not in delivered, "the reader may not receive the unsupported number"
    assert "Ga naar Belplan" in delivered


@pytest.mark.asyncio
async def test_an_open_stream_is_only_measured(monkeypatch):
    """An Open stream really does send the model's words as they come."""
    import klai_kb_citation_render as render

    async def _never(**_kwargs):
        raise AssertionError("text already sent may not be rewritten")

    monkeypatch.setattr(render, "repair_answer", _never)
    monkeypatch.setattr(render, "_measure_answer_grounding", lambda *_a: None)

    result = await render._repair_or_measure(
        "Ga naar Belplan.",
        [],
        _kb_meta(),
        stream=True,
        allowed_image_urls=set(),
        trusted_sources=[],
        no_citable_sources=False,
    )

    assert result == "Ga naar Belplan."
