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
