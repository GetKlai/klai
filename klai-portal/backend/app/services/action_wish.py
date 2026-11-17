"""Whether an employee's last message asks the internal chat to perform an action with a tool.

The internal chat's front-end sends its tool definitions with every request.
With those tools beside the reply format the production model ran away on
8 of 8 replayed turns (SPEC-CHAT-INTERNAL-ROUTE-001 §1), so the writer gets
the tools only on an action turn. One small call on the case's alias reads the
wish, quote first; code accepts it only when the quote stands literally in
the last message, the pattern of the case's contact wish (case_file._verified_routes). A field for
this inside the case prompt was measured and rejected on 17 November 2026: it
read pasted material as a plain question on 16 of 19 real messages and
changed the case's ``wants`` on 5 of 75.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.core.config import Settings
from app.services.case_file import _TIMEOUT_SECONDS as CASE_TIMEOUT_SECONDS
from app.services.chat_safety import _last_user_message
from app.services.turn_judge import every_field_required, structured_judge_call

_PROMPT = (
    "You read the last message an employee sent to an assistant that can use tools. "
    "quote: the employee's own words, copied verbatim from that message, that ask the assistant to perform an "
    "action with a tool: save something to the knowledge base, create, change or list a page, or search the web. "
    "Empty when the message asks a question, asks for a text, or only pastes material.\n"
    "action: true only when quote is not empty."
)
# The quote is one phrase; the measured call took 0.54 s median (17 November 2026).
_MAX_TOKENS = 100
# A pasted report can run to thousands of words; the wish is said in a sentence
# before or after it, so the call reads this much of the start and of the end.
_MAX_EDGE_CHARS = 2000

ActionOutcome = Literal["action", "none", "quote_not_found", "failed"]


class _ActionWish(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, json_schema_extra=every_field_required)

    quote: str
    action: bool


async def read_action_wish(messages: list[dict], settings: Settings, *, delegated_org_id: str | None) -> ActionOutcome:
    """``action`` only when the call says so with a quote that stands in the last message; anything else is not."""
    latest = _last_user_message(messages) or ""
    wish = await structured_judge_call(
        name="action_wish",
        system_prompt=_PROMPT,
        user_content=_edges(latest),
        schema=_ActionWish,
        timeout_seconds=CASE_TIMEOUT_SECONDS,
        settings=settings,
        delegated_org_id=delegated_org_id,
        max_tokens=_MAX_TOKENS,
    )
    if wish is None:
        return "failed"
    if not wish.action or not wish.quote.strip():
        return "none"
    return "action" if _literal(wish.quote) in _literal(latest) else "quote_not_found"


def _edges(message: str) -> str:
    """The message, or its start and its end when it is longer than both together."""
    if len(message) <= 2 * _MAX_EDGE_CHARS:
        return message
    return f"{message[:_MAX_EDGE_CHARS]}\n[...]\n{message[-_MAX_EDGE_CHARS:]}"


def _literal(text: str) -> str:
    """Text as compared for a literal quote: whitespace collapsed, case folded."""
    return " ".join(text.split()).casefold()
