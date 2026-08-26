"""AC-3 contract between JSON-feed record shaping and the production chunker."""

from knowledge_ingest.chunker import chunk_markdown_with_parents
from knowledge_ingest.content_profiles import get_profile


def test_kb_article_chunker_never_splits_adapter_record_lines() -> None:
    record_lengths = [120, 275, 450, 625, 800, 900] * 3
    record_lines = [
        f"- **Product {index:02d}** — description: "
        + chr(ord("a") + index % 26) * (length - len(f"- **Product {index:02d}** — description: "))
        for index, length in enumerate(record_lengths)
    ]
    document = "# JSON feed — prijzen\n\n" + "\n\n".join(record_lines)
    profile = get_profile("kb_article")

    chunks, _parents = chunk_markdown_with_parents(
        document,
        child_size=profile.chunk_tokens_max * 4,
        child_overlap=200,
    )

    assert len(document) > profile.chunk_tokens_max * 4
    for record_line in record_lines:
        assert sum(record_line in chunk.text for chunk in chunks) == 1


def test_kb_article_chunker_cuts_wrapped_oversized_records_at_segment_boundaries() -> None:
    # An oversized record is emitted by the adapter as blank-line separated
    # physical segments (each < 900 chars). The chunker may split the record
    # across chunks, but every individual segment must stay intact — a cut
    # inside a segment means the "\n\n" wrap contract regressed to "\n".
    segments = [f"veld {index:02d}: " + chr(ord("a") + index % 26) * 840 for index in range(6)]
    document = "# JSON feed — prijzen\n\nVelden: veld\n\n- **Lang product** — " + "\n\n".join(
        segments
    )
    profile = get_profile("kb_article")

    chunks, _parents = chunk_markdown_with_parents(
        document,
        child_size=profile.chunk_tokens_max * 4,
        child_overlap=200,
    )

    assert len(chunks) > 1
    for segment in segments:
        assert any(segment in chunk.text for chunk in chunks), "segment cut mid-value"
