"""
AI-assisted CSS selector detection for the crawl wizard.

When a crawl yields too little content (< 100 words), this module:
1. Re-crawls the URL with a JS snippet that extracts a DOM summary
2. Calls the klai-fast LLM alias to identify the main content selector
3. Returns the selector string (or None on failure)

SPEC-CRAWL-001 / R-4
"""
import json
import logging

import httpx

from knowledge_ingest.config import settings

logger = logging.getLogger(__name__)

# JS injected into the page to extract a DOM summary.
# Appends a <pre> element off-screen (NOT display:none — innerText returns ""
# for hidden elements, which would break crawl4ai's text extraction).
# Captured via css_selector="#__klai_dom_summary__".
# Uses only direct selector construction (id, classes) to avoid compound paths
# that differ between crawl passes.
_DOM_SUMMARY_JS = """
(async () => {
  const els = [...document.body.querySelectorAll('*')]
    .filter(el => el.innerText && el.children.length < 5)
    .map(el => ({
      tag: el.tagName.toLowerCase(),
      id: el.id || null,
      className: (typeof el.className === 'string' ? el.className : null) || null,
      wordCount: el.innerText.trim().split(/\\s+/).length,
      selector: el.id
        ? '#' + el.id
        : (typeof el.className === 'string' && el.className.trim())
          ? el.tagName.toLowerCase() + '.' + el.className.trim().split(/\\s+/).join('.')
          : el.tagName.toLowerCase()
    }))
    .sort((a, b) => b.wordCount - a.wordCount)
    .slice(0, 25);

  const pre = document.createElement('pre');
  pre.id = '__klai_dom_summary__';
  pre.style.cssText = 'position:absolute;left:-9999px;top:-9999px;';
  pre.textContent = JSON.stringify(els);
  document.body.appendChild(pre);
})();
"""

_LLM_PROMPT = """\
Given this DOM summary of a webpage (sorted by word count descending), identify the \
single CSS selector that contains the main article/content body. Exclude navigation, \
sidebar, footer, and header elements. Return ONLY the CSS selector string, nothing else.

DOM Summary:
{json_summary}"""


async def extract_dom_summary(url: str) -> list[dict] | None:
    """Crawl url with DOM extraction JS and return the parsed summary, or None on failure."""
    try:
        from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode  # noqa: PLC0415

        config = CrawlerRunConfig(
            cache_mode=CacheMode.BYPASS,
            js_code=_DOM_SUMMARY_JS,
            css_selector="#__klai_dom_summary__",
            word_count_threshold=0,
            page_timeout=30000,
            remove_consent_popups=True,
        )
        import asyncio  # noqa: PLC0415

        async with AsyncWebCrawler() as crawler:
            result = await asyncio.wait_for(
                crawler.arun(url=url, config=config),
                timeout=30.0,
            )
        raw = (result.markdown.raw_markdown or "").strip()
        if not raw:
            return None
        # crawl4ai wraps the <pre> content in markdown code fences or plain text
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return json.loads(raw)
    except Exception as exc:
        logger.warning("DOM summary extraction failed for %s: %s", url, exc)
        return None


async def detect_selector_via_llm(dom_summary: list[dict]) -> str | None:
    """Call klai-fast via LiteLLM proxy to identify the main content selector.

    Returns the CSS selector string, or None on any failure.
    """
    json_summary = json.dumps(dom_summary, ensure_ascii=False)
    prompt = _LLM_PROMPT.format(json_summary=json_summary)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{settings.litellm_url}/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.litellm_api_key}"},
                json={
                    "model": "klai-fast",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 64,
                    "temperature": 0,
                },
            )
            resp.raise_for_status()
        data = resp.json()
        selector = data["choices"][0]["message"]["content"].strip()
        # Basic sanity check: a CSS selector should not be empty or contain newlines
        if not selector or "\n" in selector:
            return None
        return selector
    except Exception as exc:
        logger.warning("LLM selector detection failed: %s", exc)
        return None
