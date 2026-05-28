"""Provider interface for optional external LLM safety classifiers."""

from __future__ import annotations

from typing import Protocol

from klai_llm_safety.models import SafetyDecision, SafetyRequest


class SafetyProvider(Protocol):
    """Classifier provider contract.

    Implementations must own their timeout/error handling and return an
    explicit SafetyDecision instead of raising on ordinary provider failures.
    """

    name: str

    async def check(self, request: SafetyRequest) -> SafetyDecision:
        """Classify one safety request."""
        ...
