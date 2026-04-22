"""AES-256-GCM encryption utilities for credential storage.

Thin re-export of :class:`connector_credentials.cipher.AESGCMCipher` from the
shared ``klai-connector-credentials`` library (SPEC-CRAWLER-004 Fase 0). Every
historical import path (``app.core.security.AESGCMCipher``) remains valid;
the implementation now lives in a single path-installed lib shared across
portal-api, klai-connector, and knowledge-ingest.
"""

from connector_credentials.cipher import AESGCMCipher

__all__ = ["AESGCMCipher"]
