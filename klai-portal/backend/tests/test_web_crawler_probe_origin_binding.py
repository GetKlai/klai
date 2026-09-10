"""Regression: saved web-crawler credentials must not be probeable against
an arbitrary URL.

``auth_probe`` / ``crawl_preview`` accept ``use_saved_credentials=True`` +
``connector_id`` and decrypt the connector's stored cookies server-side, then
forward them to whatever ``url`` the caller supplied. Before this fix, the
frontend wizard's "test URL" field was the only restriction - a direct API
call (or a user editing base_url first) could point that URL at an
attacker-controlled domain and exfiltrate the org's saved session cookies.

``_load_saved_web_crawler_cookies`` now requires ``probe_url`` to share the
connector's stored ``base_url`` origin before it will decrypt anything.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api.app_knowledge_bases import _load_saved_web_crawler_cookies


def _kb() -> MagicMock:
    kb = MagicMock()
    kb.id = 42
    return kb


def _db_with_connector(*, base_url: str) -> MagicMock:
    connector = MagicMock()
    connector.config = {"base_url": base_url}
    connector.encrypted_credentials = None  # short-circuits after the origin check
    result = MagicMock()
    result.scalar_one_or_none.return_value = connector
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.mark.asyncio
async def test_cross_origin_probe_url_rejected_before_decrypt() -> None:
    db = _db_with_connector(base_url="https://wiki.redcactus.cloud/nl/")
    with pytest.raises(HTTPException) as exc_info:
        await _load_saved_web_crawler_cookies(
            _kb(),
            "connector-1",
            org_id=8,
            db=db,
            probe_url="https://attacker.example.com/collect",
        )
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == {"error_code": "probe_url_origin_mismatch"}


@pytest.mark.asyncio
async def test_same_origin_probe_url_passes_the_guard() -> None:
    """A same-origin probe URL (different path) clears the origin check and
    reaches the next validation step - proven here by getting the DIFFERENT
    409 that fires when there are no stored credentials to decrypt, not the
    400 from the origin guard."""
    db = _db_with_connector(base_url="https://wiki.redcactus.cloud/nl/")
    with pytest.raises(HTTPException) as exc_info:
        await _load_saved_web_crawler_cookies(
            _kb(),
            "connector-1",
            org_id=8,
            db=db,
            probe_url="https://wiki.redcactus.cloud/nl/login",
        )
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {"error_code": "saved_credentials_missing"}
