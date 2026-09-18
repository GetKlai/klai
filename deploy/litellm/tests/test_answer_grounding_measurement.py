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
