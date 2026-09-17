"""Whether an uncited draft reply may reach the visitor, decided after generation.

SPEC-RAG-CLARIFY-FLOW-001 REQ-2, decision 2 on the widget path. The composer
replaces every reply without a citable source with the fixed refusal, which
also throws away a clarifying question or a natural "I can't find that, is it
about your invoice or your subscription?" — neither ever cites anything. This
module asks one question about the FINAL draft: does it state anything about
the organisation? It is the post-generation check ``turn_scope.py`` names as
its structural upgrade, and it has the same shape for the same measured reason:
a fixed choice list via ``response_format``, never a boolean.

The prompt, schema and parser live in ``klai_chat_prompts`` so both chat paths
classify identically; only the HTTP call lives here.

Fail direction is the fixed refusal: any failure (timeout, upstream error, bad
JSON, a category outside the two allowed values) returns ``None``, and
``may_show_model_text_without_sources`` reads ``None`` like ``claims``. A broken
classifier therefore restores exactly today's behaviour and can never let an
unverified claim about the organisation through.
"""

from __future__ import annotations

import asyncio
from typing import Literal

import httpx
import structlog
from klai_chat_prompts import (
    ANSWER_CLAIMS_SYSTEM_PROMPT,
    answer_claims_response_format,
    parse_answer_claims,
)

from app.core.config import Settings

logger = structlog.get_logger()

# turn_scope classifies one short visitor message within 2 s. This call carries
# the visitor message, the whole draft reply and the article titles, roughly
# three to five times that input, while the output stays a single enum. Prefill
# is the part that grows, so the bound grows with it rather than staying at 2 s.
# Kept well under the marker path's upstream budget, because the visitor is
# already waiting on a fully buffered reply, and a timeout only costs the turn
# its friendly wording: the fixed refusal is what they would have got anyway.
# Not yet measured against the running service; REQ-6's classifier_failed share
# is the number that says whether 4 s is too tight.
_CLASSIFIER_TIMEOUT_SECONDS = 4.0


def _classifier_input(visitor_query: str, draft: str, article_titles: list[str]) -> str:
    titles = "\n".join(f"- {title}" for title in article_titles) or "(none)"
    return (
        f"Visitor's last message:\n{visitor_query}\n\n"
        f"Draft reply:\n{draft}\n\n"
        f"Titles of the articles available when the draft was written:\n{titles}"
    )


async def classify_answer_claims(
    *,
    visitor_query: str,
    draft: str,
    article_titles: list[str],
    settings: Settings,
) -> Literal["no_claims", "claims"] | None:
    """Return ``no_claims``, ``claims``, or ``None`` when the call failed.

    ``visitor_query`` must be the visitor's own last message, never the
    rewritten retrieval query: the classifier judges the draft as a reply to
    what the visitor asked. Never raises.
    """
    try:
        async with asyncio.timeout(_CLASSIFIER_TIMEOUT_SECONDS):
            async with httpx.AsyncClient(timeout=_CLASSIFIER_TIMEOUT_SECONDS) as client:
                response = await client.post(
                    f"{settings.litellm_base_url}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {settings.litellm_master_key}"},
                    json={
                        "model": settings.extraction_model,
                        "messages": [
                            {"role": "system", "content": ANSWER_CLAIMS_SYSTEM_PROMPT},
                            {"role": "user", "content": _classifier_input(visitor_query, draft, article_titles)},
                        ],
                        "response_format": answer_claims_response_format(),
                    },
                )
                response.raise_for_status()
                result = parse_answer_claims(response.json()["choices"][0]["message"]["content"])
    except Exception:
        logger.warning("answer_claims_classification_failed", exc_info=True)
        return None
    if result is None:
        logger.warning("answer_claims_classification_unparseable")
    return result


def answer_claims_label(result: Literal["no_claims", "claims"] | None) -> str:
    """The outcome as one queryable word; a failure is kept apart from ``claims``.

    Same reasoning as ``turn_scope.scope_label``: both behave identically, but a
    rising failure rate is a signal about the classifier, not about the drafts.
    """
    return result or "classifier_failed"
