"""Read-only LiteLLM analytics database access for platform usage stats."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import settings

_engine: AsyncEngine | None = None


def litellm_analytics_configured() -> bool:
    return bool(settings.litellm_analytics_database_url.strip())


def _get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.litellm_analytics_database_url,
            pool_size=2,
            max_overflow=2,
            pool_pre_ping=True,
            connect_args={"server_settings": {"statement_timeout": "5000"}},
        )
    return _engine


async def execute_litellm_analytics(
    sql: str,
    params: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Run one read-only LiteLLM analytics query.

    The configured database role is expected to have SELECT only on
    LiteLLM_TeamTable and LiteLLM_DailyTeamSpend.
    """
    if not litellm_analytics_configured():
        raise RuntimeError("LiteLLM analytics database is not configured")

    async with _get_engine().connect() as conn:
        result = await conn.execute(text(sql), dict(params or {}))
        return [dict(row) for row in result.mappings().all()]
