"""Answer-policy and prompt-foundation contract for path-A KB chat.

This module is deliberately small and dependency-light compared with
``klai_knowledge.py``.  It owns the decisions that must stay stable across the
retrieval path:

* Strict/Open mode is a KB answer policy, not a renderer decision.
* User-provided content is independently usable in both modes.
* The ``_klai_kb_meta`` shape is identical for every retrieval branch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from klai_chat_prompts import (
    GENERAL_CHAT_SYSTEM_PROMPT,
    GROUNDED_CHAT_SYSTEM_PROMPT,
    META_CHAT_SYSTEM_PROMPT,
    OPEN_KB_CHAT_SYSTEM_PROMPT,
)
from klai_context import (
    HISTORY_BUDGET_CONTEXT_PLACEHOLDER as _HISTORY_BUDGET_CONTEXT_PLACEHOLDER,
    STALE_ATTACHMENT_CONTEXT_PLACEHOLDER as _STALE_ATTACHMENT_CONTEXT_PLACEHOLDER,
    STALE_LIBRECHAT_UPLOAD_PREFIX as _STALE_LIBRECHAT_UPLOAD_PREFIX,
)

KB_ANSWER_POLICY_STATES = (
    "retrieval_failure",
    "gate_bypassed",
    "missing_evidence_pack",
    "zero_chunks",
    "chunks_present",
)
KB_ANSWER_POLICY_SUPPRESS_CITATION_STATES = frozenset({
    "zero_chunks",
    "chunks_present",
})

USER_ATTACHMENT_PART_TYPES = frozenset({
    "file",
    "image",
    "image_url",
    "input_file",
    "input_image",
})
USER_PROVIDED_CONTENT_QUERY_RE = re.compile(
    r"\b(afbeelding|bijlage|foto|geupload(?:e|de)?|image|pasted|plak(?:te)?|"
    r"screenshot|upload(?:ed)?)\b"
    r"|"
    r"\b(deze|dit|onderstaande|bovenstaande|meegegeven|geplakte)\s+"
    r"(bestand|document|file|tekst)\b"
    r"|"
    r"\b(wat\s+(zei|schreef)\s+ik|wat\s+staat\s+(hierboven|daarboven)|"
    r"(vorige|eerdere)\s+(bericht|vraag)|dit\s+gesprek|deze\s+"
    r"(chat|conversatie)|chatgeschiedenis|conversation\s+history)\b",
    re.IGNORECASE,
)
OMITTED_USER_CONTENT_PLACEHOLDERS = frozenset({
    _HISTORY_BUDGET_CONTEXT_PLACEHOLDER,
    _STALE_ATTACHMENT_CONTEXT_PLACEHOLDER,
})

# A user-provided attachment is the user's OWN input, not a knowledge-base
# source. It must always be usable standalone content, in every mode and on
# every branch: a screenshot held up against the KB is a legitimate Strict use
# case. Strict/Open governs KB-grounding and general-knowledge fallback, NOT
# whether the model may look at what the user attached. Paths B/C never carry
# user attachments, so this deliberately stays out of the shared
# ``klai_chat_prompts`` foundation constants.
USER_PROVIDED_CONTENT_SCOPE = (
    "[User-provided content]\n"
    "Any images, screenshots, files, or text the user attached or pasted in "
    "this conversation, and the visible conversation itself, are the user's "
    "own input. Always inspect and use them to understand and answer the "
    "request. This is independent of Strict/Open mode and of whether the "
    "knowledge base returned results: even in Strict mode, and even when the "
    "knowledge base has zero or weak results, you may read and reason about "
    "what the user gave you. In Strict mode this permission only covers "
    "directly observable or user-provided information: do not add general-world "
    "explanations, organization-specific facts, prices, routes, product names, "
    "steps, or source claims unless the knowledge-base evidence below supports "
    "them. They are NOT knowledge-base sources — never cite them as numbered "
    "sources and never present their contents as knowledge-base facts. "
    "Strict/Open only controls how you use the knowledge base and whether you "
    "may add general knowledge; it never blocks the user's own attachments or "
    "visible conversation."
)


def has_user_provided_content_context(messages: list[dict], query: object) -> bool:
    """Return whether the current turn can be answered from user-owned content.

    Ordinary chat text is intentionally not enough: treating every latest user
    message as "user-provided content" would make Strict+zero-results pass
    through general-knowledge answers. We only unlock the post-call exception
    for concrete attachment/file/image shapes or queries that explicitly ask
    about pasted/attached content.
    """
    if isinstance(query, str) and USER_PROVIDED_CONTENT_QUERY_RE.search(query):
        return True

    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            stripped = content.lstrip()
            if stripped in OMITTED_USER_CONTENT_PLACEHOLDERS:
                continue
            if stripped.startswith(_STALE_LIBRECHAT_UPLOAD_PREFIX):
                return True
            continue
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type in USER_ATTACHMENT_PART_TYPES:
                return True
            if (
                part_type == "text"
                and isinstance(part.get("text"), str)
                and part["text"].lstrip().startswith(_STALE_LIBRECHAT_UPLOAD_PREFIX)
            ):
                return True
    return False


@dataclass(frozen=True)
class KbAnswerPolicy:
    """Single source of truth for prompt/post-call answer policy flags."""

    state: str
    kb_narrow: bool
    user_provided_content_context: bool
    low_confidence_inject: bool = False

    @property
    def mode(self) -> str:
        return "strict" if self.kb_narrow else "open"

    @property
    def allow_uncited_user_content(self) -> bool:
        return self.user_provided_content_context

    @property
    def suppress_kb_citations(self) -> bool:
        return (
            self.user_provided_content_context
            and self.low_confidence_inject
            and self.state in KB_ANSWER_POLICY_SUPPRESS_CITATION_STATES
        )

    def metadata(self) -> dict[str, bool | str]:
        return {
            "answer_policy_state": self.state,
            "answer_policy_mode": self.mode,
            "user_provided_content_context": self.user_provided_content_context,
            "low_confidence_inject": self.low_confidence_inject,
            "allow_uncited_user_content": self.allow_uncited_user_content,
            "suppress_kb_citations": self.suppress_kb_citations,
        }

    def to_kb_meta(
        self,
        *,
        org_id: object,
        user_id: object,
        retrieval_ms: int,
        user_query: object = None,
        chunks_injected: int = 0,
        chunk_ids: list | None = None,
        allowed_source_urls: list | None = None,
        allowed_image_urls: list | None = None,
        citation_source_urls: dict | None = None,
        citation_chunks: list | None = None,
        trusted_sources: list | None = None,
        evidence_pack: dict | None = None,
        citable_sources_count: int = 0,
        confidence_band: object = None,
        no_citable_sources: bool = False,
        no_citable_reason: object = None,
        no_citable_message: object = None,
        original_stream: object = None,
        render_mode: object = None,
        gate_bypassed: bool = False,
        retrieval_failure: object = False,
    ) -> dict[str, Any]:
        """Build the COMPLETE ``_klai_kb_meta`` dict for any branch."""
        return {
            "org_id": org_id,
            "user_id": user_id,
            "user_query": user_query,
            "kb_narrow": self.kb_narrow,
            "chunks_injected": chunks_injected,
            "chunk_ids": chunk_ids if chunk_ids is not None else [],
            "allowed_source_urls": allowed_source_urls if allowed_source_urls is not None else [],
            "allowed_image_urls": allowed_image_urls if allowed_image_urls is not None else [],
            "citation_source_urls": citation_source_urls if citation_source_urls is not None else {},
            "citation_chunks": citation_chunks if citation_chunks is not None else [],
            "trusted_sources": trusted_sources if trusted_sources is not None else [],
            "evidence_pack": evidence_pack,
            "citable_sources_count": citable_sources_count,
            "confidence_band": confidence_band,
            "no_citable_sources": no_citable_sources,
            "no_citable_reason": no_citable_reason,
            "no_citable_message": no_citable_message,
            "original_stream": original_stream,
            "render_mode": render_mode,
            "retrieval_ms": retrieval_ms,
            "gate_bypassed": gate_bypassed,
            "retrieval_failure": retrieval_failure,
            **self.metadata(),
        }


def compose_libre_chat_prefix(*blocks: str) -> str:
    """Compose the system-prompt prefix for Strict/grounded path-A KB chat.

    SPEC-RAG-MULTILINGUAL-CHAT-001 Phase 4 (REQ-10). Leads with
    ``GROUNDED_CHAT_SYSTEM_PROMPT`` so language detection / 3-guard switch
    semantics apply, AND so the model defaults to KB-grounded behaviour on
    every branch where a KB scope is in play, even when retrieval returned zero
    chunks or failed loud. ``USER_PROVIDED_CONTENT_SCOPE`` follows the
    foundation so user attachments stay usable on every Strict branch.
    """
    return "\n\n".join(
        b
        for b in (GROUNDED_CHAT_SYSTEM_PROMPT, USER_PROVIDED_CONTENT_SCOPE, *blocks)
        if b
    )


def compose_open_kb_chat_prefix(*blocks: str) -> str:
    """Compose the system-prompt prefix for Open mode with KB scope."""
    return "\n\n".join(
        b
        for b in (OPEN_KB_CHAT_SYSTEM_PROMPT, USER_PROVIDED_CONTENT_SCOPE, *blocks)
        if b
    )


def compose_kb_mode_chat_prefix(kb_narrow: bool, *blocks: str) -> str:
    if kb_narrow:
        return compose_libre_chat_prefix(*blocks)
    return compose_open_kb_chat_prefix(*blocks)


def compose_general_chat_prefix(*blocks: str) -> str:
    """Compose the path-A prefix when the user selected no KB scopes.

    This is the "Algemene AI" branch the chat-config dropdown surfaces:
    general-assistant rules, no model-generated source lists, and still with
    user-provided content available because attachments are independent of KB
    scope.
    """
    return "\n\n".join(
        b
        for b in (GENERAL_CHAT_SYSTEM_PROMPT, USER_PROVIDED_CONTENT_SCOPE, *blocks)
        if b
    )


def compose_meta_chat_prefix(*blocks: str) -> str:
    """Compose the path-A prefix for meta questions about Klai itself.

    Meta questions deliberately do not include ``USER_PROVIDED_CONTENT_SCOPE``:
    this branch describes Klai/capabilities and should not pull KB or user
    attachment content into an answer when the user did not ask about content.
    """
    return "\n\n".join(b for b in (META_CHAT_SYSTEM_PROMPT, *blocks) if b)


def strict_no_kb_scope_notice(reason: str) -> str:
    """Strict-mode notice for branches where retrieval cannot produce KB chunks."""
    return (
        "[Klai Knowledge Base — no knowledge base evidence is available for "
        "this request. The user selected Strict mode, so do not answer from "
        "general knowledge. Tell the user in their detected language that you "
        "cannot answer reliably from their knowledge sources for this request "
        f"(technical reason: {reason}).]\n"
    )
