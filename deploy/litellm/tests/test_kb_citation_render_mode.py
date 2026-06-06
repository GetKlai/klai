from __future__ import annotations

from klai_kb_citation_render import compose_non_streaming_kb_response


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
