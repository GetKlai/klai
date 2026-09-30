"""Which retrieved passages answer the visitor's question, decided before anyone writes.

The reranker score says a passage resembles the search query, not that it
answers the question. Measured on 101 replayed turns of the help widget
(2026-09-30): in 37 of the 82 turns where the answer model wrote, no passage
contained the answer, 22 of those 37 scored 0.4 or higher, and the model wrote
anyway, inventing menu names and steps in 17. Every check after the reply then
had to undo that. With this step in front, on the same passages, replies that
were mostly unsupported went from 19 to 1 and unsupported statements from 32%
to 16% of all statements.

One call on the checker model returns a closed verdict and the passage numbers;
code decides the route. The writer then reads only the chosen passages. No
filter on product or passage length in code: both were tried in the same
measurement and cost answers whose only source was such a passage.

Fails open: no verdict means the turn runs as it did before this step existed.
"""

from __future__ import annotations

from typing import Literal

import structlog
from pydantic import BaseModel, ConfigDict

from app.core.config import Settings
from app.services.turn_judge import conversation_excerpt, structured_judge_call

logger = structlog.get_logger()

SELECTION_SYSTEM_PROMPT = (
    "You prepare a help-centre reply. You get the conversation and numbered passages from the help articles. "
    "Decide what the writer may use, before anyone writes.\n"
    "- answers: one or more passages contain what the visitor needs for THIS question, about the same product, "
    "device and direction (import is not export, inbound is not outbound, a headset is not a phone, the mobile app "
    "is not a desk phone). List exactly those passages, at most three.\n"
    "- depends: passages answer it, but differently per variant (device, app, direction) and the conversation does "
    "not say which applies. Name the one fact to ask for in a few words, in the visitor's language. List the "
    "passages per variant.\n"
    "- not_in_passages: no passage contains what the visitor needs, however similar the topic looks. Empty list.\n"
    "A passage about the same subject that does not contain the answer does not count. A request for a person, a "
    "thank-you or a remark about the chat needs no passage: answers with an empty list."
)

# The checker's own budget on the widget is 4 s for a reply that lists every
# statement; this call reads the same passages and returns three fields.
_TIMEOUT_SECONDS = 4.0
_PASSAGE_CHARS = 2500


class PassageSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    verdict: Literal["answers", "depends", "not_in_passages"]
    passages: list[int]
    missing_fact: str

    def chosen(self, chunks: list[dict]) -> list[dict]:
        """The listed passages, in the model's order, without the same text twice.

        A matched FAQ item comes back as its whole section, so two hits in one
        section are one text under two ids (24 of 102 replayed turns).
        """
        picked: list[dict] = []
        seen: set[str] = set()
        for number in self.passages:
            if not 0 < number <= len(chunks):
                continue
            chunk = chunks[number - 1]
            text = " ".join(str(chunk.get("text") or "").split())
            if text not in seen:
                seen.add(text)
                picked.append(chunk)
        return picked


async def select_passages(
    messages: list[dict], chunks: list[dict], settings: Settings, *, delegated_org_id: str | None = None
) -> PassageSelection | None:
    passages = "\n\n".join(
        f"[{number}] {chunk.get('title') or ''}\n{str(chunk.get('text') or '')[:_PASSAGE_CHARS]}"
        for number, chunk in enumerate(chunks, 1)
    )
    selection = await structured_judge_call(
        name="passage_selection",
        system_prompt=SELECTION_SYSTEM_PROMPT,
        user_content=f"Conversation:\n{conversation_excerpt(messages)}\n\nPassages:\n{passages}",
        schema=PassageSelection,
        timeout_seconds=_TIMEOUT_SECONDS,
        settings=settings,
        model=settings.answer_grounding_model,
        delegated_org_id=delegated_org_id,
    )
    if selection is not None:
        # Counts and the closed verdict only; the missing fact is the visitor's words.
        logger.info(
            "passage_selection",
            verdict=selection.verdict,
            offered=len(chunks),
            chosen=len(selection.chosen(chunks)),
        )
    return selection
