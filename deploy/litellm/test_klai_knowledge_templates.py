"""Tests for the templates-injection path in deploy/litellm/klai_knowledge.py.

Pytest-discoverable locally when run from this directory:
    pytest deploy/litellm/test_klai_knowledge_templates.py

These tests are pure-function and mocked-httpx: they don't need Redis,
Postgres or running portal-api. They cover SPEC-CHAT-TEMPLATES-001
REQ-TEMPLATES-HOOK.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("KNOWLEDGE_RETRIEVE_URL", "http://retrieval-api:8040/retrieve")
sys.path.insert(0, str(Path(__file__).parent))

# litellm is not installed in the portal-api backend venv. The hook ships
# inside the LiteLLM container where CustomLogger is available. Stub the
# minimum surface we need so the module imports cleanly in CI.
if "litellm" not in sys.modules:
    litellm = types.ModuleType("litellm")
    integrations = types.ModuleType("litellm.integrations")
    custom_logger = types.ModuleType("litellm.integrations.custom_logger")

    class _CustomLogger:
        pass

    custom_logger.CustomLogger = _CustomLogger
    sys.modules["litellm"] = litellm
    sys.modules["litellm.integrations"] = integrations
    sys.modules["litellm.integrations.custom_logger"] = custom_logger


class _FakeCache:
    """Minimal stand-in for LiteLLM's DualCache (async_get_cache / async_set_cache)."""

    def __init__(self) -> None:
        self._store: dict = {}

    async def async_get_cache(self, key: str):
        return self._store.get(key)

    async def async_set_cache(self, key: str, value, ttl: int | None = None) -> None:
        self._store[key] = value


@pytest.fixture(autouse=True)
def _set_secret(monkeypatch):
    import klai_knowledge

    monkeypatch.setattr(klai_knowledge, "PORTAL_INTERNAL_SECRET", "test-secret")


# ---------------------------------------------------------------------------
# _build_template_instructions_block — pure formatter
# ---------------------------------------------------------------------------


def test_build_block_empty_returns_empty_string():
    import klai_knowledge as k

    assert k._build_template_instructions_block([]) == ""


def test_build_block_single_template_wraps_with_markers():
    import klai_knowledge as k

    block = k._build_template_instructions_block(
        [{"source": "template", "name": "Klantenservice", "text": "Wees vriendelijk."}]
    )
    assert "[Klai Templates — pas onderstaande instructies toe bij je antwoord]" in block
    assert "[Klantenservice]" in block
    assert "Wees vriendelijk." in block
    assert "[Einde templates]" in block


def test_build_block_preserves_order():
    import klai_knowledge as k

    block = k._build_template_instructions_block(
        [
            {"source": "template", "name": "First", "text": "A"},
            {"source": "template", "name": "Second", "text": "B"},
        ]
    )
    first_pos = block.index("[First]")
    second_pos = block.index("[Second]")
    assert first_pos < second_pos


def test_build_block_skips_empty_text_entries():
    import klai_knowledge as k

    block = k._build_template_instructions_block(
        [
            {"source": "template", "name": "Good", "text": "keep"},
            {"source": "template", "name": "Empty", "text": ""},
            {"source": "template", "name": "Whitespace", "text": "   "},
        ]
    )
    assert "[Good]" in block
    assert "[Empty]" not in block
    assert "[Whitespace]" not in block


# ---------------------------------------------------------------------------
# _get_templates — fetch + cache + fail-open
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_templates_fail_closed_without_secret(monkeypatch):
    """No PORTAL_INTERNAL_SECRET → empty list (can't authenticate)."""
    import klai_knowledge as k

    monkeypatch.setattr(k, "PORTAL_INTERNAL_SECRET", "")
    cache = _FakeCache()

    result = await k._get_templates("org-1", "user-1", cache)

    assert result == []


@pytest.mark.asyncio
async def test_get_templates_happy_path_caches_result():
    import klai_knowledge as k

    cache = _FakeCache()

    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(
        return_value={
            "instructions": [
                {"source": "template", "name": "T1", "text": "hi"},
            ]
        }
    )

    client = MagicMock()
    client.get = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    with patch("klai_knowledge.httpx.AsyncClient", return_value=client):
        result = await k._get_templates("org-1", "user-1", cache)

    assert len(result) == 1
    assert result[0]["name"] == "T1"
    # Cached under the expected key
    assert await cache.async_get_cache("templates:org-1:user-1") == result


@pytest.mark.asyncio
async def test_get_templates_cache_hit_no_http_call():
    """Second call with the same key MUST NOT hit httpx."""
    import klai_knowledge as k

    cache = _FakeCache()
    await cache.async_set_cache(
        "templates:org-1:user-1",
        [{"source": "template", "name": "Cached", "text": "t"}],
    )

    # If we wrongly hit httpx, this mock would raise AssertionError.
    with patch(
        "klai_knowledge.httpx.AsyncClient",
        side_effect=AssertionError("should not be called"),
    ):
        result = await k._get_templates("org-1", "user-1", cache)

    assert result[0]["name"] == "Cached"


@pytest.mark.asyncio
async def test_get_templates_timeout_fail_open():
    """REQ-TEMPLATES-HOOK-N1: timeout/5xx → empty list, no raise."""
    import klai_knowledge as k
    import httpx

    cache = _FakeCache()

    client = MagicMock()
    client.get = AsyncMock(side_effect=httpx.ReadTimeout("slow"))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    with patch("klai_knowledge.httpx.AsyncClient", return_value=client):
        result = await k._get_templates("org-1", "user-1", cache)

    assert result == []
    # Even a failed fetch caches empty so we don't retry for 30s.
    assert await cache.async_get_cache("templates:org-1:user-1") == []


@pytest.mark.asyncio
async def test_get_templates_401_config_error_fail_open():
    """401 from portal-api → empty list + error log (not a raise)."""
    import klai_knowledge as k
    import httpx

    cache = _FakeCache()

    resp = MagicMock()
    resp.status_code = 401
    err = httpx.HTTPStatusError("unauthorized", request=MagicMock(), response=resp)

    http_resp = MagicMock()
    http_resp.raise_for_status = MagicMock(side_effect=err)

    client = MagicMock()
    client.get = AsyncMock(return_value=http_resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)

    with patch("klai_knowledge.httpx.AsyncClient", return_value=client):
        result = await k._get_templates("org-1", "user-1", cache)

    assert result == []
