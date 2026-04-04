"""
AI-assisted CSS selector detection for the crawl wizard.

When a crawl yields too little content (< 100 words), the crawl route:
1. Calls crawl4ai_client.crawl_dom_summary() to extract a ranked DOM summary
2. Calls detect_selector_via_llm() (this module) to identify the main content selector
3. Re-crawls with the detected selector

SPEC-CRAWL-001 / R-4
"""
import json

import httpx
import structlog

from knowledge_ingest.config import settings

logger = structlog.get_logger()

_LLM_PROMPT = """\
Given this DOM summary of a webpage (sorted by word count descending), identify the \
single CSS selector that contains the main article/content body. Exclude navigation, \
sidebar, footer, and header elements. Return ONLY the CSS selector string, nothing else.

DOM Summary:
{json_summary}"""


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
        logger.warning("crawl_llm_selector_failed", error=str(exc))
        return None
