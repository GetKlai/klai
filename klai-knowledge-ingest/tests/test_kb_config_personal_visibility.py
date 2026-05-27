"""Regression guard for the 2026-05-27 personal-KB visibility leak.

``kb_config.get_kb_visibility`` MUST return ``'private'`` for any kb_slug
matching the canonical personal-KB pattern ``personal-<user_id>``,
regardless of what ``knowledge.kb_config`` (or its absence) says.

Production state at the moment of the bug: 379 chunks in qdrant under
``kb_slug LIKE 'personal-%'`` were stamped ``visibility=internal`` (the
default for ``portal_knowledge_bases``). The retrieval-api scope=org/both
filter only excludes ``visibility=private`` chunks, so personal-KB chunks
were returned for other users' org-scope queries in the same org. The
``get_kb_visibility`` override below makes that drift impossible to recur
on new ingests.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from knowledge_ingest import kb_config


@pytest.fixture(autouse=True)
def _clear_cache():
    """kb_config keeps a module-level TTL cache; reset between tests so
    one test's writes don't influence another's read.
    """
    kb_config._cache.clear()
    yield
    kb_config._cache.clear()


class TestPersonalKbVisibilityOverride:
    @pytest.mark.asyncio
    async def test_personal_slug_returns_private_regardless_of_db(self):
        """Even when the DB reports ``visibility='internal'`` (the
        default for ``portal_knowledge_bases`` rows), the override
        forces ``'private'`` because the slug pattern is the structural
        truth.
        """
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"visibility": "internal"})

        visibility = await kb_config.get_kb_visibility(
            conn, "org-1", "personal-364818484816773122"
        )
        assert visibility == "private"
        # The DB lookup MUST NOT be the source of truth — the helper
        # should short-circuit before any fetchrow call.
        conn.fetchrow.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_personal_slug_returns_private_when_kb_config_missing(self):
        """When ``knowledge.kb_config`` has no row, the legacy default
        was ``'internal'``. The override forces ``'private'`` so a
        not-yet-configured personal KB is still safe from day one.
        """
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=None)

        visibility = await kb_config.get_kb_visibility(
            conn, "org-1", "personal-someuser"
        )
        assert visibility == "private"

    @pytest.mark.asyncio
    async def test_org_slug_still_consults_db(self):
        """Non-personal slugs MUST still flow through the kb_config DB
        lookup so org admins can configure their KBs as
        public/internal/private as before.
        """
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value={"visibility": "public"})

        visibility = await kb_config.get_kb_visibility(conn, "org-1", "support")
        assert visibility == "public"
        conn.fetchrow.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_personal_slug_cached(self):
        """Second call for the same personal slug must not re-derive."""
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=None)
        slug = "personal-foo"

        first = await kb_config.get_kb_visibility(conn, "org-1", slug)
        second = await kb_config.get_kb_visibility(conn, "org-1", slug)

        assert first == "private"
        assert second == "private"
        # Cache must hold the private value so the org_id+slug key is
        # populated for downstream NOTIFY-eviction.
        assert kb_config._cache[kb_config._cache_key("org-1", slug)] == "private"
