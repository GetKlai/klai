"""Connector credential encryption service (SPEC-KB-020, SPEC-CRAWLER-004).

Thin re-export of the shared :mod:`connector_credentials` library. The store
logic and ``SENSITIVE_FIELDS`` mapping live in ``klai-libs/connector-credentials``
so every Klai service loads them from a single source of truth. The module-level
``credential_store`` singleton is constructed here because only portal-api
reads :mod:`app.core.config.settings` directly; other services build their own
store from their own settings.
"""

from __future__ import annotations

import logging

from connector_credentials import (
    SENSITIVE_FIELDS,
    ConnectorCredentialStore,
)

__all__ = [
    "SENSITIVE_FIELDS",
    "ConnectorCredentialStore",
    "credential_store",
]

logger = logging.getLogger(__name__)


def _create_credential_store() -> ConnectorCredentialStore | None:
    """Create the module-level credential store singleton.

    Returns None when ENCRYPTION_KEY is not configured (dev environments
    that do not exercise connector credential encryption).
    """
    from app.core.config import settings

    if not settings.encryption_key:
        return None
    try:
        return ConnectorCredentialStore(settings.encryption_key)
    except ValueError:
        logger.warning("Invalid ENCRYPTION_KEY, connector credential encryption disabled")
        return None


# Module-level singleton -- None when encryption is not configured.
credential_store = _create_credential_store()
