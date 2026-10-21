"""Test helpers for reloading the LiteLLM PII modules with fresh env constants."""

from __future__ import annotations

import sys


KLAI_KB_MODULES = (
    "klai_kb_request_context",
    "klai_pii_observe",
    "klai_pii_entities",
    "klai_pii_map_store",
    "klai_pii_text_masking",
    "klai_pii_org_policy",
    "klai_pii_enforce",
    "klai_pii_restore_eval",
)


def reset_klai_kb_modules() -> None:
    for module_name in KLAI_KB_MODULES:
        sys.modules.pop(module_name, None)
