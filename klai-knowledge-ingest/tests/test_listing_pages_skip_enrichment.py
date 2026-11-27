"""A listing page of link lines with short teasers is a navigation page.

A crawled search-result list has one heading link per result followed by one line
of excerpt (~170 characters). The excerpts are not writing about the world, so
the page must skip the graph and LLM enrichment, while an article whose links are
each followed by a real paragraph stays in both.
"""

from __future__ import annotations

from knowledge_ingest.enrichment_policy import enrichment_skip_reason, graph_episode_skip_reason


def _listing(results: int, teaser_chars: int) -> str:
    teaser = ("Below are the reasons why these devices are recommended " * 10)[:teaser_chars]
    return "\n".join(
        f"  {i}. ### [Result title {i}](https://example.com/articles/{i})\n{teaser}"
        for i in range(1, results + 1)
    )


def _article_with_link_lines(links: int, paragraph_chars: int) -> str:
    paragraph = ("A paragraph of real writing that explains how the feature works. " * 20)[
        :paragraph_chars
    ]
    return "\n".join(
        f"* [Related topic {i}](https://example.com/articles/{i})\n{paragraph}"
        for i in range(links)
    )


def test_a_search_result_list_with_excerpts_is_a_navigation_page() -> None:
    page = _listing(results=20, teaser_chars=170)

    assert graph_episode_skip_reason(page) == "navigation_page"
    assert (
        enrichment_skip_reason(chunk_count=3, extra_payload={}, document_text=page)
        == "navigation_page"
    )


def test_link_lines_followed_by_long_paragraphs_are_an_article() -> None:
    page = _article_with_link_lines(links=10, paragraph_chars=500)

    assert graph_episode_skip_reason(page) is None
    assert enrichment_skip_reason(chunk_count=3, extra_payload={}, document_text=page) is None


def test_sections_under_headings_that_link_to_themselves_are_an_article() -> None:
    section = ("This section explains one setting and when to change it. " * 4)[:200]
    page = "\n".join(f"## [Setting {i}](#setting-{i})\n{section}" for i in range(10))

    assert graph_episode_skip_reason(page) is None
