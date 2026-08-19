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
    NON_CONTENT_LISTING_PAGE = "non_content_listing_page"
    NOT_FETCHED_BUDGET_EXHAUSTED = "not_fetched_budget_exhausted"
    NOT_FETCHED_DEPTH_LIMIT = "not_fetched_depth_limit"
    NOT_FETCHED_DISCOVERY_LIMIT = "not_fetched_discovery_limit"
    NOT_FETCHED_EXCLUDED = "not_fetched_excluded"
    NOT_FETCHED_DUPLICATE = "not_fetched_duplicate"
    # 2026-08-18 (bulk-path defects block A / A1): a chunk earlier in the
    # SAME bulk-fetch attempt (or the sequential-recovery loop) came back
    # RATE_LIMITED or BLOCKED_ANTI_BOT, so every URL still queued behind it
    # is deliberately never sent — not attempted, not observed, and
    # therefore honestly distinct from RATE_LIMITED itself. Keeping this
    # separate from RATE_LIMITED matters downstream: a domain-level
    # "how many times did this site actually reject us" count must not be
    # inflated by URLs we chose not to ask.
    NOT_FETCHED_RATE_LIMIT_STOP = "not_fetched_rate_limit_stop"
    UNKNOWN_EXCEPTION = "unknown_exception"
    # 2026-08-14: distinguishes a Cloudflare/anti-bot JS-challenge block from
    # a generic unknown_exception. Additive and safe without a migration —
    # ``crawl_jobs.fetch_outcomes`` has no per-element Postgres CHECK on
    # ``reason_code`` (see alembic/versions/0005_crawl_jobs_fetch_outcomes.py,
    # "Shape guard only"); validation of individual reason codes is
    # application-side only.
    BLOCKED_ANTI_BOT = "blocked_anti_bot"
    # 2026-08-19 (intermedia.com / support.ascendcloud.com "weigering"
    # incident): the site explicitly refuses automated access — a 403 (or
    # any status) carrying a concrete refusal marker crawl4ai's own
    # "blocked by anti-bot protection" detector does not surface (a
    # ``cf-mitigated`` header, or "Just a moment"/"Attention
    # Required"/"Access denied"/"challenge" in the body). Deliberately
    # separate from BLOCKED_ANTI_BOT (crawl4ai's OWN detector output):
    # BLOCKED_ANTI_BOT stays a congestion signal that lowers the domain's
    # stored rate_limit (knowledge_ingest.domain_rate_limit_control), but a
    # refusal is not fixed by going slower, so REFUSED is deliberately NOT
    # in that congestion set — see the module comment there.
    REFUSED = "refused"
    # 2026-08-19 (host circuit breaker, knowledge_ingest.host_circuit_
    # breaker): a URL abandoned because the breaker tripped (persistent
    # failure or repeated refusal) partway through a
    # ``_chunked_bulk_fetch`` call — distinct from
    # NOT_FETCHED_RATE_LIMIT_STOP so "how many times did the breaker
    # actually intervene" has an honest, uninflated answer.
    NOT_FETCHED_CIRCUIT_BREAKER_STOP = "not_fetched_circuit_breaker_stop"
    # 2026-08-19 (crawl-cancel): a URL abandoned because the operator
    # requested cancellation (``POST .../crawl/sync/{job_id}/cancel``) —
    # ``_chunked_bulk_fetch`` checks ``knowledge.crawl_jobs.cancel_requested``
    # between chunks and stops sending further chunks the moment it flips
    # true. Deliberately its own code, not NOT_FETCHED_RATE_LIMIT_STOP or
    # NOT_FETCHED_CIRCUIT_BREAKER_STOP: those both mean "the site told us to
    # stop"; this means "the operator told us to stop" — a different
    # operator-facing story and a different terminal status
    # (``crawl_jobs.status = 'cancelled'``, never a failure).
    NOT_FETCHED_CANCELLED = "not_fetched_cancelled"


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
