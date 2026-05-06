"""Anonymous-crawl login-wall detector via near-duplicate clustering.

SPEC-INGEST-LOGIN-WALL-DETECT-002 — supersedes v1's substring matching.

A login wall is a page where the source CMS serves a TEMPLATED STUB to
anonymous visitors INSTEAD of the article's real content. The structural fact
"the same content body is served across many URLs" is the wall; the phrasing
("you must log in...") is incidental — a side-effect of templating, not the
cause. v2 detects the cause via SimHash cluster-membership lookup against
sibling pages in the same ``(org_id, kb_slug)``.

Algorithm:

1. Compute the page's 64-bit SimHash from ``fit_markdown or markdown`` using
   ``content_fingerprint.compute_simhash``.
2. Query existing SimHashes in the same ``(org_id, kb_slug)``, excluding the
   page's own row (matched by ``url``). Rows with NULL ``content_simhash``
   (legacy, pre-backfill) are returned as-is and filtered in Python.
3. Count siblings within Hamming distance 3 of the target.
4. If count >= ``cluster_min`` (default 5), flag as wall.

Fail-open behaviour: when ``org_id``, ``kb_slug``, or ``conn`` are missing
(e.g., a v1 caller that has not yet been wired through Phase C/D), the
detector emits a single WARN log and returns ``None``. Ingest is never
blocked by detector misconfiguration.

Async signature: cluster lookup requires a DB query, so the function is
``async``. v1 callers must migrate to ``await detect_anonymous_auth_wall(...)``;
the legacy positional args (markdown, fit_markdown, url) remain compatible.

Why no phrase fallback? See ``spec.md`` and ``research.md``: production
canary on voys/support proved that phrase matching produces 2.6% FP rate on
legitimate Dutch and English content. The cluster mechanism targets the
template structure that is the actual wall, regardless of language or CMS.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from knowledge_ingest.utils.content_fingerprint import (
    compute_simhash,
    hamming_distance,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_CLUSTER_MIN",
    "DEFAULT_HAMMING_MAX",
    "AuthWallSignal",
    "detect_anonymous_auth_wall",
]


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthWallSignal:
    """Outcome of a positive cluster-detection.

    Attributes:
        pattern: ``"template_cluster"`` in v2. Was ``canonical_phrase_*`` in
            v1; tests pin the new value so a regression is caught.
        evidence: Single-element tuple of the form
            ``("cluster_size={N} hamming<={M}",)`` used for diagnostic logs.
        confidence: Always ``0.9`` for cluster matches. v1's tiered confidence
            (0.95 for canonical phrase, 0.7 for weak signal) is gone — v2 has
            one signal type.
    """

    pattern: str
    evidence: tuple[str, ...] = field(default_factory=tuple)
    confidence: float = 0.0


# ---------------------------------------------------------------------------
# Tunables
# ---------------------------------------------------------------------------

# REQ-02 default. Overridable per-call via ``cluster_min`` or per-deployment
# via the ``KLAI_INGEST_TEMPLATE_CLUSTER_MIN`` env var (read by callers in
# ``crawler.py`` / ``backfill_tasks.py``, NOT in this module — keep the
# detector pure).
DEFAULT_CLUSTER_MIN = 5

# REQ-02 fixed. Hamming 3 corresponds to ~95% content overlap on 64-bit
# SimHash. Lowering risks missing real walls; raising risks merging legitimate
# pages into wall clusters. Adjusting this is a SPEC revision and requires
# re-validating all production fixtures, so it is not env-tunable.
DEFAULT_HAMMING_MAX = 3


# ---------------------------------------------------------------------------
# SQL — single query per detection
# ---------------------------------------------------------------------------

# Phase A migration created the partial index
# ``idx_crawled_pages_simhash_org_kb`` on
# ``(org_id, kb_slug, content_simhash) WHERE content_simhash IS NOT NULL``,
# which makes this lookup a sub-millisecond index scan within a single KB.
# The ``url <>`` clause excludes the page's own row in the backfill scenario
# (where the row was inserted on a previous crawl); for the ingest path the
# row does not yet exist so the clause is a no-op.
_CLUSTER_LOOKUP_SQL = (
    "SELECT content_simhash FROM knowledge.crawled_pages "
    "WHERE org_id = $1 "
    "AND kb_slug = $2 "
    "AND content_simhash IS NOT NULL "
    "AND url <> $3"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def detect_anonymous_auth_wall(
    markdown: str,
    *,
    fit_markdown: str | None = None,
    url: str | None = None,
    org_id: str | None = None,
    kb_slug: str | None = None,
    conn: Any | None = None,
    cluster_min: int = DEFAULT_CLUSTER_MIN,
    hamming_max: int = DEFAULT_HAMMING_MAX,
    target_simhash: int | None = None,
) -> AuthWallSignal | None:
    """Return a signal if ``markdown`` clusters with sibling stubs in the KB.

    The function is ``async`` because cluster detection requires a DB query.
    Callers that lack DB access (legacy v1 sites pre-migration) get ``None``
    via fail-open; their warning log surfaces the misconfiguration without
    blocking the ingest pipeline.

    Args:
        markdown: ``raw_markdown`` from the crawl result.
        fit_markdown: ``crawl4ai.fit_markdown`` (chrome-stripped). Preferred
            content view because it isolates article body from boilerplate.
        url: Page URL. Used to exclude the page's own row from the cluster
            count (see ``_CLUSTER_LOOKUP_SQL``).
        org_id: Tenant ID (REQ-09 isolation).
        kb_slug: KB slug (REQ-09 isolation).
        conn: ``asyncpg.Connection``-compatible. Typed as ``Any`` so unit
            tests can inject a stub without an asyncpg dep.
        cluster_min: Minimum sibling-cluster size to flag. Default 5.
        hamming_max: Maximum Hamming distance to consider a sibling. Fixed
            at 3 by SPEC.
        target_simhash: Pre-computed SimHash of the page. When provided, the
            detector skips ``compute_simhash(text)`` — the crawler computes
            the hash once in ``_ingest_crawl_result`` and reuses it for both
            detection and post-ingest storage.
    """
    text = fit_markdown or markdown
    if not text:
        return None

    if not (org_id and kb_slug and conn is not None):
        # Fail-open. Logged so an operator can spot the misconfiguration in
        # VictoriaLogs (``event="auth_wall_detector_db_missing"``) without
        # blocking the ingest pipe due to an upstream wiring bug.
        logger.warning(
            "auth_wall_detector_db_missing — fail-open, no cluster lookup",
            extra={"event": "auth_wall_detector_db_missing"},
        )
        return None

    target = target_simhash if target_simhash is not None else compute_simhash(text)
    if target == 0:
        # Empty/whitespace-only normalised content cannot meaningfully
        # cluster. ``compute_simhash`` returns 0 for that case; bailing here
        # avoids wasting a DB roundtrip on degenerate input.
        return None

    page_url = url or ""
    rows = await conn.fetch(_CLUSTER_LOOKUP_SQL, org_id, kb_slug, page_url)

    cluster_size = 0
    for row in rows:
        sibling = row["content_simhash"]
        if sibling is None:
            # Legacy rows pre-Phase-A backfill: no fingerprint yet, skip.
            continue
        if hamming_distance(target, sibling) <= hamming_max:
            cluster_size += 1

    if cluster_size < cluster_min:
        return None

    return AuthWallSignal(
        pattern="template_cluster",
        evidence=(f"cluster_size={cluster_size} hamming<={hamming_max}",),
        confidence=0.9,
    )
