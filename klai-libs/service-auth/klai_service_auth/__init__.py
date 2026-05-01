"""Klai service-to-service authentication library.

SPEC-SEC-SERVICE-AUTH-001. Replaces the legacy ``X-Internal-Secret`` shared
secret pattern with OAuth 2.0 Client Credentials grant against Zitadel.

Public API
----------

* ``ZitadelTokenClient`` — async token client with caching + thundering-herd
  prevention.
* ``ServiceAuthError`` — raised when token mint fails (network, IdP error,
  invalid credentials). Callers decide whether to fail-closed or fall back.
* ``scopes`` — canonical scope-name constants for receiver-side authorization.
"""

from __future__ import annotations

from klai_service_auth import scopes
from klai_service_auth.client import (
    ServiceAuthError,
    ZitadelTokenClient,
)

__all__ = ["ServiceAuthError", "ZitadelTokenClient", "scopes"]
