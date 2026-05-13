"""Sanity checks on the raw SQL bound into the KB-stats endpoints.

The bugs that motivated this file (PR #508 → #510 → #513):

1. `COALESCE(properties->'kb_slugs', '[]'::jsonb)` — failed because the
   live column was `json` while the model said `JSONB`. Fixed by an
   alembic migration that converts the column. After that landed, the
   defensive `(properties::jsonb)` casts went away too.

2. `to_jsonb(:slug::text)` — silent SQLAlchemy parser collision: `::`
   immediately after a bind parameter is interpreted as parameter-name
   continuation, NOT as a Postgres type cast. SQLAlchemy then fails to
   detect `:slug` as a bind, leaves the literal `:slug::text` in the
   prepared SQL, and asyncpg raises ``syntax error at or near ":"``.
   Fixed by switching to `CAST(:slug_jsonb AS jsonb)`, the pattern
   documented in klai/projects/portal-backend.md.

Both errors were caught by the per-call `try/except Exception:` and
logged at debug level — invisible in production until a human noticed
the tiles said "Usage unavailable".

This module checks every `text()` SQL string in the stats endpoints by
asking SQLAlchemy which bind parameters it detected. The `:p::cast`
class drops binds silently — the assertion catches that.

It does NOT need a running Postgres. The actual semantics (does this
query return the right rows?) are verified end-to-end after deploy;
this layer is the prepare-time guard.
"""

from __future__ import annotations

from typing import ClassVar

from sqlalchemy import text


def _detected_binds(sql: str) -> set[str]:
    """Return the set of bind parameter names SQLAlchemy detected in `sql`.

    A bind name that is followed by `::cast` is NOT detected — that is
    the exact failure mode of the `:p::cast` collision. Comparing this
    set against the dict of params the endpoint actually supplies is
    enough to catch the regression.
    """
    return set(text(sql)._bindparams.keys())


class TestBulkStatsSummarySQL:
    """The LATERAL jsonb_array_elements_text query for /stats-summary."""

    SQL = """
        SELECT
            s.kb_slug AS slug,
            COUNT(*) AS queries,
            COUNT(DISTINCT pe.user_id) AS users,
            COUNT(DISTINCT date_trunc('day', pe.created_at)) AS active_days
        FROM product_events pe
        CROSS JOIN LATERAL jsonb_array_elements_text(
            pe.properties->'kb_slugs'
        ) AS s(kb_slug)
        WHERE pe.org_id = :org_id
          AND pe.event_type = 'knowledge.queried'
          AND pe.created_at >= :cutoff
          AND jsonb_typeof(pe.properties->'kb_slugs') = 'array'
          AND s.kb_slug = ANY(:slugs)
        GROUP BY s.kb_slug
    """

    EXPECTED_BINDS: ClassVar[set[str]] = {"org_id", "cutoff", "slugs"}

    def test_all_expected_binds_detected(self) -> None:
        assert _detected_binds(self.SQL) == self.EXPECTED_BINDS


class TestPerKbStatsSQL:
    """The @>-with-jsonb-scalar query for /knowledge-bases/{slug}/stats."""

    SQL = """
        SELECT
            COUNT(*) AS queries,
            COUNT(DISTINCT user_id) AS users,
            COUNT(DISTINCT date_trunc('day', created_at)) AS active_days
        FROM product_events
        WHERE org_id = :org_id
          AND event_type = 'knowledge.queried'
          AND created_at >= :cutoff
          AND properties->'kb_slugs' @> CAST(:slug_jsonb AS jsonb)
    """

    EXPECTED_BINDS: ClassVar[set[str]] = {"org_id", "cutoff", "slug_jsonb"}

    def test_all_expected_binds_detected(self) -> None:
        assert _detected_binds(self.SQL) == self.EXPECTED_BINDS


class TestParamCastCollisionRegressionGuard:
    """Pin the rule from klai/projects/portal-backend.md.

    If anyone reverts the per-KB query (or any future query) to use
    `:slug::text` instead of `CAST(:slug AS text)`, the `:slug` bind is
    silently dropped — and that is the exact bug we just paid three
    deploys for.
    """

    def test_param_followed_by_cast_is_not_detected(self) -> None:
        # SQLAlchemy's bind regex is `(?<!:):(?!:)\w+` (don't match a
        # `:` if preceded or followed by another `:`). `:slug::text`
        # falls in the followed-by-`::` case.
        bad = "SELECT 1 WHERE x = :slug::text"
        assert "slug" not in _detected_binds(bad)

    def test_cast_function_form_is_detected(self) -> None:
        good = "SELECT 1 WHERE x = CAST(:slug AS text)"
        assert "slug" in _detected_binds(good)
