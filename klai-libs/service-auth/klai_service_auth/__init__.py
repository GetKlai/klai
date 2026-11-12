"""Klai service-to-service authentication library.

SPEC-SEC-SERVICE-AUTH-001 / -002. Receivers authorize service principals from
Zitadel's native project-role claims.

Public API
----------

* ``project_role_scopes`` — extract granted role keys from a Zitadel
  access-token payload (the canonical receiver-side claim parser shared by
  every Phase C receiver; see ``claims`` for why roles, not ``scope``).
"""

from __future__ import annotations

from klai_service_auth.claims import project_role_scopes

__all__ = ["project_role_scopes"]
