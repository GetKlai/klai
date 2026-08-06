"""Unrendered ``{{...}}`` template residue must not reach the KB.

Regression context (2026-08-06, support.ascendcloud.com): the site is an
AngularJS app that fails to bootstrap in the crawler browser, so nav items
arrive as literal ``{{item.Name}}`` bullets and ``{{selectedCountryPhone...}}``
fragments — which then showed up verbatim in the connector preview and would
be ingested as document content.
"""

from __future__ import annotations

from knowledge_ingest.crawl4ai_client import (
    _extract_result,
    strip_unrendered_template_lines,
)

# Mirrors the actual junk observed in the ascendcloud preview.
_ASCEND_LIKE = """# Welcome to the Support Center!
Dedicated and reliable support, accessible from anywhere.
Get Support by Product
* {{item.Name}}
* {{item.Name}}
{{selectedCountryPhone.countryCode}} {{selectedCountryPhone.text}} [ {{selectedLanguage.text}} ]
[Cloud telefooncentrale voor Ascend](https://example.com/articles/detail/a_id/{{item.ID}})
Top Answers Across Products"""


def test_pure_token_lines_are_dropped() -> None:
    cleaned = strip_unrendered_template_lines(_ASCEND_LIKE)
    assert "{{item.Name}}" not in cleaned
    assert "{{selectedCountryPhone" not in cleaned
    assert "Welcome to the Support Center!" in cleaned
    assert "Top Answers Across Products" in cleaned


def test_link_with_real_anchor_text_survives() -> None:
    # The anchor text is genuine content even though the URL holds a token;
    # only the URL is blanked for the word count, the line itself is kept
    # verbatim.
    cleaned = strip_unrendered_template_lines(_ASCEND_LIKE)
    assert "Cloud telefooncentrale voor Ascend" in cleaned


def test_prose_mentioning_tokens_is_kept_unchanged() -> None:
    md = "Use {{name}} to insert the customer name into the template."
    assert strip_unrendered_template_lines(md) == md


def test_fenced_code_is_never_touched() -> None:
    md = "intro\n```\n{{item.Name}}\n```\nafter"
    assert strip_unrendered_template_lines(md) == md


def test_markdown_without_tokens_is_returned_as_is() -> None:
    md = "# Title\n\nPlain prose only."
    assert strip_unrendered_template_lines(md) is md


def test_extract_result_cleans_and_recounts() -> None:
    page = {
        "url": "https://example.com/hub",
        "markdown": {"fit_markdown": _ASCEND_LIKE, "raw_markdown": _ASCEND_LIKE},
        "html": "<html></html>",
        "success": True,
    }
    result = _extract_result("https://example.com/hub", page)
    assert "{{item.Name}}" not in result.fit_markdown
    assert "{{item.Name}}" not in result.raw_markdown
    # word_count is computed AFTER cleaning, so token junk no longer pads a
    # thin page over the preview threshold.
    assert result.word_count == len(result.fit_markdown.split())
