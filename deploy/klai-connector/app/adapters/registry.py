"""Adapter registry for klai-connector."""

from app.adapters.base import BaseAdapter


class AdapterRegistry:
    """Registry mapping connector types to their adapter instances."""

    def __init__(self) -> None:
        self._adapters: dict[str, BaseAdapter] = {}

    def register(self, connector_type: str, adapter: BaseAdapter) -> None:
        """Register an adapter for a given connector type."""
        self._adapters[connector_type] = adapter

    def get(self, connector_type: str) -> BaseAdapter:
        """Retrieve the adapter for a connector type.

        Raises:
            ValueError: If no adapter is registered for the given type.
        """
        adapter = self._adapters.get(connector_type)
        if adapter is None:
            raise ValueError(f"Unsupported connector type: {connector_type!r}")
        return adapter

    async def aclose(self) -> None:
        """Close all registered adapters that support it."""
        for adapter in self._adapters.values():
            if hasattr(adapter, "aclose"):
                await adapter.aclose()  # type: ignore[union-attr]
