"""Contract tests for chunks-present KB context prompt assembly."""

from __future__ import annotations

from klai_kb_context_prompt import (
    KB_ANSWER_FORMAT_INSTRUCTION,
    KB_LANGUAGE_REMINDER,
    build_kb_context_prompt,
    chunk_source_url,
    normalise_guard_url,
)


def _chunk(**overrides):
    chunk = {
        "text": "Customers can book climbing courses in the app.",
        "scope": "org",
        "metadata": {"title": "Booking"},
        "source_url": "HTTPS://WWW.Example.com/docs/course?x=1#ignored",
        "chunk_id": "c1",
    }
    chunk.update(overrides)
    return chunk


def test_normalise_guard_url_rejects_sentinels_and_keeps_safe_urls():
    assert normalise_guard_url(" undefined ") == ""
    assert normalise_guard_url("ftp://example.com/file") == ""
    assert normalise_guard_url("/kb/image.png") == "/kb/image.png"
    assert normalise_guard_url("<HTTPS://WWW.Example.com/Path?x=1#fragment>") == (
        "https://example.com/Path?x=1"
    )


def test_chunk_source_url_prefers_normalised_absolute_chunk_source():
    assert chunk_source_url(_chunk()) == "https://example.com/docs/course?x=1"
    assert (
        chunk_source_url(
            _chunk(
                source_url=None,
                metadata={"source_url": "https://Docs.Example.com/a"},
            )
        )
        == "https://docs.example.com/a"
    )


def test_context_prompt_keeps_template_between_format_rules_and_chunks():
    result = build_kb_context_prompt(
        kb_narrow=False,
        context_chunks=[_chunk(text="Chunk body")],
        trusted_sources=[{"url": "https://example.com/docs/course"}],
        templates_block="[Template]\nUse short bullets.",
        images_base_url="https://images.example",
        low_confidence_inject=False,
        low_confidence_injection_disabled=False,
        low_confidence_strict_text="[strict low]",
        low_confidence_open_text="[open low]",
    )

    assert "use this as supplementary context" in result.context_block
    assert KB_ANSWER_FORMAT_INSTRUCTION in result.context_block
    assert "[Template]\nUse short bullets." in result.context_block
    assert "Chunk body" in result.context_block
    assert result.context_block.index(KB_ANSWER_FORMAT_INSTRUCTION) < result.context_block.index(
        "[Template]"
    )
    assert result.context_block.index("[Template]") < result.context_block.index("Chunk body")
    assert result.context_block.index(KB_LANGUAGE_REMINDER) > result.context_block.index(
        "[End knowledge base context]"
    )
    assert result.allowed_source_urls == ["https://example.com/docs/course"]
    assert result.citation_source_urls == {"1": "https://example.com/docs/course?x=1"}
    assert result.low_confidence_injection_applied is False


def test_context_prompt_absolutises_relative_image_urls():
    result = build_kb_context_prompt(
        kb_narrow=True,
        context_chunks=[
            _chunk(
                image_urls=[
                    "/knowledge/images/one.png",
                    "https://cdn.example/two.png",
                    "undefined",
                ]
            )
        ],
        trusted_sources=[],
        templates_block="",
        images_base_url="https://images.example",
        low_confidence_inject=False,
        low_confidence_injection_disabled=False,
        low_confidence_strict_text="[strict low]",
        low_confidence_open_text="[open low]",
    )

    assert "answer strictly using only the sources below" in result.context_block
    assert "![afbeelding 1](https://images.example/knowledge/images/one.png)" in (
        result.context_block
    )
    assert "![afbeelding 2](https://cdn.example/two.png)" in result.context_block
    assert result.allowed_image_urls == [
        "https://cdn.example/two.png",
        "https://images.example/knowledge/images/one.png",
    ]


def test_low_confidence_injection_tracks_mode_and_disabled_flag():
    open_result = build_kb_context_prompt(
        kb_narrow=False,
        context_chunks=[_chunk()],
        trusted_sources=[],
        templates_block="",
        images_base_url="https://images.example",
        low_confidence_inject=True,
        low_confidence_injection_disabled=False,
        low_confidence_strict_text="[strict low]",
        low_confidence_open_text="[open low]",
    )
    assert "[open low]" in open_result.context_block
    assert "[strict low]" not in open_result.context_block
    assert open_result.low_confidence_injection_applied is True

    disabled_result = build_kb_context_prompt(
        kb_narrow=True,
        context_chunks=[_chunk()],
        trusted_sources=[],
        templates_block="",
        images_base_url="https://images.example",
        low_confidence_inject=True,
        low_confidence_injection_disabled=True,
        low_confidence_strict_text="[strict low]",
        low_confidence_open_text="[open low]",
    )
    assert "[strict low]" not in disabled_result.context_block
    assert disabled_result.low_confidence_injection_applied is False
