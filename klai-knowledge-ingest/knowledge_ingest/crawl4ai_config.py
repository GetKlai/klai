"""Pure Crawl4AI request configuration builders."""

from __future__ import annotations

import copy
from typing import Any

# crawl4ai 0.9.5 (live 2026-10-22) answers HTTP 400 to any JavaScript in a
# network request (wait_for "js:", js_code, c4a_script, init_scripts), which
# ended every nightly crawl with 0 pages from 2026-10-23. The config therefore
# carries CSS only. A probe of 20 stored pages without the old JS prep fetched
# 20/20 with identical headings, +1% to +8% words and markdown links 139 -> 363:
# collapsed toggle bodies are already in the DOM, and the JS chrome removal was
# dropping link labels.
_CHROME_ROLES_SELECTOR = (
    '[role="navigation"],[role="banner"],[role="contentinfo"],'
    '[role="complementary"],[role="search"]'
)


def build_crawl_config(selector: str | None) -> dict[str, Any]:
    """Build a Crawl4AI ``CrawlerRunConfig`` payload.

    Pacing is intentionally absent: the REST server ignores those config keys,
    so ``host_pacing.HostGateRegistry`` owns request pacing.
    """
    markdown_generator: dict[str, Any] = {
        "type": "DefaultMarkdownGenerator",
        "params": {
            "content_filter": {
                "type": "PruningContentFilter",
                "params": {"threshold": 0.45, "threshold_type": "dynamic"},
            },
            "options": {"type": "dict", "value": {"ignore_links": False, "body_width": 0}},
        },
    }

    params: dict[str, Any] = {
        "cache_mode": "bypass",
        "word_count_threshold": 10,
        "remove_consent_popups": True,
        "remove_overlay_elements": True,
        "page_timeout": 30000,
        # crawl4ai waits for <body> to become visible and then ignores the
        # answer (ignore_body_visibility defaults to True). On ng-cloak /
        # v-cloak sites whose app never bootstraps that was a flat 30s per
        # page; 2000 measured byte-identical output ~5x faster, and a page
        # with a visible body leaves this wait immediately either way.
        # Supported since 0.9.3 (our issue #2129); the server clamps it.
        "body_visibility_timeout": 2000,
        "markdown_generator": markdown_generator,
    }

    if selector:
        # target_elements narrows extraction while preserving the full DOM for
        # site-link discovery; css_selector would hide navigation links.
        params["target_elements"] = [selector]
        params["excluded_tags"] = []
    else:
        params["excluded_tags"] = ["nav", "footer", "header", "aside", "script", "style"]
        params["excluded_selector"] = _CHROME_ROLES_SELECTOR
    return params


def relax_seed_crawl_config(crawler_config: dict[str, Any]) -> dict[str, Any]:
    """Retry thin seeds without chrome stripping while keeping script/style excluded."""
    relaxed = copy.deepcopy(crawler_config)
    relaxed.pop("excluded_selector", None)
    if relaxed.get("excluded_tags"):
        relaxed["excluded_tags"] = ["script", "style"]
    return relaxed
