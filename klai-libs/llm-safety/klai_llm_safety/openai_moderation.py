"""Placeholder adapter for OpenAI moderation.

The runtime implementation is intentionally deferred until provider choice and
data-processing constraints are approved. Keeping this module now fixes the
import path and lets service adapters depend on the shared provider contract.
"""

from __future__ import annotations

from klai_llm_safety.models import SafetyDecision, SafetyRequest


class OpenAIModerationProvider:
    name = "openai_moderation"

    async def check(self, request: SafetyRequest) -> SafetyDecision:
        _ = request
        return SafetyDecision.needs_provider(
            reason="openai_moderation_not_configured",
            categories=(),
            confidence=0.0,
        )
