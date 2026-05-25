from klai_citations import (
    build_citation_registry,
    compose_citations,
    render_markdown_answer,
    render_markdown_answer_with_sources,
    render_markdown_sources,
    render_structured_answer,
    render_structured_sources,
)


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


def test_citation_registry_dedupes_and_excludes_invalid_urls() -> None:
    registry = build_citation_registry(
        [
            {
                "title": "Steward ownership",
                "source_url": "https://www.getklai.com/docs/company/steward-ownership",
                "text": "Klai is steward-owned.",
            },
            {
                "title": "Duplicate title ignored",
                "source_url": "https://getklai.com/docs/company/steward-ownership",
                "text": "Steward ownership protects the mission.",
            },
            {
                "title": "Bad",
                "source_url": "not-a-url",
                "text": "This should not become a source.",
            },
        ]
    )

    assert registry.has_sources
    assert len(registry.sources) == 1
    assert registry.sources[0].title == "Steward ownership"
    assert registry.sources[0].url == "https://getklai.com/docs/company/steward-ownership"
    assert registry.sources[0].chunk_texts == [
        "Klai is steward-owned.",
        "Steward ownership protects the mission.",
    ]


def test_registry_renderers_output_markdown_and_structured_sources() -> None:
    registry = build_citation_registry(
        [
            {
                "title": "Privacy policy",
                "source_url": "https://getklai.com/docs/legal/privacy",
                "text": "The privacy policy explains data handling.",
            }
        ]
    )

    assert render_structured_sources(registry) == [
        {
            "label": "1",
            "title": "Privacy policy",
            "url": "https://getklai.com/docs/legal/privacy",
        }
    ]
    assert render_markdown_sources(registry) == "- [Privacy policy](https://getklai.com/docs/legal/privacy)"

    structured = render_structured_answer("The privacy policy explains data handling.", registry)
    assert structured.content == "The privacy policy explains data handling (1)."

    rendered = render_markdown_answer("The privacy policy explains data handling.", registry)

    assert "The privacy policy explains data handling (1)." in rendered.content
    assert "- [Privacy policy](https://getklai.com/docs/legal/privacy)" in rendered.content
