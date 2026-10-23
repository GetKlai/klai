"""SPEC-RAG-PARENT-CHILD-001 — fetch parent text for child chunks.

Retrieval matches on small "child" chunks (Qdrant). The response then
swaps the child text for the corresponding parent's broader-context
text so the LLM sees more narrative.

Children carry ``parent_chunk_id`` in their Qdrant payload; this
module batch-fetches the parent rows from ``knowledge.parent_chunks``
in a single query.

The chat does not get the whole parent (up to 6000 chars) but a window of at
most ``PARENT_WINDOW_CHARS`` around the matched child, see ``parent_window``.

Fail-open per REQ-3: when no DB pool is available, when a parent_id
is missing in the table, or when the query fails, the caller falls
through to using the child's own text.
"""

from __future__ import annotations

from collections.abc import Iterable

import structlog

from retrieval_api.services import events

logger = structlog.get_logger()

# Cap on the text the chat receives per served child. The children are 200-300
# tokens (about 1000 chars), so 2000 chars is the child plus roughly one child
# of context on each side; the selection step verifies quotes per passage and
# reads 13-22 passages on an internal turn, so a 6000-char parent per passage
# was most of its input (plan §3, §5 row 5).
PARENT_WINDOW_CHARS = 2000


async def fetch_parents(parent_ids: Iterable[int]) -> dict[int, str]:
    """Return ``{parent_chunk_id: text}`` for the given ids.

    Missing or unfetchable ids are simply absent from the result —
    callers MUST check membership and fall back to child text.
    """
    unique_ids = sorted({int(pid) for pid in parent_ids if pid is not None})
    if not unique_ids:
        return {}

    pool = events.get_pool()
    if pool is None:
        logger.warning("parent_lookup_no_pool", count=len(unique_ids))
        return {}

    try:
        rows = await pool.fetch(
            "SELECT id, text FROM knowledge.parent_chunks WHERE id = ANY($1::bigint[])",
            unique_ids,
        )
    except Exception:
        # SPEC-SEC-HYGIENE-001 REQ-43.3 / TRY401: exc_info=True preserves the
        # traceback that the previous str(exc)[:200] dropped. F6 audit cleanup.
        logger.warning("parent_lookup_failed", count=len(unique_ids), exc_info=True)
        return {}

    return {int(row["id"]): row["text"] for row in rows}


def parent_window(
    parent_text: str,
    child_text: str,
    heading_path: str | None = None,
    cap: int = PARENT_WINDOW_CHARS,
) -> str:
    """The child and its neighbours inside the parent, at most ``cap`` chars.

    A parent that fits under the cap is returned whole. Otherwise the window is
    centred on the child, moved back inside the parent at either end, and
    trimmed to whitespace so it does not open or close inside a word. The child
    is located by its text (minus the heading prefix the chunker adds to both),
    which also works for children indexed before offsets existed. When it
    cannot be found, or is longer than the cap itself, the child text is
    returned, which is what the chat got before parents existed.
    """
    if len(parent_text) <= cap:
        return parent_text
    body = child_text
    if heading_path and body.startswith(heading_path):
        body = body[len(heading_path) :].lstrip()
    at = parent_text.find(body) if body else -1
    if at < 0:
        logger.warning("parent_window_child_not_found", child_chars=len(child_text))
        return child_text
    if len(body) > cap:
        return child_text
    child_end = at + len(body)
    start = min(max(at + len(body) // 2 - cap // 2, 0), len(parent_text) - cap)
    end = start + cap
    while 0 < start < at and not parent_text[start - 1].isspace():
        start += 1
    while child_end < end < len(parent_text) and not parent_text[end].isspace():
        end -= 1
    return parent_text[start:end].strip()
