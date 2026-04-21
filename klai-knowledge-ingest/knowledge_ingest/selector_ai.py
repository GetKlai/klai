"""
AI-assisted CSS selector detection for the crawl wizard.

Content selector (SPEC-CRAWL-001 / R-4):
  When a crawl yields too little content (< 100 words), the crawl route calls
  ``detect_selector_via_llm()`` to identify the main content container.

Login indicator (SPEC-CRAWL-004 / REQ-2):
  When cookies are present, ``detect_login_indicator_via_llm()`` identifies a DOM
  element that is only visible to logged-in users (logout button, user menu, etc.).
"""

import json

import httpx
import structlog

from knowledge_ingest.config import settings

logger = structlog.get_logger()

_CONTENT_SELECTOR_PROMPT = """\
Given this DOM summary of a webpage (sorted by word count descending), identify the \
single CSS selector that contains the main article/content body. Exclude navigation, \
sidebar, footer, and header elements. Return ONLY the CSS selector string, nothing else.

DOM Summary:
{json_summary}"""

_LOGIN_INDICATOR_PROMPT = """\
Given this DOM summary of an authenticated webpage, identify a SINGLE CSS selector for \
an element that is ONLY visible when the user is logged in. Good candidates:
- Logout / sign-out buttons or links
- User avatar or profile picture
- Account menu or user dropdown
- Dashboard navigation that anonymous users don't see

Return ONLY the CSS selector string (e.g. ".user-menu", "a[href*=logout]", \
"#account-dropdown"). If you cannot identify a login-specific element with confidence, \
return the word NONE.

DOM Summary:
{json_summary}"""


async def _call_llm(prompt: str, log_event: str) -> str | None:
    """Shared LLM call via LiteLLM proxy. Returns stripped response or None."""
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
        result = data["choices"][0]["message"]["content"].strip()
        if not result or "\n" in result:
            return None
        return result
    except Exception as exc:
        logger.warning(log_event, error=str(exc))
        return None


async def detect_selector_via_llm(dom_summary: list[dict]) -> str | None:
    """Identify the main content CSS selector from a DOM summary.

    Returns the CSS selector string, or None on any failure.
    """
    json_summary = json.dumps(dom_summary, ensure_ascii=False)
    prompt = _CONTENT_SELECTOR_PROMPT.format(json_summary=json_summary)
    return await _call_llm(prompt, "crawl_llm_selector_failed")


async def detect_login_indicator_via_llm(dom_summary: list[dict]) -> str | None:
    """Identify a login-indicator CSS selector from a DOM summary.

    SPEC-CRAWL-004 REQ-2: detects elements only visible to authenticated users
    (logout buttons, user menus, account dropdowns). Returns None if no confident
    match or if the LLM returns "NONE".

    Returns the CSS selector string, or None.
    """
    json_summary = json.dumps(dom_summary, ensure_ascii=False)
    prompt = _LOGIN_INDICATOR_PROMPT.format(json_summary=json_summary)
    result = await _call_llm(prompt, "crawl_llm_login_indicator_failed")
    if result and result.upper() == "NONE":
        return None
    return result
