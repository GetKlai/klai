"""What kind of chat turn this is, resolved once per request.

One pipeline serves the website widget, the partner API and the internal chat
(LibreChat). The fields below are the only ways those surfaces may differ, and
each maps to a reason in docs/architecture/chat-quality-history-and-plan.md
§7.2: who is asking (``surface``, ``user_id``), what may be used beyond the
knowledge base (``kb_mode``), what is searched (``kb_scope``, ``kb_slugs``),
and how the answer is delivered (``stream_live``). A difference between
surfaces that is not one of these is a duplicate to merge, not a new field.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Surface = Literal["widget", "partner", "internal"]
# strict: answer from the knowledge base only; open: the knowledge base plus
# general knowledge, labelled as such; general: no retrieval at all.
KbMode = Literal["strict", "open", "general"]
KbScope = Literal["org", "personal", "both"]


@dataclass(frozen=True, slots=True)
class ChatProfile:
    surface: Surface
    kb_mode: KbMode = "strict"
    kb_scope: KbScope = "org"
    # None searches every knowledge base the caller may read.
    kb_slugs: tuple[str, ...] | None = None
    # Zitadel subject of the employee; only the internal surface has one.
    user_id: str | None = None
    # Stream tokens as they arrive. Only an Open internal turn does: every
    # other turn is held until the grounding check has decided what to show.
    stream_live: bool = False
