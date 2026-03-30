"""
Content-type parameter profiles for enrichment.
Each profile specifies HyPE behavior, context strategy, token ranges, and prompt focus.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ContentTypeProfile:
    content_type: str
    hype_enabled: Callable[[int], bool]  # (synthesis_depth) -> bool
    context_strategy: str                # name of function in context_strategies.STRATEGIES
    context_tokens_min: int
    context_tokens_max: int
    chunk_tokens_min: int
    chunk_tokens_max: int
    hype_question_focus: str


PROFILES: dict[str, ContentTypeProfile] = {
    "kb_article": ContentTypeProfile(
        content_type="kb_article",
        hype_enabled=lambda depth: depth <= 1,
        context_strategy="first_n",
        context_tokens_min=800,
        context_tokens_max=2000,
        chunk_tokens_min=300,
        chunk_tokens_max=500,
        hype_question_focus=(
            "Genereer vragen in alledaagse taal die een gebruiker zou typen "
            "— herformuleringen, synoniemen, informele varianten."
        ),
    ),
    "meeting_transcript": ContentTypeProfile(
        content_type="meeting_transcript",
        hype_enabled=lambda depth: True,
        context_strategy="rolling_window",
        context_tokens_min=600,
        context_tokens_max=1200,
        chunk_tokens_min=150,
        chunk_tokens_max=400,
        hype_question_focus=(
            "Genereer vragen over beslissingen, actiepunten, eigenaren "
            "en deadlines die in dit fragment besproken worden."
        ),
    ),
    "1on1_transcript": ContentTypeProfile(
        content_type="1on1_transcript",
        hype_enabled=lambda depth: True,
        context_strategy="rolling_window",
        context_tokens_min=400,
        context_tokens_max=800,
        chunk_tokens_min=100,
        chunk_tokens_max=300,
        hype_question_focus=(
            "Genereer vragen over toezeggingen, besproken onderwerpen "
            "en genoemde namen."
        ),
    ),
    "email_thread": ContentTypeProfile(
        content_type="email_thread",
        hype_enabled=lambda depth: depth <= 1,
        context_strategy="most_recent",
        context_tokens_min=1000,
        context_tokens_max=4000,
        chunk_tokens_min=200,
        chunk_tokens_max=500,
        hype_question_focus=(
            "Genereer vragen over de status, beslissingen "
            "en verzoeken in deze e-mailthread."
        ),
    ),
    "pdf_document": ContentTypeProfile(
        content_type="pdf_document",
        hype_enabled=lambda depth: True,
        context_strategy="front_matter",
        context_tokens_min=800,
        context_tokens_max=2000,
        chunk_tokens_min=400,
        chunk_tokens_max=800,
        hype_question_focus=(
            "Genereer how-to vragen, definitie-vragen en specificatie-vragen "
            "die dit fragment beantwoordt."
        ),
    ),
    "unknown": ContentTypeProfile(
        content_type="unknown",
        hype_enabled=lambda depth: False,
        context_strategy="first_n",
        context_tokens_min=2000,
        context_tokens_max=2000,
        chunk_tokens_min=500,
        chunk_tokens_max=500,
        hype_question_focus="",
    ),
}


def get_profile(content_type: str) -> ContentTypeProfile:
    """Return the profile for the given content type, falling back to 'unknown'."""
    return PROFILES.get(content_type, PROFILES["unknown"])
