# Negative fixtures for .semgrep/rules/jwt-peek-without-verify.yml
#
# Each function below contains a legitimate peek-then-verify pattern.
# The rule MUST NOT fire on any of them. If semgrep reports a finding here,
# the rule has regressed into a false-positive — fix the rule, do not
# silence with `# nosemgrep:`.
#
# This file mirrors the canonical pattern at
# `klai-portal/backend/app/api/partner_dependencies.py:100-145` plus three
# alternative verified-decode shapes the rule must recognise.
#
# This file is fixture-only. Do not import from production code.

from __future__ import annotations

from typing import Any

import jwt


def decode_session_token(
    token: str, master_secret: str, tenant_slug: str
) -> dict[str, Any]:
    """Stand-in for the real verified wrapper in widget_auth.py."""
    derived_key = master_secret + tenant_slug  # NOT a real HKDF, fixture only
    return jwt.decode(token, derived_key, algorithms=["HS256"])


# ---------------------------------------------------------------------------
# Shape 1: peek -> decode_session_token(...) (canonical klai pattern)
# ---------------------------------------------------------------------------
def case_decode_session_token_wrapper(token: str, master_secret: str) -> dict[str, Any]:
    unverified = jwt.decode(token, options={"verify_signature": False})
    org_id = unverified.get("org_id", 0)
    tenant_slug = f"tenant-{org_id}"
    payload = decode_session_token(
        token, master_secret=master_secret, tenant_slug=tenant_slug
    )
    return payload


# ---------------------------------------------------------------------------
# Shape 2: peek -> jwt.decode(..., options={"verify_signature": True})
# ---------------------------------------------------------------------------
def case_explicit_verify_true(token: str, key: str) -> dict[str, Any]:
    unverified = jwt.decode(token, options={"verify_signature": False})
    _ = unverified.get("kid")
    payload = jwt.decode(
        token,
        key,
        algorithms=["HS256"],
        options={"verify_signature": True},
    )
    return payload


# ---------------------------------------------------------------------------
# Shape 3: peek -> bare positional-key decode
# ---------------------------------------------------------------------------
def case_bare_positional_key(token: str, key: str) -> dict[str, Any]:
    unverified = jwt.decode(token, options={"verify_signature": False})
    _ = unverified.get("aud")
    payload = jwt.decode(token, key, algorithms=["HS256"])
    return payload


# ---------------------------------------------------------------------------
# Shape 4: peek -> keyword-form key
# ---------------------------------------------------------------------------
def case_keyword_key(token: str, signing_key: str) -> dict[str, Any]:
    unverified = jwt.decode(token, options={"verify_signature": False})
    _ = unverified.get("iss")
    payload = jwt.decode(token, key=signing_key, algorithms=["HS256"])
    return payload
