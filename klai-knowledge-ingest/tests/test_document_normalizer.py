from __future__ import annotations

import json

from knowledge_ingest.blocknote_markdown import blocknote_json_to_markdown
from knowledge_ingest.docs_provenance import (
    build_docs_source_extra,
    build_docs_source_url,
)
from knowledge_ingest.document_normalizer import normalize_document_for_chunking


def test_markdown_content_passes_through_unchanged() -> None:
    content = "---\ntitle: Plain\n---\n\n# Plain\n\nAlready markdown."

    assert normalize_document_for_chunking(content) == content


def test_blocknote_document_preserves_frontmatter_and_renders_text() -> None:
    blocks = [
        {
            "type": "heading",
            "props": {"level": 1},
            "content": [{"type": "text", "text": "Invite people", "styles": {}}],
            "children": [],
        },
        {
            "type": "paragraph",
            "content": [
                {"type": "text", "text": "Open ", "styles": {}},
                {"type": "link", "href": "https://getklai.com", "content": ["Klai"]},
                {"type": "text", "text": ".", "styles": {}},
            ],
            "children": [],
        },
    ]

    normalized = normalize_document_for_chunking(
        "---\ntitle: Invite people\n---\n\n" + json.dumps(blocks)
    )

    assert normalized.startswith("---\ntitle: Invite people\n---")
    assert "# Invite people" in normalized
    assert "Open [Klai](https://getklai.com)." in normalized


def test_blocknote_empty_document_is_not_indexed_as_json() -> None:
    assert blocknote_json_to_markdown("[]") == ""


def test_non_blocknote_json_is_left_to_callers() -> None:
    assert blocknote_json_to_markdown(json.dumps([{"not": "blocknote"}])) is None


def test_docs_provenance_builds_public_reader_url() -> None:
    source_url = build_docs_source_url(
        "org-getklai/klai-help",
        "klai-help",
        "users/invite people.md",
    )

    assert source_url == "https://getklai.getklai.com/docs/klai-help/users/invite%20people"
    assert build_docs_source_extra("org-getklai/klai-help", "klai-help", "users/page.md") == {
        "source_url": "https://getklai.getklai.com/docs/klai-help/users/page",
        "source_ref": "https://getklai.getklai.com/docs/klai-help/users/page",
    }


def test_docs_provenance_ignores_non_tenant_repos() -> None:
    assert build_docs_source_url("personal/klai-help", "klai-help", "users/page.md") is None
    assert build_docs_source_extra("personal/klai-help", "klai-help", "users/page.md") == {}
