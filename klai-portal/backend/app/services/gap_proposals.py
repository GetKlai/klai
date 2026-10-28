"""What is missing for one knowledge-gap cluster, and where it belongs (knowledge-gap-to-article, phase 1).

One model call on ``klai-judge`` per cluster, made only when the owner asks
for it (``POST /api/app/gaps/clusters/{cluster_key}/proposal``). The call
carries the tenant's Zitadel org as ``_klai_delegated_org_id``
(``structured_judge_call``): that is how the chat path's customer text is
masked, because LiteLLM's PII enforcer masks a master-key call only for the
org that travels with it (app/services/litellm_delegation.py).

The prompt lives next to the service like the other portal judge prompts
(support_gap_grouping.py, conversation_judge.py); klai-libs/chat-prompts holds
the chat answer prompts only.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.core.config import settings
from app.services.turn_judge import every_field_required, structured_judge_call

# The Vibe allowance alias the nightly judges use; never a chat alias, so a
# burst of proposals cannot eat the chat's quota.
PROPOSAL_MODEL = "klai-judge"
MAX_EXAMPLE_CHARS = 4000
# Mistral Medium writing up to 4,000 characters of example text.
_TIMEOUT_SECONDS = 90.0

PROPOSAL_SYSTEM_PROMPT = """You help the owner of a knowledge base close a gap: customers asked something the \
articles do not answer.

You get the customers' questions (their own words), the parts of their questions no article answered, the nearest \
existing articles (title and URL; their text is not given) and the language of the questions. Write:

- missing: what is missing, in two to five sentences, in that language.
- where_kind: "existing" when the answer belongs in one of the given articles, otherwise "new".
- where_title: for "existing", the title of that article copied exactly as given; for "new", a title for the new \
article.
- where_section: the section it belongs in: the heading inside the existing article, or for a new article the \
nearest section of the knowledge base the given articles suggest. Empty when you cannot tell.
- example_text: an example text in markdown, in that language and in the plain style of a help article, that the \
owner can edit and publish. End it with the line "Ook genoemd als: " followed by the customers' own words for this \
question. Keep it under 3,500 characters.
- open_facts: every fact the owner must fill in before publishing (a menu path, a limit, a price, a time).

Never invent facts, steps, phone numbers, e-mail addresses, prices or links. Where the example text needs a fact \
you were not given, write a placeholder like [menu path] and list that fact in open_facts.
"""


class ProposalDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, json_schema_extra=every_field_required)

    missing: str
    where_kind: Literal["existing", "new"]
    where_title: str
    where_section: str
    example_text: str
    open_facts: list[str]


async def draft_proposal(
    *,
    questions: list[str],
    open_parts: list[str],
    articles: list[dict],
    language: str | None,
    delegated_org_id: str,
) -> ProposalDraft | None:
    """The model's proposal for one cluster, or None when the call or its shape failed (logged by the call)."""
    user_content = json.dumps(
        {
            "language": language or "unknown",
            "questions": questions,
            "unanswered_parts": open_parts,
            "nearest_articles": [{"title": a.get("title"), "url": a.get("url")} for a in articles],
        },
        ensure_ascii=False,
    )
    return await structured_judge_call(
        name="gap_proposal",
        system_prompt=PROPOSAL_SYSTEM_PROMPT,
        user_content=user_content,
        schema=ProposalDraft,
        timeout_seconds=_TIMEOUT_SECONDS,
        settings=settings,
        model=PROPOSAL_MODEL,
        delegated_org_id=delegated_org_id,
    )
