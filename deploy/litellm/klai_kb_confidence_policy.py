"""Low-confidence KB evidence policy for LiteLLM retrieval context.

Environment-derived constants in this module are boot-time configuration:
production imports the LiteLLM hook once per process, and runtime env toggles
take effect on process restart.
"""

from __future__ import annotations

import os

from klai_citations import extract_salient_query_tokens

# English on purpose: every other instruction block in the prompt stack is
# English-wrapped (SPEC-RAG-MULTILINGUAL-CHAT-001 REQ-10). A Dutch guard here
# was the last strong language anchor before generation and pulled English
# questions into Dutch answers when the low-confidence band fired.
LOW_CONFIDENCE_INJECTION_TEXT = (
    "[Klai retrieval — low relevance]\n"
    "The retrieved KB material has a low relevance score for this "
    "question. Cite only what is literally in the chunks. Do NOT "
    "invent integration routes, product names, steps, amounts, or "
    "technical details that do not explicitly appear in the chunks. "
    "If the material does not fully cover the question, close with a "
    "clarifying question to the user — in the user's language — "
    "rather than giving a fabricated answer."
)
LOW_CONFIDENCE_OPEN_CONTEXT_TEXT = (
    "[Klai retrieval — low relevance in Open mode]\n"
    "The retrieved KB material has a low relevance score for this "
    "question. Treat the chunks as weak supplementary context. Open "
    "mode stays active: do not refuse solely because KB evidence is "
    "weak, tangential, or absent. Answer from general knowledge or "
    "visible user context when the question can be answered reliably "
    "that way. Present such parts explicitly as general knowledge or "
    "as derived from the user context, not as something that comes "
    "from the knowledge base. For organisation-specific facts, "
    "prices, routes, product names, steps, or source claims: do not "
    "invent them and say briefly that the knowledge base does not "
    "support that specific claim."
)
LOW_CONFIDENCE_INJECTION_DISABLED = (
    os.getenv("KNOWLEDGE_DISABLE_LOW_CONFIDENCE_INJECTION", "0") == "1"
)

def low_confidence_query_tokens(query: object) -> set[str]:
    if not isinstance(query, str):
        return set()
    return extract_salient_query_tokens(query)


def has_direct_evidence_for_query(query: object, chunks: list[dict]) -> bool:
    """Return whether low-scored retrieval still has literal answer evidence."""
    tokens = low_confidence_query_tokens(query)
    if not tokens:
        return False
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        chunk_tokens = extract_salient_query_tokens(
            " ".join(
                str(chunk.get(key) or "")
                for key in ("title", "heading_path", "source_label", "text", "content")
            )
        )
        if tokens & chunk_tokens:
            return True
    return False


def should_apply_low_confidence_injection(
    confidence_band: object,
    *,
    user_query: object,
    evidence_chunks: list[dict],
) -> bool:
    if confidence_band not in ("low", "unknown"):
        return False
    return not has_direct_evidence_for_query(user_query, evidence_chunks)
