"""
Domain-level persistence for the crawl wizard, keyed by (domain, org_id).

Two independent concerns share ``knowledge.crawl_domains``:

- CSS selector: the best known content selector for a domain, so repeat
  crawls don't need manual selector entry (SPEC-CRAWL-001 / R-2, R-3).
- Rate limit: a lowered requests/second value for a domain that previously
  hit a rate-limit or anti-bot block, so the NEXT crawl starts already
  paced down instead of hitting the same wall again (2026-08-17,
  intermedia.com + support.ascendcloud.com incident — see
  ``knowledge_ingest.adapters.crawler.run_crawl_job``), PLUS the additive
  recovery counters (``clean_streak``, ``last_congestion_at``) that let the
  rate limit climb back up once the domain has behaved for a while (block
  B follow-up — see ``knowledge_ingest.domain_rate_limit_control``).

A row may carry either field, both, or neither — ``css_selector`` and
``selector_source`` are nullable so a rate-limit-only row (no selector ever
recorded for that domain) is representable without inventing a placeholder
selector value.

SPEC-TI-003-FOLLOWUP-001 AC-1: helpers take an asyncpg.Connection from a
tenant_scoped_connection(org_id) block instead of acquiring a fresh pool
connection (which would not see the RLS GUC).
"""

from enum import StrEnum
from urllib.parse import urlparse

import asyncpg

from knowledge_ingest.domain_rate_limit_control import DomainRateLimitState

# Ports that are implied by the scheme and therefore carry no distinguishing
# information in the domain key — ``example.com:443`` (https) and
# ``example.com`` MUST collide onto the same rate-limit/selector row.
_DEFAULT_PORTS_BY_SCHEME = {"http": "80", "https": "443"}


class DomainRateLimitWriteKind(StrEnum):
    """Conflict policy for one persisted domain-rate transition."""

    CONGESTION = "congestion"
    RECOVERY = "recovery"
    DECAY = "decay"


def extract_domain(url: str) -> str:
    """Return a normalised domain key for ``url``, e.g. 'help.example.com'.

    Used as the lookup/storage key for ``knowledge.crawl_domains`` (selector
    + rate-limit persistence, see module docstring) — so two URLs that are
    the SAME site to a human MUST produce the SAME key, or they silently
    become two independent rate limits / selector rows.

    Normalisation (all four independently observed as real-world variance
    in crawl targets, none handled by a bare ``urlparse(url).netloc``):

    - lowercase (``Example.com`` == ``example.com``)
    - default port stripped when it matches the URL's scheme
      (``example.com:443`` over https == ``example.com``) — a NON-default
      port (``example.com:8080``) is kept, since that genuinely is a
      different origin for rate-limiting purposes
    - trailing dot stripped (``example.com.`` == ``example.com`` — a valid
      absolute-DNS-name suffix that is otherwise a distinct string)
    - IDNA-normalised for non-ASCII hostnames, so a Unicode domain and its
      punycode form (``münchen.example`` vs ``xn--mnchen-3ya.example``)
      collide onto the same key

    No data migration ships with this change: production
    ``knowledge.crawl_domains`` rows were audited and are already lowercase
    with no port suffix, so a backfill would touch zero rows. See
    docs/pitfalls or the SPEC changelog for the RLS reason a migration-time
    UPDATE would be unsafe on this table regardless.
    """
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower().rstrip(".")
    if hostname:
        try:
            hostname = hostname.encode("idna").decode("ascii")
        except UnicodeError:
            # Not a valid IDNA hostname (e.g. already punycode, or contains
            # characters idna can't encode) — keep the lowercased form
            # rather than raising out of a pure key-derivation helper.
            pass
    port = parsed.port
    if port is None or _DEFAULT_PORTS_BY_SCHEME.get(parsed.scheme) == str(port):
        return hostname
    return f"{hostname}:{port}"


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


async def get_domain_rate_limit_state(
    conn: asyncpg.Connection, domain: str, org_id: str
) -> DomainRateLimitState:
    """Return the full AIMD state for (domain, org_id) — see
    ``knowledge_ingest.domain_rate_limit_control.DomainRateLimitState``.

    No row for this (domain, org_id) is the same as a healthy domain with
    no override: ``rate_limit=None, clean_streak=0, last_congestion_at=None``.
    Callers fall back to their own default rate_limit in that case.
    """
    row = await conn.fetchrow(
        """
        SELECT rate_limit, clean_streak, last_congestion_at
        FROM knowledge.crawl_domains
        WHERE domain = $1 AND org_id = $2
        """,
        domain,
        org_id,
    )
    if row is None:
        return DomainRateLimitState(rate_limit=None, clean_streak=0, last_congestion_at=None)
    return DomainRateLimitState(
        rate_limit=row["rate_limit"],
        clean_streak=row["clean_streak"],
        last_congestion_at=row["last_congestion_at"],
    )


async def save_domain_rate_limit_state(
    conn: asyncpg.Connection,
    domain: str,
    org_id: str,
    *,
    expected_state: DomainRateLimitState,
    state: DomainRateLimitState,
    kind: DomainRateLimitWriteKind,
    default_rate_limit: float,
) -> bool:
    """Atomically persist one AIMD transition without losing congestion.

    Recovery and decay are compare-and-swap writes over the complete state;
    if another crawl changed any field since ``expected_state`` was read,
    they are conservatively deferred. Congestion is monotonic: on conflict
    the statement keeps the lowest current/proposed rate, resets the clean
    streak, and preserves the newest congestion timestamp. Two congestion
    proposals computed from the same state therefore lower once rather than
    accidentally halving twice.

    Does not touch css_selector/selector_source (a separate concern — see
    module docstring); a fresh insert leaves them NULL, an update to an
    existing row leaves them untouched.

    Callers MUST only invoke this when
    ``domain_rate_limit_control.compute_domain_rate_limit_update`` returned
    a non-``None`` state — a healthy domain with no stored override has
    nothing to persist, and calling this unconditionally would create a
    row for every domain ever crawled.
    """
    row = await conn.fetchrow(
        """
        INSERT INTO knowledge.crawl_domains AS crawl_domains
            (domain, org_id, rate_limit, clean_streak, last_congestion_at, created_at, updated_at)
        VALUES ($1, $2, $3, $4, $5, now(), now())
        ON CONFLICT (domain, org_id) DO UPDATE
            SET rate_limit = CASE
                    WHEN $10::boolean THEN LEAST(
                        COALESCE(crawl_domains.rate_limit, $9),
                        EXCLUDED.rate_limit
                    )
                    ELSE EXCLUDED.rate_limit
                END,
                clean_streak = CASE
                    WHEN $10::boolean THEN 0
                    ELSE EXCLUDED.clean_streak
                END,
                last_congestion_at = CASE
                    WHEN $10::boolean THEN CASE
                        WHEN crawl_domains.last_congestion_at IS NULL
                            THEN EXCLUDED.last_congestion_at
                        WHEN EXCLUDED.last_congestion_at IS NULL
                            THEN crawl_domains.last_congestion_at
                        ELSE GREATEST(
                            crawl_domains.last_congestion_at,
                            EXCLUDED.last_congestion_at
                        )
                    END
                    ELSE EXCLUDED.last_congestion_at
                END,
                updated_at         = now()
        WHERE $10::boolean OR (
            crawl_domains.rate_limit IS NOT DISTINCT FROM $6
            AND crawl_domains.clean_streak = $7
            AND crawl_domains.last_congestion_at IS NOT DISTINCT FROM $8
        )
        RETURNING rate_limit, clean_streak, last_congestion_at
        """,
        domain,
        org_id,
        state.rate_limit,
        state.clean_streak,
        state.last_congestion_at,
        expected_state.rate_limit,
        expected_state.clean_streak,
        expected_state.last_congestion_at,
        default_rate_limit,
        kind is DomainRateLimitWriteKind.CONGESTION,
    )
    return row is not None
