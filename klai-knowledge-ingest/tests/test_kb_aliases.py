"""KB aliases in the context prefix of a chunk that names one of them (plan step 6)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from knowledge_ingest import kb_config
from knowledge_ingest.enrichment import EnrichedChunk


def _chunk(text: str) -> EnrichedChunk:
    return EnrichedChunk(
        original_text=text,
        enriched_text=f"Context.\n\n{text}",
        context_prefix="Context.",
        questions=[],
    )


@pytest.mark.asyncio
async def test_alias_lookup_fails_open_before_the_portal_migration_has_run():
    pool = AsyncMock()
    pool.fetchval.side_effect = asyncpg.UndefinedColumnError("column kb.aliases does not exist")
    kb_config._alias_cache.clear()
    with patch.object(kb_config, "get_pool", AsyncMock(return_value=pool)):
        assert await kb_config.get_kb_aliases("org-1", "help") == []


def test_a_chunk_naming_an_alias_gets_the_other_names_in_its_context_prefix():
    naming = _chunk("Install the web client from the download page.")
    other = _chunk("Faxes arrive as a PDF in your mailbox.")

    kb_config.add_alias_names(
        [naming, other], [["Web client", "Windows app", "desktop app"], ["Fax", "e-fax"]]
    )

    assert naming.context_prefix == "Context.\nAlso known as: Windows app | desktop app"
    assert naming.enriched_text.endswith("\n\nAlso known as: Windows app | desktop app")
    assert other.context_prefix == "Context."
    assert other.enriched_text == "Context.\n\nFaxes arrive as a PDF in your mailbox."
