"""Stable reason-code registry — connector-side copy.

SPEC-INGEST-RECONCILE-001 Fix 3 / AC-9.

Mirrors :mod:`knowledge_ingest.reason_codes`. The two services are
independent deployables, so the enum is duplicated rather than imported
from a shared package. Drift between the two copies is caught by the
test ``klai-connector/tests/test_reason_codes_parity.py`` which asserts
identical value sets.

If you change values here, update the knowledge-ingest copy AND both
service alembic migrations whose CHECK constraints reference these
values:

  - ``klai-knowledge-ingest/.../0005_crawl_jobs_fetch_outcomes.py``
  - ``klai-connector/alembic/versions/009_sync_runs_skip_reasons.py``
"""

from __future__ import annotations

from enum import StrEnum


class FetchReasonCode(StrEnum):
    """See knowledge_ingest.reason_codes.FetchReasonCode."""

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
    """See knowledge_ingest.reason_codes.PersistSkipReason."""

    CONTENT_TOO_SHORT = "content_too_short"
    AUTH_WALL_DETECTED = "auth_wall_detected"
    DEDUPE_CONTENT_HASH_MATCH = "dedupe_content_hash_match"
    DEDUPE_RAW_HTML_HASH_MATCH = "dedupe_raw_html_hash_match"
    NON_TEXT_CONTENT = "non_text_content"
    EXCLUDED_BY_KB_CONFIG = "excluded_by_kb_config"
    TAXONOMY_CLASSIFY_FAILED = "taxonomy_classify_failed"


FETCH_REASON_VALUES: frozenset[str] = frozenset(c.value for c in FetchReasonCode)
PERSIST_SKIP_REASON_VALUES: frozenset[str] = frozenset(r.value for r in PersistSkipReason)
