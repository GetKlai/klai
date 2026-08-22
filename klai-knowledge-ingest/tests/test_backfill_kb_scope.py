"""A rebuild scoped to one language is expressed as a set of knowledge bases.

A tenant mixes languages per KB. Voys keeps its Dutch help centre in `support`
(help.voys.nl plus wiki.redcactus.cloud/nl) and an English vendor corpus in
`ascend`, which is 421 of its 959 episodes and 7,118 of its 18,031 edges. A
rebuild of "the Dutch content" that cannot exclude `ascend` is not a rebuild of
the Dutch content.
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest import backfill


def _conn() -> MagicMock:
    conn = MagicMock()
    conn.fetchval = AsyncMock(return_value=True)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock(return_value=None)
    conn.execute = AsyncMock(return_value=None)
    return conn


def _admin(conn):
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


@pytest.mark.asyncio
async def test_kb_slugs_restrict_the_query():
    conn = _conn()
    with (
        patch("knowledge_ingest.backfill.cross_org_admin_connection", return_value=_admin(conn)),
        patch("knowledge_ingest.backfill.AsyncQdrantClient"),
    ):
        await backfill.main(org_id="org-1", kb_slugs=["support", "sip"])

    sql, *values = conn.fetch.call_args[0]
    assert "kb_slug = ANY" in sql, "the run is not restricted to the requested knowledge bases"
    assert ["support", "sip"] in values


@pytest.mark.asyncio
async def test_no_kb_slugs_means_the_whole_tenant():
    """Omitting the flag must not silently narrow the run."""
    conn = _conn()
    with (
        patch("knowledge_ingest.backfill.cross_org_admin_connection", return_value=_admin(conn)),
        patch("knowledge_ingest.backfill.AsyncQdrantClient"),
    ):
        await backfill.main(org_id="org-1")

    sql = conn.fetch.call_args[0][0]
    # kb_slug is still SELECTed as a column; what must be absent is the filter.
    assert "kb_slug = ANY" not in sql


def test_the_flag_is_an_include_list_not_an_exclude_list():
    """An exclude list would silently pull in every KB added later."""
    source = inspect.getsource(backfill)
    assert '"--kb-slug"' in source
    assert 'action="append"' in source
    assert "--exclude-kb" not in source
