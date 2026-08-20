"""Pure Crawl4AI request configuration builders."""

from __future__ import annotations

import copy
from typing import Any

JS_PREP_REMOVE_CHROME = (
    "['nav','header','footer','aside',"
    "'[role=\"navigation\"]','[role=\"banner\"]','[role=\"contentinfo\"]',"
    "'[role=\"complementary\"]','[role=\"search\"]'"
    "].forEach(sel => document.querySelectorAll(sel).forEach(el => el.remove()));"
)

JS_PREP_EXPAND_TOGGLES = (
    "document.querySelectorAll('details:not([open])')"
    ".forEach(d => d.setAttribute('open', ''));"
    "document.querySelectorAll('.notion-toggle__summary, "
    '[data-block-type="toggle"] > *:first-child\')'
    ".forEach(s => s.click());"
)


def build_wait_for(*, strip_chrome: bool, ready_condition: str) -> str:
    """Compose the Crawl4AI wait predicate with one-time DOM preparation."""
    prep = (JS_PREP_REMOVE_CHROME if strip_chrome else "") + JS_PREP_EXPAND_TOGGLES
    return (
        "js:() => {"
        " const d = document.documentElement.dataset;"
        " if (!d.klaiPrepTs) { " + prep + " d.klaiPrepTs = String(Date.now()); return false; }"
        " if (Date.now() - Number(d.klaiPrepTs) < 300) return false;"
        " return " + ready_condition + ";"
        " }"
    )


def build_crawl_config(
    selector: str | None,
    login_indicator_selector: str | None = None,
) -> dict[str, Any]:
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

    ready_condition = "(document.body.innerText.trim().split(/\\s+/).length > 50)"
    if login_indicator_selector:
        selector_escaped = login_indicator_selector.replace("\\", "\\\\").replace("'", "\\'")
        ready_condition += f" && !document.querySelector('{selector_escaped}')"

    params: dict[str, Any] = {
        "cache_mode": "bypass",
        "word_count_threshold": 10,
        "wait_for": build_wait_for(
            strip_chrome=selector is None,
            ready_condition=ready_condition,
        ),
        "remove_consent_popups": True,
        "remove_overlay_elements": True,
        "page_timeout": 30000,
        "markdown_generator": markdown_generator,
    }

    if selector:
        # target_elements narrows extraction while preserving the full DOM for
        # site-link discovery; css_selector would hide navigation links.
        params["target_elements"] = [selector]
        params["excluded_tags"] = []
    else:
        params["excluded_tags"] = ["nav", "footer", "header", "aside", "script", "style"]
    return params


def relax_seed_crawl_config(crawler_config: dict[str, Any]) -> dict[str, Any]:
    """Retry thin seeds without chrome stripping while keeping script/style excluded."""
    relaxed = copy.deepcopy(crawler_config)
    relaxed.pop("wait_for", None)
    if relaxed.get("excluded_tags"):
        relaxed["excluded_tags"] = ["script", "style"]
    return relaxed
