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

__all__ = ["episode_name", "parse_episode_name", "personal_kb_slug"]


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


# ---------------------------------------------------------------------------
# Graphiti episode naming (SPEC-RAG-GRAPH-CITE-002)
# ---------------------------------------------------------------------------
#
# An episode used to be named after the artifact_id it was ingested from.
# That id identifies a VERSION, not a document: knowledge-ingest mints a fresh
# uuid4 on every ingest (pg_store.create_artifact) and marks the previous row
# superseded_by. Qdrant only holds chunks of the current version, so as soon as
# a page was re-ingested every graph edge extracted from it pointed at an id
# nothing could resolve any more — the fact stayed citable but its source could
# not be looked up, and the citation rendered as a truncated sentence.
#
# ``(org_id, kb_slug, path)`` is the identity ingest already dedups on, and
# ``kb_slug``/``path`` are both stamped on every Qdrant chunk. It survives
# re-ingest, so an episode named this way stays resolvable for the life of the
# document.
#
# org_id is deliberately absent: each tenant has its own FalkorDB database, so
# the name is already tenant-scoped by construction and repeating org_id here
# would invite a caller to trust the string instead of the database boundary.

_EPISODE_NAME_PREFIX = "doc"


def episode_name(kb_slug: str, path: str) -> str:
    """Return the stable Graphiti episode name for a document.

    Stamped by knowledge-ingest at episode creation and parsed back by
    retrieval-api when it resolves a graph fact to its source document.
    """
    return f"{_EPISODE_NAME_PREFIX}:{kb_slug}:{path}"


def parse_episode_name(name: str) -> tuple[str, str] | None:
    """Return ``(kb_slug, path)`` for a stable episode name, else None.

    None means the episode predates SPEC-RAG-GRAPH-CITE-002 and is still named
    after its artifact_id; callers fall back to resolving that instead. Split
    with maxsplit=2 because a path may legitimately contain colons while a
    kb_slug may not.
    """
    if not name:
        return None
    parts = name.split(":", 2)
    if len(parts) != 3 or parts[0] != _EPISODE_NAME_PREFIX:
        return None
    kb_slug, path = parts[1], parts[2]
    if not kb_slug or not path:
        return None
    return kb_slug, path
