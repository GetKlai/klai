"""Text footer for internal-chat answers: the sources and search activity a
widget shows as structured frames instead.

Deliberately kept separate as the last render step (docs/architecture/chat-quality-history-and-plan.md
§7.2, row "Appointment button and escalation on the widget; sources footer and
'Agent activity' internally"): a visitor can be handed to a person, an
employee needs the sources instead. The widget renders a ``sources`` frame and
an appointment button; LibreChat renders only text, so
``profile.surface == "internal"`` gets this footer appended after the answer
instead.

Ported from ``deploy/litellm/klai_kb_citation_render.py``
(``_append_visible_sources_section`` / ``_format_visible_agent_activity``,
~530-800), with two deliberate differences:

* The "Retrieval score: <band>" line is dropped entirely (product decision,
  2026-09-24 slice 6) — not ported.
* "Deelvragen" only counts ``sub_queries``, a plain list of question strings.
  The hook's ``sub_query_coverage`` also carries an ``evidence_count``/``error``
  per question (portal's retrieval does not thread that through yet — slice 4
  supplies ``sub_queries``; a later slice can enrich this line the same way the
  hook does once per-question coverage exists).
"""

from __future__ import annotations

import re
from typing import Any

from klai_chat_prompts import _language_is_dutch

from app.services.citations import format_sources_markdown

_MODE_LABEL = {
    "nl": {
        "strict": "Strict, alleen kennisbank.",
        "open": "Open, kennisbank met fallback.",
        "general": "Algemeen, geen kennisbank.",
    },
    "en": {
        "strict": "Strict, knowledge base only.",
        "open": "Open, knowledge base with fallback.",
        "general": "General, no knowledge base.",
    },
}


def _plural(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


# An Open answer that no knowledge-base source supports, labelled here and
# never by the model: the decision (answer_judge.decide_answer) is taken after
# the text was written, and on a live turn after it was streamed, so the label
# can only go in this footer.
_GENERAL_KNOWLEDGE_LINE = {
    "nl": "- Antwoord: algemene kennis, niet uit je kennisbank.",
    "en": "- Answer: general knowledge, not from your knowledge base.",
}


def _format_agent_activity(
    *, kb_mode: str, chunks_injected: int, sub_queries: list[str], language: str, general_knowledge: bool
) -> str:
    lines = [("- Modus: " if language == "nl" else "- Mode: ") + _MODE_LABEL[language][kb_mode]]
    if general_knowledge:
        lines.append(_GENERAL_KNOWLEDGE_LINE[language])
    if chunks_injected:
        if language == "nl":
            chunk_label = _plural(chunks_injected, "fragment", "fragmenten")
            lines.append(f"- Kennisbank geraadpleegd: {chunks_injected} {chunk_label} opgehaald.")
        else:
            chunk_label = _plural(chunks_injected, "chunk", "chunks")
            lines.append(f"- Knowledge base queried: {chunks_injected} {chunk_label} retrieved.")
    if sub_queries:
        if language == "nl":
            lines.append(f"- Deelvragen: {len(sub_queries)} apart gezocht.")
        else:
            lines.append(f"- Sub-questions: {len(sub_queries)} searched separately.")
    return "\n".join(lines)


def render_answer_footer(
    *,
    sources: list[dict[str, Any]],
    kb_mode: str,
    chunks_injected: int,
    sub_queries: list[str] | None = None,
    language: object = None,
    general_knowledge: bool = False,
) -> str:
    """Render the "Bronnen"/"Agent activiteit" footer, or "" without sources.

    ``general_knowledge`` (an Open answer without a supporting source) renders
    the activity section with its general-knowledge line even without sources.

    ``sources`` must be the same labelled list the answer's inline citation
    markers were composed against (``_compose_backend_managed_answer``'s
    return value) — that is what keeps the footer's source order in step with
    the numbers the employee already saw, on both a held and a live turn: both
    compose that list from the same accumulated text (see
    ``_chat_completion_streaming_with_composed_citations``'s docstring on why
    a live turn is not re-rendered).

    No sources means no footer at all — an internal turn without anything to
    disclose gets no "Agent activiteit" section either, unlike the hook (which
    can show KB-scope trace lines without a citable source); portal does not
    carry that richer ``kb_meta`` yet.

    ``language`` follows the same "no decision defaults to Dutch" rule as
    every other rendered string in this chat path (see
    ``klai_chat_prompts._language_is_dutch``): pass the turn's
    ``response_language`` / ``language_decision.language`` so the footer never
    disagrees with the answer it is attached to.
    """
    if not sources and not general_knowledge:
        return ""
    language_code = "nl" if _language_is_dutch(language) else "en"
    sections: list[str] = []
    sources_markdown = format_sources_markdown(sources)
    if sources_markdown:
        heading = "Bronnen" if language_code == "nl" else "Sources"
        sections.append(f"**{heading}**\n{sources_markdown}")
    activity = _format_agent_activity(
        kb_mode=kb_mode,
        chunks_injected=chunks_injected,
        sub_queries=sub_queries or [],
        language=language_code,
        general_knowledge=general_knowledge,
    )
    heading = "Agent activiteit" if language_code == "nl" else "Agent activity"
    sections.append(f"**{heading}**\n{activity}")
    return "\n\n".join(sections)


# --- History strip -----------------------------------------------------
#
# Ported from deploy/litellm/klai_kb_request_context.py:strip_klai_backend_footer_from_text
# (and its heading regex at :77-83), minus the base64 sources-marker line the
# hook also strips: portal never writes that marker into visible text (its
# sources travel in the structured ``sources`` field instead of an HTML
# comment for a client that ignores it).

_FOOTER_HEADING_RE = re.compile(
    r"(?im)^[ \t]*(?:#{1,6}[ \t]+)?(?:\*\*)?(Bronnen|Sources|Agent activiteit|Agent activity)(?:\*\*)?[ \t]*$"
)
_FOOTER_SOURCES_HEADINGS = {"bronnen", "sources"}
_FOOTER_ACTIVITY_HEADINGS = {"agent activiteit", "agent activity"}
_FOOTER_BODY_BULLET_RE = re.compile(r"^[ \t]*[-*+•][ \t]+")


def _is_footer_region(text: str) -> bool:
    """A footer region contains only headings, bullet lines, and blanks."""
    for line in text.splitlines():
        if not line.strip():
            continue
        if _FOOTER_HEADING_RE.match(line):
            continue
        if _FOOTER_BODY_BULLET_RE.match(line):
            continue
        return False
    return True


def strip_answer_footer_from_text(text: str) -> str:
    """Remove this module's own footer from an earlier assistant turn.

    The footer is always the tail of the message. Cut at the LAST activity
    heading (extended back to its adjoining sources heading) and only when
    everything from the cut point onward is footer-shaped — a bare "Agent
    activity" line inside real prose must not swallow the rest of the answer.
    """
    matches = list(_FOOTER_HEADING_RE.finditer(text))
    last_activity_index = next(
        (index for index, match in enumerate(reversed(matches)) if match.group(1).lower() in _FOOTER_ACTIVITY_HEADINGS),
        None,
    )
    if last_activity_index is None:
        return text
    last_activity_index = len(matches) - 1 - last_activity_index

    activity_match = matches[last_activity_index]
    cut_match = activity_match
    for match in reversed(matches[:last_activity_index]):
        if match.group(1).lower() in _FOOTER_SOURCES_HEADINGS:
            if _is_footer_region(text[match.start() : activity_match.start()]):
                cut_match = match
            break
    if not _is_footer_region(text[cut_match.start() :]):
        return text
    return text[: cut_match.start()].rstrip()
