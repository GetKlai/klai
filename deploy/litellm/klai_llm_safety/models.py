"""Data models for Klai LLM safety decisions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SafetyAction(StrEnum):
    ALLOW = "allow"
    BLOCK = "block"
    NEEDS_PROVIDER = "needs_provider"


class SafetyPhase(StrEnum):
    INPUT = "input"
    CONTEXT = "context"
    OUTPUT = "output"


class SafetySurface(StrEnum):
    WIDGET = "widget"
    PARTNER_CHAT = "partner_chat"
    LIBRECHAT = "librechat"
    RETRIEVAL_SYNTHESIS = "retrieval_synthesis"
    RETRIEVAL_COREFERENCE = "retrieval_coreference"
    INGEST_ENRICHMENT = "ingest_enrichment"
    SCRIBE_SUMMARY = "scribe_summary"
    UNKNOWN = "unknown"


class SafetyCategory(StrEnum):
    PROMPT_INJECTION = "prompt_injection"
    SYSTEM_PROMPT_EXTRACTION = "system_prompt_extraction"
    HAZARDOUS_INSTRUCTIONS = "hazardous_instructions"
    ENCODED_WRAPPER = "encoded_wrapper"


def _empty_metadata() -> dict[str, str]:
    return {}


@dataclass(frozen=True)
class SafetyRequest:
    text: str
    phase: SafetyPhase
    surface: SafetySurface = SafetySurface.UNKNOWN
    locale_hint: str | None = None
    org_id: int | str | None = None
    metadata: dict[str, str] = field(default_factory=_empty_metadata)


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    action: SafetyAction
    reason: str = ""
    categories: tuple[SafetyCategory, ...] = ()
    confidence: float = 0.0
    provider: str = "deterministic"
    safe_replacement: str | None = None

    @classmethod
    def allow(cls) -> SafetyDecision:
        return cls(allowed=True, action=SafetyAction.ALLOW, reason="allowed", confidence=1.0)

    @classmethod
    def block(
        cls,
        *,
        reason: str,
        categories: tuple[SafetyCategory, ...],
        confidence: float = 1.0,
        safe_replacement: str | None = None,
    ) -> SafetyDecision:
        return cls(
            allowed=False,
            action=SafetyAction.BLOCK,
            reason=reason,
            categories=categories,
            confidence=confidence,
            safe_replacement=safe_replacement,
        )

    @classmethod
    def needs_provider(
        cls,
        *,
        reason: str,
        categories: tuple[SafetyCategory, ...],
        confidence: float = 0.5,
    ) -> SafetyDecision:
        return cls(
            allowed=False,
            action=SafetyAction.NEEDS_PROVIDER,
            reason=reason,
            categories=categories,
            confidence=confidence,
        )
