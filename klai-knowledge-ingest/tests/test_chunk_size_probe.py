"""The probe's synthetic corpus carries what the context-prefix arms need."""

from knowledge_ingest import chunker
from scripts import chunk_size_probe as probe


def test_generator_emits_summary_and_per_passage_about_sentence():
    articles, questions = probe.generate(seed=7, products=3, per_article_questions=2)

    assert len(articles) == 3
    for article in articles:
        # The cheap arm uses only the first sentence of the summary, which names the product.
        product = article["title"].removesuffix(" help")
        assert probe.first_sentence(article["summary"]).startswith(f"Help page for {product}")
        for passage in article["passages"]:
            assert passage["setting"] in passage["about"]
            assert passage["text"] in article["content"]
    # Every gold fact sits in exactly one passage, so a chunk maps to the right "about".
    for q in questions:
        article = next(a for a in articles if a["path"] == q["path"])
        assert sum(q["gold"] in p["text"] for p in article["passages"]) == 1


def test_about_for_chunk_names_every_setting_in_the_chunk():
    articles, _ = probe.generate(seed=7, products=2, per_article_questions=1)
    article = articles[0]
    children, _ = chunker.chunk_markdown_with_parents(
        article["content"], child_size=1000, child_overlap=0
    )

    for child in children:
        about = probe.about_for_chunk(child.text, article["passages"])
        in_chunk = [p for p in article["passages"] if p["opening"] in child.text]
        assert in_chunk
        assert all(p["setting"] in about for p in in_chunk)


def test_cheap_prefix_is_title_path_plus_first_summary_sentence_without_duplicates():
    prefix = probe.cheap_prefix(
        "Zorbex 3 help",
        "Zorbex 3 help > Security",
        "Help page for Zorbex 3 covering its settings. It explains more.",
    )

    assert prefix == "Zorbex 3 help > Security. Help page for Zorbex 3 covering its settings."
    assert probe.cheap_prefix("A help", "", "One. Two.") == "A help. One."
