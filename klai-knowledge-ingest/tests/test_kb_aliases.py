"""The KB alias lookup (plan step 6); the alias line itself is tested in test_cheap_layer."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from knowledge_ingest import kb_config


@pytest.mark.asyncio
async def test_alias_lookup_fails_open_before_the_portal_migration_has_run():
    pool = AsyncMock()
    pool.fetchval.side_effect = asyncpg.UndefinedColumnError("column kb.aliases does not exist")
    kb_config._alias_cache.clear()
    with patch.object(kb_config, "get_pool", AsyncMock(return_value=pool)):
        assert await kb_config.get_kb_aliases("org-1", "help") == []
