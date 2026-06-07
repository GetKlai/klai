"""System-prompt assembly helpers for the Klai LiteLLM KB-chat hook.

Two pure primitives extracted from ``klai_knowledge.py`` so the templates-only
and templates+KB paths share one insertion implementation and both stay
unit-testable without a running hook:

- ``prepend_system_prefix`` — insert a prefix into the existing system message
  (or add one) in place.
- ``build_template_instructions_block`` — render the active prompt-template
  list into the English-wrapped ``[Klai Templates …]`` system-prompt block.

Neither reads module/global state, so the move is a behavior-preserving lift:
``klai_knowledge`` re-imports both (aliased to their ``_``-prefixed names) so
the hook call sites and the test suite — which reach them as
``klai_knowledge._prepend_system_prefix`` / ``._build_template_instructions_block``
— are unchanged.
"""

from __future__ import annotations


def prepend_system_prefix(messages: list[dict], prefix: str) -> None:
    """Prepend `prefix` to the system message (or insert one if none exists).

    Mutates `messages` in-place. No-op when `prefix` is empty.

    Separated from the hook body so templates-only and templates+KB paths
    share the same insertion logic. Unit-testable without a running hook.
    """
    if not prefix:
        return
    sys_idx = next(
        (i for i, m in enumerate(messages) if m.get("role") == "system"), None
    )
    if sys_idx is not None:
        existing = messages[sys_idx].get("content", "")
        messages[sys_idx] = {
            "role": "system",
            "content": f"{prefix}\n\n{existing}" if existing else prefix,
        }
    else:
        messages.insert(0, {"role": "system", "content": prefix})


def build_template_instructions_block(instructions: list[dict]) -> str:
    """Render template list into a single system-prompt prefix block.

    Empty list returns "" — caller MUST check before prepending.
    Only the template's `name` and `text` appear in the block. Raw
    template text never goes to logs (REQ-TEMPLATES-HOOK-N2).
    """
    if not instructions:
        return ""
    # SPEC-RAG-MULTILINGUAL-CHAT-001 Phase 4 (REQ-10): English-prefixed wrapper.
    # The model receives English instructions but answers in the language
    # detected by GROUNDED_CHAT_SYSTEM_PROMPT (prepended above this block at
    # the call site). Template `name` and `text` themselves are tenant-defined
    # — they may already be in any language; we don't translate them.
    parts: list[str] = [
        "[Klai Templates — apply the following instructions to your answer. "
        "These instructions override the default answer format when they define "
        "a fixed structure, opening, wording, numbering, labels, fixed values, "
        "or whitespace. Preserve requested line breaks, blank lines, numbering, "
        "labels, and fixed values exactly. Do not collapse a fixed template into "
        "prose.]"
    ]
    for inst in instructions:
        name = inst.get("name") or "template"
        text = (inst.get("text") or "").strip()
        if not text:
            continue
        parts.append(f"[{name}]\n{text}")
    parts.append("[End templates]")
    return "\n\n".join(parts)
