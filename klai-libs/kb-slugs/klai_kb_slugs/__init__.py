"""Canonical Knowledge Base slug helpers shared across Klai services.

Owned by SPEC-RAG-PERSONAL-SCOPE-001. Both ``klai-portal``'s provisioning
helpers and ``klai-retrieval-api``'s scope filter import this same function
so the slug template lives in exactly one place. A future rename of the
template moves in one file and both services pick up the change atomically.

The template ``personal-<zitadel_user_id>`` is also reproduced by chunk
metadata stamped at ingest time (``klai-knowledge-ingest``); the search
side never reads that stamp by string, it reads via this helper. If the
template ever changes, ingest+search must be redeployed together — the
shared lib is the coordination point.
"""

from __future__ import annotations

__all__ = ["personal_kb_slug"]


def personal_kb_slug(user_id: str) -> str:
    """Return the canonical Persoonlijk-KB slug for a user.

    The slug is deterministic and lives on:

    - ``portal_knowledge_bases.slug`` (DB row, created at provisioning)
    - Qdrant chunk payload ``kb_slug`` (stamped at ingest)
    - retrieval-api ``_scope_filter`` (matched at search)

    Pass the Zitadel user sub (``portal_users.zitadel_user_id``); the
    library does not validate the input shape because every call site
    already passes a verified identifier upstream.
    """
    return f"personal-{user_id}"
