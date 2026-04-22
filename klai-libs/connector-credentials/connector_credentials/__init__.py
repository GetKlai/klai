"""Shared connector credential encryption (SPEC-KB-020, SPEC-CRAWLER-004).

Public API re-exports AESGCMCipher, SENSITIVE_FIELDS and ConnectorCredentialStore
so downstream services can ``from connector_credentials import ...``.
"""

from connector_credentials.cipher import AESGCMCipher
from connector_credentials.store import (
    SENSITIVE_FIELDS,
    ConnectorCredentialStore,
)

__all__ = [
    "SENSITIVE_FIELDS",
    "AESGCMCipher",
    "ConnectorCredentialStore",
]
