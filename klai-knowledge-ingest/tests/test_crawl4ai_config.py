"""Pin the selector-branch contract in build_crawl_config.

Regression anchor for the wiki.redcactus.cloud /nl/ bug: when a CSS selector
is set on a web-crawler connector, the shared Crawl4AI config must NOT use
`css_selector` (that shrinks the raw HTML before BFS link discovery, so
sidebar/nav links disappear — site crawls then return 1 page). It must use
`target_elements` which only narrows the markdown/extraction pass.

If someone "simplifies" by switching back to css_selector, these tests fail
and the contract surfaces in CI before live debugging is needed.
"""

from __future__ import annotations

import pytest

from knowledge_ingest.crawl4ai_config import build_crawl_config


def test_selector_goes_to_target_elements_not_css_selector() -> None:
    cfg = build_crawl_config(selector="main")

    assert cfg.get("target_elements") == ["main"], (
        "selector must be delivered as target_elements so BFS link discovery sees the full DOM"
    )
    assert "css_selector" not in cfg, (
        "css_selector shrinks raw HTML before BFS walks the DOM — forbidden "
        "for the site-crawl pipeline"
    )
    # When a selector is supplied the trusted pipeline kicks in: nothing is
    # stripped, the caller vouches for the content scope.
    assert cfg.get("excluded_tags") == []


def test_no_selector_enables_chrome_stripping() -> None:
    cfg = build_crawl_config(selector=None)

    assert "target_elements" not in cfg
    assert "css_selector" not in cfg
    # Fallback pipeline: strip known chrome before extraction.
    assert cfg.get("excluded_tags") == [
        "nav",
        "footer",
        "header",
        "aside",
        "script",
        "style",
    ]
    # ARIA chrome is removed server-side by a CSS excluded_selector, which
    # crawl4ai 0.9.5 accepts from untrusted callers (JS is refused).
    assert cfg["excluded_selector"] == (
        '[role="navigation"],[role="banner"],[role="contentinfo"],'
        '[role="complementary"],[role="search"]'
    )
    assert "wait_for" not in cfg


def test_selector_pipeline_keeps_the_page_chrome() -> None:
    cfg = build_crawl_config(selector="main")

    assert "excluded_selector" not in cfg


def test_build_crawl_config_never_sets_ignored_pacing_keys() -> None:
    """Guard against reintroduction (2026-08-18): a prior fix added
    ``semaphore_count`` / ``mean_delay`` to this config believing crawl4ai's
    REST server enforced them. Measured live against the running server
    (``/app/api.py:681``): it builds its own ``MemoryAdaptiveDispatcher`` and
    passes THAT to ``arun_many`` — these ``CrawlerRunConfig`` keys are never
    read. They still pass the untrusted-config boundary silently (HTTP 200),
    so a future "fix" that sets them again would look correct in review and
    in this exact kind of unit test, and would do nothing in production. Real
    pacing lives in ``_chunked_bulk_fetch`` (client-side burst + gap), not in
    this config. ``build_crawl_config`` no longer even accepts a
    ``rate_limit`` argument — see its docstring.
    """
    cfg = build_crawl_config(selector=None)

    assert "semaphore_count" not in cfg
    assert "mean_delay" not in cfg


def test_selector_with_complex_css_is_passed_as_list() -> None:
    # Crawl4AI's target_elements expects List[str]; single-element list is
    # the stable shape across versions.
    cfg = build_crawl_config(selector=".tab-structure article")

    assert cfg["target_elements"] == [".tab-structure article"]
    assert isinstance(cfg["target_elements"], list)


_JS_PREFIXES = ("js:", "()", "function")
_JS_CONFIG_KEYS = ("js_code", "js_code_before_wait", "c4a_script", "init_scripts")


def _js_strings(node: object, path: str = "") -> list[str]:
    if isinstance(node, dict):
        return [hit for k, v in node.items() for hit in _js_strings(v, f"{path}/{k}")]
    if isinstance(node, list):
        return [hit for i, v in enumerate(node) for hit in _js_strings(v, f"{path}/{i}")]
    if isinstance(node, str) and node.lstrip().startswith(_JS_PREFIXES):
        return [path]
    return []


@pytest.mark.parametrize("selector", [None, "article.main"])
def test_crawler_config_carries_no_javascript_so_crawl4ai_0_9_5_accepts_it(selector) -> None:
    """crawl4ai 0.9.5 answers HTTP 400 "a JavaScript wait_for is not permitted
    from an untrusted request" to a config with page JS; from 2026-10-23 that
    ended every nightly crawl with 0 pages."""
    cfg = build_crawl_config(selector=selector)

    assert _js_strings(cfg) == []
    assert not [key for key in _JS_CONFIG_KEYS if key in cfg]
