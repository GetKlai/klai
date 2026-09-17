from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
from pathlib import Path

from klai_kb_citation_render import (
    compose_non_streaming_kb_response as _compose_non_streaming_kb_response,
    compose_streaming_kb_response as _compose_streaming_kb_response,
    log_kb_citation_render,
)


# The composers are async since SPEC-RAG-CLARIFY-FLOW-001 REQ-4 (a Strict
# refusal may await the answer-claims classification). These contract tests
# exercise the render itself, so they run each call to completion.
def compose_non_streaming_kb_response(*args, **kwargs):
    return asyncio.run(_compose_non_streaming_kb_response(*args, **kwargs))


def compose_streaming_kb_response(*args, **kwargs):
    return asyncio.run(_compose_streaming_kb_response(*args, **kwargs))


def _response(content: str) -> dict:
    return {"choices": [{"message": {"content": content}}]}


def _meta(**overrides) -> dict:
    return {
        "chat_retrieval_prompt_mode": "strict_kb",
        "kb_narrow": False,
        "allowed_image_urls": [],
        "citation_chunks": [],
        "trusted_sources": [],
        "no_citable_sources": True,
        "user_query": "Wat is het beleid?",
        # The hook now always carries the conversation decision; the footer
        # reads THIS, never user_query.
        "response_language_target": "nl",
        **overrides,
    }


def test_prompt_mode_overrides_legacy_kb_narrow_for_strict_no_sources():
    response = _response("Model answer without sources.")

    stats = compose_non_streaming_kb_response(response, _meta())

    content = response["choices"][0]["message"]["content"]
    assert stats.no_citable_sources is True
    assert content != "Model answer without sources."


def test_prompt_mode_overrides_legacy_kb_narrow_for_open_no_sources():
    response = _response("Model answer without sources.")

    stats = compose_non_streaming_kb_response(
        response,
        _meta(chat_retrieval_prompt_mode="open_kb", kb_narrow=True),
    )

    content = response["choices"][0]["message"]["content"]
    assert stats.no_citable_sources is False
    assert content == "Model answer without sources."


def test_visible_footer_follows_the_english_decision_not_dutch_sources():
    response = _response("Use the Voys app troubleshooting steps.")

    stats = compose_non_streaming_kb_response(
        response,
        _meta(
            chat_retrieval_prompt_mode="open_kb",
            no_citable_sources=False,
            user_query="The app does not call, just drops the call",
            response_language_target="en",
            citation_chunks=[
                {
                    "text": "iPhone Voys App Probleemoplosser > Ik ontvang geen inkomende oproepen",
                    "source_url": "https://help.voys.nl/iphone-voys-app-probleemoplosser",
                    "metadata": {"title": "iPhone Voys App Probleemoplosser"},
                    "chunk_id": "c1",
                    "reranker_score": 0.13,
                }
            ],
            trusted_sources=[
                {
                    "title": "iPhone Voys App Probleemoplosser",
                    "url": "https://help.voys.nl/iphone-voys-app-probleemoplosser",
                }
            ],
            chunks_injected=1,
            retrieval_ms=12,
            citable_sources_count=1,
            confidence_band="low",
            kb_scope_mode="all_org_and_personal",
            kbs_with_results=["support"],
            kbs_used_as_sources=["support"],
        ),
    )

    content = response["choices"][0]["message"]["content"]
    assert stats.rendered_messages == 1
    assert "**Sources**" in content
    assert "**Agent activity**" in content
    assert "Knowledge base queried" in content
    assert "**Bronnen**" not in content
    assert "Kennisbank geraadpleegd" not in content


def test_visible_footer_follows_the_dutch_decision_not_the_user_query():
    response = _response("Gebruik de Voys app probleemoplosser.")

    compose_non_streaming_kb_response(
        response,
        _meta(
            chat_retrieval_prompt_mode="open_kb",
            no_citable_sources=False,
            # English text on purpose: the footer must follow the decision
            # even when the (earlier-turn) user_query would have said
            # otherwise under the deleted wordlist.
            user_query="The app does not call, just drops the call",
            response_language_target="nl",
            citation_chunks=[
                {
                    "text": "iPhone Voys App Probleemoplosser > Ik ontvang geen inkomende oproepen",
                    "source_url": "https://help.voys.nl/iphone-voys-app-probleemoplosser",
                    "metadata": {"title": "iPhone Voys App Probleemoplosser"},
                    "chunk_id": "c1",
                    "reranker_score": 0.13,
                }
            ],
            trusted_sources=[
                {
                    "title": "iPhone Voys App Probleemoplosser",
                    "url": "https://help.voys.nl/iphone-voys-app-probleemoplosser",
                }
            ],
            chunks_injected=1,
            retrieval_ms=12,
            citable_sources_count=1,
            confidence_band="low",
        ),
    )

    content = response["choices"][0]["message"]["content"]
    assert "**Bronnen**" in content
    assert "**Agent activiteit**" in content
    assert "Kennisbank geraadpleegd" in content


def _fanout_meta(**overrides) -> dict:
    """kb_meta for a normal (non-strict) fan-out answer with a real chunk,
    so both the non-streaming and streaming composers take the "answer with
    sources" path rather than the strict-refusal path."""
    return _meta(
        chat_retrieval_prompt_mode="open_kb",
        no_citable_sources=False,
        citation_chunks=[
            {
                "text": "Meldingen worden bij een storing niet opnieuw aangeboden.",
                "source_url": "https://docs.klai.example/meldingen",
                "metadata": {"title": "Gespreksmeldingen"},
                "chunk_id": "c1",
                "reranker_score": 0.62,
            }
        ],
        trusted_sources=[
            {
                "title": "Gespreksmeldingen",
                "url": "https://docs.klai.example/meldingen",
            }
        ],
        chunks_injected=1,
        retrieval_ms=12,
        citable_sources_count=1,
        confidence_band="medium",
        **overrides,
    )


def test_non_streaming_response_includes_unchecked_questions_footer():
    """Fix I (non-streaming path): the deterministic footer must list
    unchecked sub-questions regardless of what the model itself wrote."""
    response = _response("De meldingen worden niet opnieuw aangeboden.")

    compose_non_streaming_kb_response(
        response,
        _fanout_meta(unchecked_questions=["Vraag zeven?", "Vraag acht?"]),
    )

    content = response["choices"][0]["message"]["content"]
    assert "- Niet apart doorzocht (limiet bereikt): Vraag zeven?; Vraag acht?." in content


def test_streaming_response_includes_unchecked_questions_footer():
    """Fix I (streaming path): ``_append_visible_sources_section`` is the
    single call-point shared by both ``compose_non_streaming_kb_response``
    and ``compose_streaming_kb_response`` — this proves the deterministic
    unchecked-questions footer also reaches the streamed final chunk, not
    just the non-streaming response."""
    kb_meta = _fanout_meta(unchecked_questions=["Vraag zeven?", "Vraag acht?"])

    first = {
        "choices": [
            {"delta": {"content": "De meldingen worden "}, "finish_reason": None}
        ]
    }
    final = {
        "choices": [
            {"delta": {"content": "niet opnieuw aangeboden."}, "finish_reason": "stop"}
        ]
    }

    compose_streaming_kb_response(first, kb_meta)
    compose_streaming_kb_response(final, kb_meta, flush_stream=True)

    content = final["choices"][0]["delta"]["content"]
    assert "- Niet apart doorzocht (limiet bereikt): Vraag zeven?; Vraag acht?." in content


def test_non_streaming_render_log_contains_language_fields(caplog):
    response = _response(
        "The notifications are not offered again, and you can review the setting. (E1)"
    )
    kb_meta = _fanout_meta(
        response_language_target="en",
        # Part B fields as the hook flattens them out of LanguageDecision.
        response_language_reason="locked",
        response_language_method="function_words",
        response_language_votes=4,
        response_language_abstentions=1,
        response_language_switches=0,
        kb_scope_mode="all_org_and_personal",
        kbs_in_scope=["support"],
    )

    stats = compose_non_streaming_kb_response(response, kb_meta)

    logger = logging.getLogger("test_kb_citation_render_language_non_stream")
    with caplog.at_level(logging.WARNING, logger=logger.name):
        log_kb_citation_render(logger, kb_meta, stats, stream=False)

    messages = [
        record.message
        for record in caplog.records
        if "kb_citations_rendered_structured" in record.message
    ]
    assert len(messages) == 1
    assert "response_language_target=en" in messages[0]
    assert "language_reason=locked" in messages[0]
    # The mechanism sits next to the reason: an operator reading one line
    # sees both WHAT decided and HOW the deciding turn was identified.
    assert "language_reason=locked language_method=function_words" in messages[0]
    assert "language_votes=4" in messages[0]
    assert "language_abstentions=1" in messages[0]
    assert "language_switches=0" in messages[0]
    assert "answer_language=en" in messages[0]
    assert "language_correct=True" in messages[0]


def test_language_correct_is_no_decision_without_a_conversation_decision():
    # The incident's self-consistency trap: comparing our own guess against
    # our own guess. With NO decision there is nothing to be correct
    # against — that must be the distinct "no_decision", never True/False.
    response = _response(
        "The notifications are not offered again, and you can review the setting. (E1)"
    )
    kb_meta = _fanout_meta(response_language_target=None)

    compose_non_streaming_kb_response(response, kb_meta)

    assert kb_meta["answer_language"] == "en"
    assert kb_meta["language_correct"] == "no_decision"


def test_streaming_render_log_contains_language_fields(caplog):
    kb_meta = _fanout_meta(
        response_language_target="en",
        kb_scope_mode="all_org_and_personal",
        kbs_in_scope=["support"],
    )
    first = {
        "choices": [
            {"delta": {"content": "The notifications are not "}, "finish_reason": None}
        ]
    }
    final = {
        "choices": [
            {
                "delta": {
                    "content": "offered again, and you can review the setting. (E1)"
                },
                "finish_reason": "stop",
            }
        ]
    }

    compose_streaming_kb_response(first, kb_meta)
    stats = compose_streaming_kb_response(final, kb_meta, flush_stream=True)

    logger = logging.getLogger("test_kb_citation_render_language_stream")
    with caplog.at_level(logging.WARNING, logger=logger.name):
        log_kb_citation_render(logger, kb_meta, stats, stream=True)

    messages = [
        record.message
        for record in caplog.records
        if "kb_citations_rendered_structured" in record.message
    ]
    assert len(messages) == 1
    assert "response_language_target=en" in messages[0]
    assert "answer_language=en" in messages[0]
    assert "language_correct=True" in messages[0]


# Runs against the REAL litellm stream objects in a subprocess: sibling test
# modules install a fake ``litellm`` into sys.modules, so an in-process import
# would be order-dependent (same reason as test_litellm_customlogger_contract).
_HELD_OBJECT_PROBE = """
import asyncio, json, sys
sys.path.insert(0, %r)
from litellm.types.utils import Delta, ModelResponseStream, StreamingChoices
from klai_kb_citation_render import compose_streaming_kb_response

meta = {
    'chat_retrieval_prompt_mode': 'strict_kb',
    'kb_narrow': True,
    'chunks_injected': 1,
    'user_query': 'Wat kost de huur?',
    'response_language_target': 'nl',
    'citation_chunks': [{'evidence_id': 'E1', 'title': 'Tags',
                         'source_url': 'https://docs.klai.example/tags',
                         'text': 'Tabel met supporttags.'}],
    'trusted_sources': [{'label': '1', 'title': 'Tags',
                         'url': 'https://docs.klai.example/tags',
                         'evidence_ids': ['E1']}],
}
tool_call = {'index': 0, 'id': 'call_1', 'type': 'function',
             'function': {'name': 'web_search', 'arguments': '{"q": "huur"}'}}
tool_delta = Delta(role='assistant', tool_calls=[tool_call])
text_delta = Delta(content='De kantoorhuur ', reasoning_content='SECRET-REASONING',
                   provider_specific_fields={'citations': ['SECRET-PSF']})
text_delta.sources = [{'title': 'SECRET-SOURCE'}]
chunks = [ModelResponseStream(choices=[StreamingChoices(index=0, delta=d)])
          for d in (tool_delta, text_delta)]
before = chunks[0].choices[0].delta.tool_calls[0].model_dump()
for chunk in chunks:
    asyncio.run(compose_streaming_kb_response(chunk, meta))
print(json.dumps({
    'held': [chunk.model_dump_json() for chunk in chunks],
    'tool_call_before': before,
    'tool_call_after': chunks[0].choices[0].delta.tool_calls[0].model_dump(),
    'delta_type': type(chunks[1].choices[0].delta).__name__,
}))
""" % str(Path(__file__).resolve().parents[1])


def test_held_strict_litellm_stream_objects_carry_no_text_outside_tool_calls():
    result = subprocess.run(
        [sys.executable, "-c", _HELD_OBJECT_PROBE], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr[-2000:]
    out = json.loads(result.stdout)

    assert out["delta_type"] == "Delta"
    assert out["tool_call_after"] == out["tool_call_before"]
    for serialized in out["held"]:
        assert "SECRET" not in serialized
        assert "kantoorhuur" not in serialized
