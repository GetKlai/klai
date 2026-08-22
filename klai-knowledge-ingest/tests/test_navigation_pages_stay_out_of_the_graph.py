"""An index page holds no facts about the world, only about the documentation.

GetKlai/klai#1148. Replaying https://help.voys.nl/ through extraction on
2026-08-22 produced nothing but the meta-statements rule 1 forbids -- "Een van
de documentatieartikelen voor Freedom is getiteld 'Statistieken'" and eleven
siblings. Those edges cannot answer a question; they can only take up room in
the model's context, and each episode costs ~26 LLM calls out of the shared
klai-fast budget to produce.

Shapes below are the real measured ones:

    https://help.voys.nl/                       2283 chars   34 links     0 sentences
    https://help.voys.nl/2fa-freedom            2620 chars    2 links    12 sentences
    https://help.voys.nl/aan-de-slag            4725 chars    6 links    26 sentences
    https://help.voys.nl/yealink-dect-functies 27868 chars   32 links   143 sentences
"""

from __future__ import annotations

from knowledge_ingest.enrichment_policy import graph_episode_skip_reason


def _link_list(count: int) -> str:
    return "\n".join(f"* [Artikel {i}](https://help.voys.nl/artikel-{i})" for i in range(count))


def _article(sentences: int, links: int) -> str:
    body = " ".join(
        f"Dit is zin {i} van een echt artikel met inhoud die iets beweert."
        for i in range(sentences)
    )
    return body + "\n\n" + _link_list(links)


def test_the_index_page_is_skipped():
    """34 links, no prose at all -- the page this rule exists for."""
    assert graph_episode_skip_reason(_link_list(34)) == "navigation_page"


def test_a_long_article_with_as_many_links_is_kept():
    """Link count alone does not separate them.

    yealink-dect-functies carries 32 links, as many as the index page, and is
    one of the richest documents in the corpus. Prose is the discriminator.
    """
    assert graph_episode_skip_reason(_article(sentences=143, links=32)) is None


def test_a_short_article_with_few_links_is_kept():
    """2fa-freedom: 2 links, 12 sentences. Never near the rule."""
    assert graph_episode_skip_reason(_article(sentences=12, links=2)) is None


def test_a_handful_of_links_is_never_a_navigation_page():
    """Below the link floor the ratio is not consulted at all.

    A two-link stub with terse prose would otherwise trip it.
    """
    assert graph_episode_skip_reason("* [een](https://a)\n* [twee](https://b)") is None


def test_empty_and_missing_text_are_not_navigation_pages():
    """Absent text is someone else's problem; this rule must not claim it."""
    assert graph_episode_skip_reason("") is None
    assert graph_episode_skip_reason(None) is None
