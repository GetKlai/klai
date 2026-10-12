"""The case of a help-widget conversation: what the visitor needs now, kept across turns.

Every earlier step read the raw conversation and drew its own conclusion
about what the visitor meant: the turn judge, the query rewrite, the
selection step, the writer. Nothing wrote that conclusion down, so a short
follow-up ("Android", "als ingaand nummer", "dit werkt niet") lost the topic
in the search and came back as "not found". In the first week of real
traffic after the selection step that was the largest fault: over twenty
follow-up turns in 58 conversations (plan §8, 12 October).

One call reads the case as it stood after the previous turn and the
conversation so far, and writes the case anew: the problem, product and
variant, what was tried, what the visitor wants now, and two search queries
that name the product and the problem. Measured on 111 real follow-up turns
(September review plus October traffic): with the visitor's own words as the
primary query and the case's two queries as extra passes, the selection step
found a passage that answers in 77 turns against 59 with the raw message
alone, winning 24 and losing 6, where the losses were mostly the selection's
own variance on identical passages. A keyword query as the primary query
instead won as often and lost almost twice as much, so the visitor's words
stay primary.

The case is stored with the assistant turn (``answer_signals.case``) and read
back on the next request. Fails open: no case means the turn runs as it did
before this step existed.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.core.config import Settings
from app.services.turn_judge import conversation_excerpt, structured_judge_call

CASE_SYSTEM_PROMPT = (
    "You keep the case file of a conversation between a visitor and a company's help chat. You get the "
    "case as it stood after the previous turn (empty on the first turn) and the conversation so far, with "
    "the visitor's latest message marked. Update the case so it describes what the visitor needs NOW.\n"
    "problem: one sentence in the visitor's language: what does not work or what they want to achieve. A "
    "short latest message that answers the assistant's question, corrects it, or says a step did not work, "
    "narrows the earlier problem; it does not replace it. Only a message about something else starts a new "
    "problem.\n"
    "product: the product, app, device or module the problem is about (for example the Android app, the "
    "Webphone, a Yealink W73H, the dial plan), as far as the visitor has said; otherwise empty.\n"
    "variant: direction or situation that decides the answer (inbound or outbound, import or export, "
    "temporary or permanent), as far as said; otherwise empty.\n"
    "tried: what the visitor says they already did or checked, as short items; keep earlier items.\n"
    "wants: knowledge (how something works or how to fix it), human (asks for a person), contact (asks "
    "how to reach the company: call, mail, ticket), appointment (asks to schedule), chat (greeting, thanks, "
    "remark about the chat), other.\n"
    "sentiment: the visitor's tone in the latest message.\n"
    "queries: two search queries for the help articles, in the language of the help articles ("
    "Dutch), each at most twelve words, each naming the product and the problem of the case. The first "
    "is the plain problem; the second adds the variant or what was tried. Never a question word, never "
    "the visitor's name or numbers."
)

# The turn judge, which this call runs beside or just before, has 2 s; the
# case carries more fields and a Dutch rewrite, measured at 0.9 s median on
# the small model.
_TIMEOUT_SECONDS = 3.0
# retrieval-api rejects a variant over 500 characters (models.py).
_MAX_QUERY_CHARS = 500
# Visitor wants that are a route, not a knowledge question.
CONTACT_WANTS = frozenset({"human", "contact", "appointment"})


class Case(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    problem: str
    product: str
    variant: str
    tried: list[str]
    wants: Literal["knowledge", "human", "contact", "appointment", "chat", "other"]
    sentiment: Literal["negative", "neutral", "positive"]
    queries: list[str]

    def search_variants(self, query: str) -> list[str]:
        """The case's queries as extra retrieval passes beside the visitor's own words."""
        seen = {" ".join(query.split()).lower()}
        variants = []
        for candidate in self.queries:
            cleaned = " ".join(candidate.split())[:_MAX_QUERY_CHARS]
            if cleaned and cleaned.lower() not in seen:
                seen.add(cleaned.lower())
                variants.append(cleaned)
        return variants[:2]


def is_follow_up(messages: list[dict]) -> bool:
    """A turn after the visitor's first: the earlier turns carry the topic the latest message leans on."""
    return sum(message.get("role") == "user" for message in messages) > 1


async def update_case(
    messages: list[dict],
    previous: dict | None,
    settings: Settings,
    *,
    delegated_org_id: str | None = None,
) -> Case | None:
    previous_text = Case.model_validate(previous).model_dump_json() if previous else "(none)"
    return await structured_judge_call(
        name="case_update",
        system_prompt=CASE_SYSTEM_PROMPT,
        user_content=f"Case after the previous turn:\n{previous_text}\n\nConversation:\n{conversation_excerpt(messages)}",
        schema=Case,
        timeout_seconds=_TIMEOUT_SECONDS,
        settings=settings,
        delegated_org_id=delegated_org_id,
    )
