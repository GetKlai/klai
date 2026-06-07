"""Token-claim parsing shared by Phase C JWT receivers.

SPEC-SEC-SERVICE-AUTH-002 REQ-4 (decision B+): Zitadel cannot populate the
RFC 6749 ``scope`` claim from an Action — ``setClaim`` only ADDS new claims
(it cannot overwrite the reserved ``scope`` claim) and keys prefixed with
``urn:zitadel:iam`` are ignored
(https://zitadel.com/docs/apis/actions/complement-token). So service-principal
authorization scopes are read from Zitadel's NATIVE project-role claims
instead. This module is the single canonical implementation every receiver
imports, so the claim shape lives in exactly one place across all 7 Phase C
service pairs.
"""

from __future__ import annotations

from typing import Any

_ROLES_CLAIM_PREFIX = "urn:zitadel:iam:org:project:"
_ROLES_CLAIM_SUFFIX = ":roles"


def project_role_scopes(payload: dict[str, Any]) -> set[str]:
    """Return granted project-role keys from a Zitadel access-token payload.

    Zitadel emits granted roles in claims of shape
    ``urn:zitadel:iam:org:project:<projectId>:roles`` — a dict whose KEYS are
    the role keys. Klai sets role key == internal scope string (e.g.
    ``klai:internal:retrieval:query``), so the keys ARE the authorization
    scopes. A machine token surfaces them only when it requests the reserved
    ``urn:zitadel:iam:org:projects:roles`` scope; the standard ``scope`` claim
    stays empty.

    Matches any project's roles claim, so a receiver need not know its own
    projectId. Verified against a live ``svc-litellm`` token (2026-06-07).
    """
    scopes: set[str] = set()
    for key, value in payload.items():
        if (
            key.startswith(_ROLES_CLAIM_PREFIX)
            and key.endswith(_ROLES_CLAIM_SUFFIX)
            and isinstance(value, dict)
        ):
            scopes.update(str(role_key) for role_key in value)
    return scopes
