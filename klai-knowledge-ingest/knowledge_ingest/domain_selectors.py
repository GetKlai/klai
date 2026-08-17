"""
Domain-level persistence for the crawl wizard, keyed by (domain, org_id).

Two independent concerns share ``knowledge.crawl_domains``:

- CSS selector: the best known content selector for a domain, so repeat
  crawls don't need manual selector entry (SPEC-CRAWL-001 / R-2, R-3).
- Rate limit: a lowered requests/second value for a domain that previously
  hit a rate-limit or anti-bot block, so the NEXT crawl starts already
  paced down instead of hitting the same wall again (2026-08-17,
  intermedia.com + support.ascendcloud.com incident — see
  ``knowledge_ingest.adapters.crawler.run_crawl_job``).

A row may carry either field, both, or neither — ``css_selector`` and
``selector_source`` are nullable so a rate-limit-only row (no selector ever
recorded for that domain) is representable without inventing a placeholder
selector value.

SPEC-TI-003-FOLLOWUP-001 AC-1: helpers take an asyncpg.Connection from a
tenant_scoped_connection(org_id) block instead of acquiring a fresh pool
connection (which would not see the RLS GUC).
"""

from urllib.parse import urlparse

import asyncpg


def extract_domain(url: str) -> str:
    """Return the netloc (hostname) of the given URL, e.g. 'help.example.com'."""
    return urlparse(url).netloc


async def get_domain_selector(
    conn: asyncpg.Connection, domain: str, org_id: str
) -> tuple[str, str] | None:
    """Return (css_selector, selector_source) for the given domain+org, or None.

    selector_source is 'user' or 'ai'. ``css_selector IS NOT NULL`` excludes
    a rate-limit-only row (see module docstring) so this keeps returning
    None for a domain that has never had a selector recorded, even after a
    rate-limit lowering wrote a row for it.
    """
    row = await conn.fetchrow(
        """
        SELECT css_selector, selector_source
        FROM knowledge.crawl_domains
        WHERE domain = $1 AND org_id = $2 AND css_selector IS NOT NULL
        """,
        domain,
        org_id,
    )
    if row is None:
        return None
    return row["css_selector"], row["selector_source"]


async def upsert_domain_selector(
    conn: asyncpg.Connection,
    domain: str,
    org_id: str,
    css_selector: str,
    selector_source: str,
) -> None:
    """Persist or overwrite the CSS selector for (domain, org_id).

    selector_source must be 'user' or 'ai'.
    A user selector always overwrites an AI selector (enforced by caller — no
    special logic needed here since the caller only calls this when appropriate).
    Does not touch ``rate_limit`` — a stored rate-limit override survives a
    selector update.
    """
    await conn.execute(
        """
        INSERT INTO knowledge.crawl_domains
            (domain, org_id, css_selector, selector_source, created_at, updated_at)
        VALUES ($1, $2, $3, $4, now(), now())
        ON CONFLICT (domain, org_id) DO UPDATE
            SET css_selector    = EXCLUDED.css_selector,
                selector_source = EXCLUDED.selector_source,
                updated_at      = now()
        """,
        domain,
        org_id,
        css_selector,
        selector_source,
    )


async def get_domain_rate_limit(conn: asyncpg.Connection, domain: str, org_id: str) -> float | None:
    """Return the stored lowered rate_limit (requests/second) for (domain,
    org_id), or None when no override has been recorded — callers fall back
    to their own default in that case.
    """
    row = await conn.fetchrow(
        """
        SELECT rate_limit
        FROM knowledge.crawl_domains
        WHERE domain = $1 AND org_id = $2
        """,
        domain,
        org_id,
    )
    if row is None:
        return None
    return row["rate_limit"]


async def lower_domain_rate_limit(
    conn: asyncpg.Connection,
    domain: str,
    org_id: str,
    rate_limit: float,
) -> None:
    """Persist a lowered rate_limit (requests/second) for (domain, org_id).

    Does not touch css_selector/selector_source (a separate concern — see
    module docstring); a fresh insert leaves them NULL, an update to an
    existing row leaves them untouched.
    """
    await conn.execute(
        """
        INSERT INTO knowledge.crawl_domains (domain, org_id, rate_limit, created_at, updated_at)
        VALUES ($1, $2, $3, now(), now())
        ON CONFLICT (domain, org_id) DO UPDATE
            SET rate_limit = EXCLUDED.rate_limit,
                updated_at = now()
        """,
        domain,
        org_id,
        rate_limit,
    )
