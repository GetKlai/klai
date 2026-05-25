from klai_citations import compose_citations, render_markdown_answer_with_sources


def test_render_markdown_answer_uses_retrieved_source_urls_not_model_links() -> None:
    rendered = render_markdown_answer_with_sources(
        "Klai is steward-owned [fake](https://getklai.com/made-up).\n\nSources:\n1. Bad https://bad.example",
        [
            {
                "title": "Steward ownership",
                "source_url": "https://www.getklai.com/docs/company/steward-ownership",
                "text": "Klai is steward-owned.",
            }
        ],
    )

    assert "https://getklai.com/made-up" not in rendered.content
    assert "https://bad.example" not in rendered.content
    assert "Klai is steward-owned fake (1)." in rendered.content
    assert "- [Steward ownership](https://getklai.com/docs/company/steward-ownership)" in rendered.content


def test_compose_citations_preserves_allowed_image_markdown() -> None:
    composed = compose_citations(
        "Zie ![diagram](https://getklai.getklai.com/kb-images/org/diagram.png).",
        [
            {
                "title": "Diagram",
                "source_url": "https://docs.getklai.com/diagram",
                "text": "Deze handleiding heeft een diagram.",
            }
        ],
        allowed_image_urls={"https://getklai.getklai.com/kb-images/org/diagram.png"},
    )

    assert "![diagram](https://getklai.getklai.com/kb-images/org/diagram.png)" in composed.content
