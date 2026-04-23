"""Adapter registry for klai-connector."""

from typing import Any

from app.adapters.base import BaseAdapter


class AdapterRegistry:
    """Registry mapping connector types to their adapter instances.

    Supports two kinds of entries:

    * **Direct registration** — ``register(connector_type, adapter)``
    * **Alias registration** — ``register_alias(connector_type, target_adapter_key,
      config_preset)`` maps a user-facing type to an already-registered adapter
      while optionally carrying a config preset that the caller can merge over the
      live connector.config before invoking adapter methods.
    """

    def __init__(self) -> None:
        self._adapters: dict[str, BaseAdapter] = {}
        # connector_type -> target_adapter_key for aliases
        self._aliases: dict[str, str] = {}
        # connector_type -> config preset dict (only populated for aliases)
        self._presets: dict[str, dict[str, Any]] = {}

    def register(self, connector_type: str, adapter: BaseAdapter) -> None:
        """Register an adapter for a given connector type."""
        self._adapters[connector_type] = adapter

    def register_alias(
        self,
        connector_type: str,
        target_adapter_key: str,
        config_preset: dict[str, Any] | None = None,
    ) -> None:
        """Register ``connector_type`` as an alias for ``target_adapter_key``.

        When ``get(connector_type)`` is called the adapter registered under
        ``target_adapter_key`` is returned.  The optional ``config_preset`` is
        stored and retrievable via ``get_config_preset(connector_type)`` so that
        callers can merge it over the live ``connector.config`` before invoking
        adapter methods.

        Args:
            connector_type: User-facing connector type string (e.g. ``"google_docs"``).
            target_adapter_key: Adapter key already (or to be) registered via
                ``register()`` (e.g. ``"google_drive"``).
            config_preset: Optional dict that the sync engine merges OVER
                ``connector.config`` when calling the target adapter.  Defaults to
                an empty dict.

        Raises:
            ValueError: If ``connector_type`` already has a direct registration.
        """
        if connector_type in self._adapters:
            raise ValueError(
                f"Cannot register alias {connector_type!r}: a direct adapter registration "
                f"already exists for that type."
            )
        self._aliases[connector_type] = target_adapter_key
        self._presets[connector_type] = config_preset if config_preset is not None else {}

    def get(self, connector_type: str) -> BaseAdapter:
        """Retrieve the adapter for a connector type.

        Alias types are resolved to their target adapter.

        Raises:
            ValueError: If no adapter is registered for the given type, or if an
                alias points to an unregistered target.
        """
        # Direct registration takes priority.
        if connector_type in self._adapters:
            return self._adapters[connector_type]

        # Alias resolution.
        if connector_type in self._aliases:
            target_key = self._aliases[connector_type]
            adapter = self._adapters.get(target_key)
            if adapter is None:
                raise ValueError(
                    f"Alias {connector_type!r} points to unregistered adapter {target_key!r}"
                )
            return adapter

        raise ValueError(f"Unsupported connector type: {connector_type!r}")

    def get_config_preset(self, connector_type: str) -> dict[str, Any]:
        """Return the config preset for an alias type, or an empty dict.

        For directly-registered types (no alias), always returns ``{}``.
        The returned dict is a copy — mutating it does not affect the registry.
        """
        return dict(self._presets.get(connector_type, {}))

    async def aclose(self) -> None:
        """Close all registered adapters that support it (each adapter closed once)."""
        seen: set[int] = set()
        for adapter in self._adapters.values():
            adapter_id = id(adapter)
            if adapter_id in seen:
                continue
            seen.add(adapter_id)
            if hasattr(adapter, "aclose"):
                await adapter.aclose()  # type: ignore[union-attr]
