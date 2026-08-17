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

import os
import shutil
import subprocess
import tempfile

import pytest

from knowledge_ingest.crawl4ai_client import build_crawl_config


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
    # Fallback pipeline: strip known chrome so word-count wait_for fires only
    # when real article content is present.
    assert cfg.get("excluded_tags") == [
        "nav",
        "footer",
        "header",
        "aside",
        "script",
        "style",
    ]
    # Chrome stripping moved into wait_for's one-time prep (crawl4ai >= 0.9
    # forbids js_code_before_wait on network requests).
    assert "js_code_before_wait" not in cfg
    assert "el.remove()" in cfg["wait_for"]


def test_rate_limit_translates_to_semaphore_count_and_mean_delay() -> None:
    """2026-08-17 (intermedia.com incident): rate_limit (requests/second)
    must become crawl4ai's OWN pacing controls, not be silently discarded.
    2.0 req/s → semaphore_count 2 (round(2.0), within [1, 8]) and
    mean_delay 0.5s (1 / 2.0)."""
    cfg = build_crawl_config(selector=None, rate_limit=2.0)

    assert cfg["semaphore_count"] == 2
    assert cfg["mean_delay"] == 0.5


def test_rate_limit_semaphore_count_is_bounded() -> None:
    """A very high rate_limit must not reintroduce unbounded concurrency
    against the shared crawl4ai container — bounded to 8."""
    cfg = build_crawl_config(selector=None, rate_limit=50.0)
    assert cfg["semaphore_count"] == 8

    # A very low rate_limit still gets at least one fetch in flight.
    cfg = build_crawl_config(selector=None, rate_limit=0.1)
    assert cfg["semaphore_count"] == 1
    assert cfg["mean_delay"] == 10.0


def test_no_rate_limit_omits_pacing_keys() -> None:
    """Existing behaviour (no rate_limit passed) must stay unchanged —
    neither key is present at all, not present-with-a-default."""
    cfg = build_crawl_config(selector=None)

    assert "semaphore_count" not in cfg
    assert "mean_delay" not in cfg


def test_selector_with_complex_css_is_passed_as_list() -> None:
    # Crawl4AI's target_elements expects List[str]; single-element list is
    # the stable shape across versions.
    cfg = build_crawl_config(selector=".tab-structure article")

    assert cfg["target_elements"] == [".tab-structure article"]
    assert isinstance(cfg["target_elements"], list)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("selector", [None, "article.main"])
def test_wait_for_predicate_is_valid_javascript(selector) -> None:
    """The wait_for prep predicate must stay parseable JS — crawl4ai evaluates
    it in the page context, and a syntax error silently degrades every crawl."""
    cfg = build_crawl_config(selector=selector, login_indicator_selector=".login-wall")
    js = cfg["wait_for"]
    assert js.startswith("js:")
    node = shutil.which("node")
    assert node is not None
    # A real temp file, not /dev/stdin — node --check on a stdin pipe fails
    # with ENOENT on Linux CI runners (it reopens /proc/<pid>/fd/pipe:[...]).
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(f"const f = {js[3:]};")
        path = fh.name
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, test-only syntax check
            [node, "--check", path],
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert proc.returncode == 0, proc.stderr
    finally:
        os.unlink(path)
