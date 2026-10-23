"""Link lists and one-sentence stubs must not become retrievable chunks.

Plan: docs/architecture/knowledge-base-ingestion-and-retrieval-plan.md section 5, row 4.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import structlog

from knowledge_ingest.chunker import chunk_markdown_with_parents
from knowledge_ingest.crawl4ai_types import CrawlResult

_PROSE = (
    "Dit artikel legt stap voor stap uit hoe je de telefoon instelt voor gebruik op kantoor. "
    "Je begint met het koppelen van het toestel, daarna kies je de juiste lijn, en tot slot "
    "controleer je of inkomende gesprekken bij het juiste team binnenkomen en de voicemail werkt."
)

_RELATED = (
    "## Gerelateerde artikelen\n\n"
    "- [Telefoon koppelen](https://example.com/koppelen)\n"
    "- [Voicemail instellen](https://example.com/voicemail)\n"
    "- [Doorschakelen](https://example.com/doorschakelen)\n"
)


def _noise_event(logs: list[dict]) -> dict:
    events = [e for e in logs if e["event"] == "chunker_noise_removed"]
    assert len(events) == 1
    return events[0]


def test_related_links_section_is_not_a_chunk_and_is_counted() -> None:
    doc = f"# Telefoon instellen\n\n{_PROSE}\n\n{_RELATED}"

    with structlog.testing.capture_logs() as logs:
        children, parents = chunk_markdown_with_parents(doc)

    joined = "\n".join(c.text for c in children)
    assert "Gerelateerde artikelen" not in joined
    assert "example.com/koppelen" not in joined
    assert "telefoon instelt" in joined
    assert len(parents) == 1
    assert _noise_event(logs)["link_blocks_dropped"] == 1


def test_link_labels_inside_a_prose_section_do_not_make_it_a_link_block() -> None:
    doc = (
        "# Telefoon instellen\n\n"
        f"{_PROSE} Ga naar [Instellingen](https://example.com/instellingen) om te beginnen."
    )

    with structlog.testing.capture_logs() as logs:
        children, _ = chunk_markdown_with_parents(doc)

    assert "[Instellingen](https://example.com/instellingen)" in children[0].text
    event = _noise_event(logs)
    assert (event["link_blocks_dropped"], event["stubs_merged"]) == (0, 0)


def test_document_that_is_only_a_link_list_keeps_its_sections() -> None:
    # An index page has nothing else to retrieve; dropping it would turn the
    # artifact into "empty document" at ingest.
    doc = f"# Overzicht\n\n{_RELATED}"

    children, _ = chunk_markdown_with_parents(doc)

    assert children


def test_one_sentence_section_merges_into_the_following_section() -> None:
    doc = (
        "# Telefoon\n\n## Windows\n\nGebruik de Windows-app.\n\n"
        f"## Instellen\n\n{_PROSE}\n\n## Afsluiten\n\n{_PROSE}"
    )

    with structlog.testing.capture_logs() as logs:
        children, parents = chunk_markdown_with_parents(doc)

    assert len(parents) == 2
    assert not any(c.text.strip().endswith("Gebruik de Windows-app.") for c in children)
    merged = next(c for c in children if "Gebruik de Windows-app." in c.text)
    assert merged.heading_path == "Telefoon > Instellen"
    assert "Windows" in merged.text
    assert "telefoon instelt" in merged.text
    assert _noise_event(logs)["stubs_merged"] == 1


def test_stubs_in_a_row_keep_their_own_heading_in_front_of_their_own_text() -> None:
    doc = (
        "# Telefoon\n\n## Let op\n\nBel eerst de beheerder.\n\n"
        f"## Windows\n\nGebruik de Windows-app.\n\n## Instellen\n\n{_PROSE}"
    )

    children, parents = chunk_markdown_with_parents(doc)

    assert len(parents) == 1
    text = children[0].text
    assert text.index("Let op") < text.index("Bel eerst") < text.index("Windows\n\nGebruik")
    assert text.index("Gebruik de Windows-app.") < text.index("telefoon instelt")


def test_last_stub_merges_into_the_preceding_section() -> None:
    doc = f"# Telefoon\n\n## Instellen\n\n{_PROSE}\n\n## Let op\n\nBel eerst de beheerder."

    children, parents = chunk_markdown_with_parents(doc)

    assert len(parents) == 1
    assert children[0].heading_path == "Telefoon > Instellen"
    assert "Let op" in children[0].text
    assert "Bel eerst de beheerder." in children[0].text


def test_stub_under_one_top_level_heading_does_not_move_to_the_next_one() -> None:
    doc = f"# Telefoon\n\nEen kort antwoord.\n\n# Webphone\n\n{_PROSE}"

    children, _ = chunk_markdown_with_parents(doc)

    short = next(c for c in children if "Een kort antwoord." in c.text)
    assert short.heading_path == "Telefoon"
    assert "telefoon instelt" not in short.text


def test_stub_never_merges_across_a_different_parent_heading() -> None:
    doc = (
        f"# Telefoon\n\n## Windows\n\n### Opmerking\n\nKort.\n\n"
        f"# Webphone\n\n## Instellen\n\n{_PROSE}"
    )

    children, _ = chunk_markdown_with_parents(doc)

    kort = next(c for c in children if "Kort." in c.text)
    assert kort.heading_path == "Telefoon > Windows > Opmerking"
    assert "telefoon instelt" not in kort.text


def test_the_only_section_of_a_document_stays_even_when_short() -> None:
    children, parents = chunk_markdown_with_parents("# Status\n\nAlles werkt.")

    assert [c.text for c in children] == ["Status\n\nAlles werkt."]
    assert len(parents) == 1


@pytest.mark.asyncio
async def test_link_graph_is_fed_from_crawl_links_not_from_chunks() -> None:
    from knowledge_ingest.adapters.crawler import _build_link_graph

    internal = [
        {"href": "https://example.com/koppelen", "text": "Telefoon koppelen"},
        {"href": "https://example.com/voicemail", "text": "Voicemail instellen"},
    ]
    markdown = f"# Telefoon instellen\n\n{_PROSE}\n\n{_RELATED}"
    result = CrawlResult(
        url="https://example.com/telefoon",
        fit_markdown=markdown,
        raw_markdown=markdown,
        html="",
        word_count=len(markdown.split()),
        success=True,
        links={"internal": internal},
    )
    children, _ = chunk_markdown_with_parents(result.fit_markdown)
    assert "example.com/koppelen" not in "\n".join(c.text for c in children)

    with patch("knowledge_ingest.adapters.crawler.pg_store") as pg:
        pg.upsert_page_links = AsyncMock()
        await _build_link_graph(MagicMock(), [result], "org", "kb")

    assert pg.upsert_page_links.await_args.kwargs["links"] == internal
