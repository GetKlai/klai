"""LLM-safety gate adapter for the Klai LiteLLM KB-chat hook.

Wraps ``klai_llm_safety``'s ``check_text`` / ``refusal_message`` for the
LiteLLM path: mode toggles, chunk-text flattening, the metadata-recording
check, the localized refusal text, and the ``mock_response`` short-circuit
that makes LiteLLM synthesise a normal assistant refusal instead of an
HTTP 400.

Extracted from ``klai_knowledge.py`` as a cohesive single-responsibility
cluster. ``LLM_SAFETY_LITELLM_MODE`` is read from the environment at import
(identical timing to the previous in-module constant); the module is
registered in ``tests/klai_module_reset.KLAI_KB_MODULES`` so reloads pick up a
new mode. ``klai_knowledge`` re-imports every public name (aliased to the
``_``-prefixed local names the hook body uses), so call sites are unchanged.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from klai_llm_safety import (
    SafetyDecision,
    SafetyPhase,
    SafetyRequest,
    SafetySurface,
    check_text,
    refusal_message,
)

logger = logging.getLogger(__name__)

LLM_SAFETY_LITELLM_MODE = (
    os.getenv("LLM_SAFETY_LITELLM_MODE", "enforce").strip().lower()
)


def llm_safety_enabled() -> bool:
    return LLM_SAFETY_LITELLM_MODE not in {"", "off", "disabled", "0", "false"}


def llm_safety_enforces() -> bool:
    return LLM_SAFETY_LITELLM_MODE in {"enforce", "block", "on", "true", "1"}


def chunk_safety_text(chunk: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ("title", "heading_path", "source_label", "text"):
        value = chunk.get(key)
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, list):
            values.extend(item for item in value if isinstance(item, str))
    return "\n".join(values)


def check_llm_safety(
    *,
    phase: SafetyPhase,
    text: str,
    query: str,
    org_id: object,
    user_id: object,
    metadata: dict[str, Any],
    chunk_id: object | None = None,
) -> SafetyDecision | None:
    if not llm_safety_enabled() or not text:
        return None
    decision = check_text(
        SafetyRequest(
            text=text,
            phase=phase,
            surface=SafetySurface.LIBRECHAT,
            locale_hint=query,
            org_id=str(org_id) if org_id is not None else None,
        )
    )
    metadata.setdefault("_klai_safety", []).append(
        {
            "mode": LLM_SAFETY_LITELLM_MODE,
            "phase": phase.value,
            "allowed": decision.allowed,
            "reason": decision.reason,
            "categories": [category.value for category in decision.categories],
            "chunk_id": chunk_id,
        }
    )
    if decision.allowed:
        return decision
    logger.warning(
        "llm_safety_litellm_decision mode=%s phase=%s org_id=%s user_id=%s reason=%s categories=%s chunk_id=%s",
        LLM_SAFETY_LITELLM_MODE,
        phase.value,
        org_id,
        user_id,
        decision.reason,
        ",".join(category.value for category in decision.categories),
        chunk_id,
    )
    return decision


def llm_safety_refusal_text(query: str, decision: SafetyDecision | None) -> str:
    reason = decision.reason if decision is not None else "safety_block"
    return refusal_message(query, reason)


def llm_safety_short_circuit(
    data: dict[str, Any],
    *,
    query: str,
    decision: SafetyDecision | None,
) -> dict[str, Any]:
    """Return ``data`` mutated so LiteLLM skips the provider and emits a refusal.

    LiteLLM honours ``mock_response`` by short-circuiting the upstream LLM
    call and synthesising a normal assistant ``ModelResponse`` (works for both
    streaming and non-streaming). This keeps the refusal surface as a regular
    chat turn instead of a 400 error.
    """
    data["mock_response"] = llm_safety_refusal_text(query, decision)
    return data
