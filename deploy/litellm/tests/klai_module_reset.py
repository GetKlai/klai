"""Test helpers for reloading LiteLLM KB modules with fresh env constants."""

from __future__ import annotations

import sys


KLAI_KB_MODULES = (
    "klai_knowledge",
    "klai_kb_answer_policy",
    "klai_kb_citation_render",
    "klai_kb_confidence_policy",
    "klai_kb_context_prompt",
    "klai_kb_query_rewrite",
    "klai_kb_render_policy",
    "klai_kb_request_context",
    "klai_kb_safety_filter",
    "klai_kb_scope_policy",
    "klai_kb_system_prompt",
    "klai_kb_traceability",
    "klai_kb_urls",
    "klai_litellm_response",
)


def reset_klai_kb_modules() -> None:
    for module_name in KLAI_KB_MODULES:
        sys.modules.pop(module_name, None)
