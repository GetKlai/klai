"""
Domain-level CSS selector persistence for the crawl wizard.

Stores and retrieves the best known CSS selector per (domain, org_id) pair so
that repeat crawls of the same domain do not need manual selector entry.

SPEC-CRAWL-001 / R-2, R-3
"""
from urllib.parse import urlparse

from knowledge_ingest.db import get_pool


def extract_domain(url: str) -> str:
    """Return the netloc (hostname) of the given URL, e.g. 'help.example.com'."""
    return urlparse(url).netloc


async def get_domain_selector(domain: str, org_id: str) -> tuple[str, str] | None:
    """Return (css_selector, selector_source) for the given domain+org, or None.

    selector_source is 'user' or 'ai'.
    """
    pool = await get_pool()
    row = await pool.fetchrow(
        """
        SELECT css_selector, selector_source
        FROM knowledge.crawl_domains
        WHERE domain = $1 AND org_id = $2
        """,
        domain,
        org_id,
    )
    if row is None:
        return None
    return row["css_selector"], row["selector_source"]


async def upsert_domain_selector(
    domain: str,
    org_id: str,
    css_selector: str,
    selector_source: str,
) -> None:
    """Persist or overwrite the CSS selector for (domain, org_id).

    selector_source must be 'user' or 'ai'.
    A user selector always overwrites an AI selector (enforced by caller — no
    special logic needed here since the caller only calls this when appropriate).
    """
    pool = await get_pool()
    await pool.execute(
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
