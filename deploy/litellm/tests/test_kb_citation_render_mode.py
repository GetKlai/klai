from __future__ import annotations

from klai_kb_citation_render import (
    compose_non_streaming_kb_response,
    compose_streaming_kb_response,
)


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


def test_visible_footer_follows_english_user_query_not_dutch_sources():
    response = _response("Use the Voys app troubleshooting steps.")

    stats = compose_non_streaming_kb_response(
        response,
        _meta(
            chat_retrieval_prompt_mode="open_kb",
            no_citable_sources=False,
            user_query="The app does not call, just drops the call",
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


def test_visible_footer_keeps_dutch_for_dutch_user_query():
    response = _response("Gebruik de Voys app probleemoplosser.")

    compose_non_streaming_kb_response(
        response,
        _meta(
            chat_retrieval_prompt_mode="open_kb",
            no_citable_sources=False,
            user_query="De app belt niet en verbreekt de oproep",
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
