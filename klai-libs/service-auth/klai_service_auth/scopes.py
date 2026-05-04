"""Canonical scope-name constants for Klai inter-service authorization.

SPEC-SEC-SERVICE-AUTH-001 REQ-3 + Risks. Scope strings live in one place so
they cannot drift the way env-var names did across services.

Naming convention
-----------------

``klai:internal:<receiver-service>:<verb>``

* ``receiver-service`` is the service that gates access (the receiver).
* ``verb`` describes the action class. Use a short noun phrase, kebab-free.

Granting scopes to service accounts is done in Zitadel — see
``klai-infra/scripts/zitadel-create-service-account.py``.
"""

from __future__ import annotations

# --- retrieval-api scopes ---------------------------------------------------

RETRIEVAL_QUERY = "klai:internal:retrieval:query"
"""Granted to: svc-litellm, svc-research-api, svc-knowledge-mcp, svc-portal-api.
Permits: ``POST /retrieve``."""

# --- knowledge-ingest scopes ------------------------------------------------

INGEST_WRITE = "klai:internal:ingest:write"
"""Granted to: svc-portal-api, svc-knowledge-mcp.
Permits: write-side ingest endpoints (``POST /ingest/v1/...``)."""

INGEST_READ = "klai:internal:ingest:read"
"""Granted to: svc-knowledge-mcp.
Permits: read-side ingest endpoints (search, stats)."""

INGEST_CRAWL = "klai:internal:ingest:crawl"
"""Granted to: svc-klai-connector.
Permits: ``POST /ingest/v1/crawl/sync`` and crawl status/cancel endpoints."""

INGEST_PURGE = "klai:internal:ingest:purge"
"""Granted to: svc-portal-api.
Permits: ``POST /ingest/v1/connector/purge``."""

# --- portal-api callback scopes ---------------------------------------------

PORTAL_CALLBACK = "klai:internal:portal:callback"
"""Granted to: svc-knowledge-ingest, svc-klai-connector, svc-scribe, svc-mailer.
Permits: ``POST /api/internal/.../{action}`` callback endpoints (e.g. finalize-delete)."""

# --- klai-connector scopes --------------------------------------------------

CONNECTOR_INVOKE = "klai:internal:connector:invoke"
"""Granted to: svc-portal-api.
Permits: ``POST /api/v1/connectors/{id}/sync`` and similar invocation endpoints."""

# --- ALL_SCOPES (drift-detection invariant) ---------------------------------

ALL_SCOPES: list[str] = [
    RETRIEVAL_QUERY,
    INGEST_WRITE,
    INGEST_READ,
    INGEST_CRAWL,
    INGEST_PURGE,
    PORTAL_CALLBACK,
    CONNECTOR_INVOKE,
]
"""Every scope constant declared above. Tests pin that this list contains
every public uppercase string constant in the module — same drift guard as
``knowledge_ingest.queues.ALL_QUEUES``."""
