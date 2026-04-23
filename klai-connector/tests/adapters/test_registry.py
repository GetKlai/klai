"""Tests for AdapterRegistry — direct registration and alias support.

SPEC-KB-CONNECTORS-001 Phase 1, R1.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.adapters.base import BaseAdapter, DocumentRef
from app.adapters.registry import AdapterRegistry

# ---------------------------------------------------------------------------
# Minimal stub adapter — only used to satisfy BaseAdapter's ABC contract.
# ---------------------------------------------------------------------------


class _StubAdapter(BaseAdapter):
    """Minimal concrete implementation of BaseAdapter for registry tests."""

    def __init__(self) -> None:
        self.aclose = AsyncMock()  # type: ignore[method-assign]

    async def list_documents(
        self, connector: Any, cursor_context: dict[str, Any] | None = None
    ) -> list[DocumentRef]:
        return []

    async def fetch_document(self, ref: DocumentRef, connector: Any) -> bytes:
        return b""

    async def get_cursor_state(self, connector: Any) -> dict[str, Any]:
        return {}


# ---------------------------------------------------------------------------
# Direct registration
# ---------------------------------------------------------------------------


class TestDirectRegistration:
    def test_register_and_get_direct(self) -> None:
        registry = AdapterRegistry()
        adapter = _StubAdapter()
        registry.register("github", adapter)

        result = registry.get("github")

        assert result is adapter

    def test_get_unregistered_type_raises(self) -> None:
        registry = AdapterRegistry()

        with pytest.raises(ValueError, match="unknown"):
            registry.get("unknown")


# ---------------------------------------------------------------------------
# Alias registration
# ---------------------------------------------------------------------------


class TestAliasRegistration:
    def test_register_alias_returns_target_adapter(self) -> None:
        registry = AdapterRegistry()
        adapter = _StubAdapter()
        registry.register("target_key", adapter)
        registry.register_alias("user_facing_type", "target_key", {"filter": "x"})

        result = registry.get("user_facing_type")

        assert result is adapter

    def test_register_alias_preset_accessible(self) -> None:
        registry = AdapterRegistry()
        adapter = _StubAdapter()
        registry.register("target_key", adapter)
        registry.register_alias("user_facing_type", "target_key", {"filter": "x"})

        preset = registry.get_config_preset("user_facing_type")

        assert preset == {"filter": "x"}

    def test_register_alias_without_preset(self) -> None:
        registry = AdapterRegistry()
        adapter = _StubAdapter()
        registry.register("target_key", adapter)
        registry.register_alias("user_facing_type", "target_key")

        preset = registry.get_config_preset("user_facing_type")

        assert preset == {}

    def test_register_alias_to_missing_target_raises_on_get(self) -> None:
        """Alias registered before the target adapter; get() must raise with both names."""
        registry = AdapterRegistry()
        registry.register_alias("user_facing_type", "target_key")

        with pytest.raises(ValueError, match="user_facing_type"):
            registry.get("user_facing_type")

        # The error message must also mention the missing target key.
        with pytest.raises(ValueError, match="target_key"):
            registry.get("user_facing_type")

    def test_register_alias_over_existing_direct_raises(self) -> None:
        """register_alias with a type that already has a direct registration must raise."""
        registry = AdapterRegistry()
        adapter = _StubAdapter()
        registry.register("my_type", adapter)

        with pytest.raises(ValueError):
            registry.register_alias("my_type", "some_other_key")

    def test_get_config_preset_for_direct_registration_returns_empty(self) -> None:
        """Direct registrations have no preset — get_config_preset returns {}."""
        registry = AdapterRegistry()
        adapter = _StubAdapter()
        registry.register("github", adapter)

        assert registry.get_config_preset("github") == {}


# ---------------------------------------------------------------------------
# aclose deduplication
# ---------------------------------------------------------------------------


class TestAclose:
    @pytest.mark.asyncio
    async def test_aclose_closes_underlying_adapter_once(self) -> None:
        """When an alias and a direct key point to the same adapter, aclose is called once."""
        registry = AdapterRegistry()
        adapter = _StubAdapter()
        registry.register("target_key", adapter)
        registry.register_alias("alias_key", "target_key", {"x": 1})

        await registry.aclose()

        adapter.aclose.assert_called_once()
