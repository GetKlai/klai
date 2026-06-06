"""Contract tests for shared LiteLLM KB URL guards."""

from __future__ import annotations

from klai_kb_urls import absolute_image_url, chunk_source_url, normalise_guard_url


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


def test_absolute_image_url_resolves_relative_paths_against_base_url():
    assert (
        absolute_image_url("/knowledge/images/one.png", images_base_url="https://images.example")
        == "https://images.example/knowledge/images/one.png"
    )
    assert (
        absolute_image_url("https://cdn.example/two.png", images_base_url="https://images.example")
        == "https://cdn.example/two.png"
    )
    assert absolute_image_url("undefined", images_base_url="https://images.example") == ""
