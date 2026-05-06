"""Stable reason-code registry for ingestion observability.

SPEC-INGEST-RECONCILE-001 Fix 3 / AC-9, AC-10, AC-11.

Two enums:

- ``FetchReasonCode`` — outcome of an HTTP fetch attempt by the crawler.
  Written per-URL into ``knowledge.crawl_jobs.fetch_outcomes`` (JSONB).
- ``PersistSkipReason`` — reason a fetched/extracted document was NOT
  persisted into ``knowledge.artifacts``. Aggregated per sync into
  ``connector.sync_runs.skip_reasons`` (JSONB ``{reason: count}``).

Both are ``str``-valued (``StrEnum``) so a Postgres CHECK constraint can
validate JSONB values against the same vocabulary the application writes.
A new reason MUST land in the enum AND in the matching CHECK constraint
before it can be persisted — mechanical guard against typo-introduced
silent reasons (rationale: SPEC §"Fix 3 — Stable reason-code registry").
"""

from __future__ import annotations

from enum import StrEnum

# @MX:ANCHOR — referenced by sync_engine, crawl4ai_client, alembic migrations,
#   ast-grep CI rules, and any future connector adapter that records skips.
#   Adding a new reason: append to enum, extend Postgres CHECK constraint
#   in the next alembic migration, document the meaning in the relevant
#   adapter. See SPEC-INGEST-RECONCILE-001.


class FetchReasonCode(StrEnum):
    """Per-URL outcome of an HTTP fetch via crawl4ai.

    Mapped from crawl4ai ``/crawl`` response (``result.success``,
    ``result.status_code``, ``result.error_message``). The classifier lives
    in :func:`knowledge_ingest.crawl4ai_client._classify_fetch_outcome`.

    Stable values — used as JSONB keys in dashboards and as CHECK constraint
    members in migrations. Renaming requires a coordinated migration.
    """

    SUCCESS = "success"
    HTTP_4XX = "http_4xx"
    HTTP_5XX = "http_5xx"
    TIMEOUT = "timeout"
    DNS_ERROR = "dns_error"
    CONNECTION_ERROR = "connection_error"
    AUTH_ERROR = "auth_error"
    PARSE_ERROR = "parse_error"
    RATE_LIMITED = "rate_limited"
    UNKNOWN_EXCEPTION = "unknown_exception"


class PersistSkipReason(StrEnum):
    """Reason a fetched/extracted document was not persisted as artifact.

    Aggregated per sync as ``{reason: count}`` JSONB on
    ``connector.sync_runs.skip_reasons``. Each adapter increments the
    appropriate counter from inside its existing flow — no contract change
    on ``BaseAdapter.list_documents`` (rationale: SPEC §"Not changing
    BaseAdapter.list_documents contract").
    """

    CONTENT_TOO_SHORT = "content_too_short"
    AUTH_WALL_DETECTED = "auth_wall_detected"
    DEDUPE_CONTENT_HASH_MATCH = "dedupe_content_hash_match"
    DEDUPE_RAW_HTML_HASH_MATCH = "dedupe_raw_html_hash_match"
    NON_TEXT_CONTENT = "non_text_content"
    EXCLUDED_BY_KB_CONFIG = "excluded_by_kb_config"
    TAXONOMY_CLASSIFY_FAILED = "taxonomy_classify_failed"


# Convenience sets — handy for migrations or runtime validation.
FETCH_REASON_VALUES: frozenset[str] = frozenset(c.value for c in FetchReasonCode)
PERSIST_SKIP_REASON_VALUES: frozenset[str] = frozenset(r.value for r in PersistSkipReason)
