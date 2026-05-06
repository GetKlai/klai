"""Shared test helpers for klai-knowledge-mcp.

Lives next to ``conftest.py`` but as its own module so callers can use a
plain ``from tests._helpers import allow_verify_result`` import. Conftest
is reserved for pytest fixture / hook registration; mixing helpers in
there relies on pytest's import side-effects rather than a real module
contract.
"""

from __future__ import annotations

from klai_identity_assert import VerifyResult
from klai_identity_assert.models import Evidence


def allow_verify_result(
    *,
    user_id: str = "user1",
    org_id: str = "org1",
    org_slug: str = "testorg",
    evidence: Evidence = "jwt",
) -> VerifyResult:
    """Build an allow-style ``VerifyResult`` for tests that mock ``_asserter.verify``.

    Defaults match the standard test fixture (``user1/org1/testorg``) used by
    every taxonomy/security/sec-internal test. Identity-assert tests pass
    explicit kwargs (different ``user_id``/``org_id``/``org_slug``/``evidence``)
    to exercise spoof and membership-fallback scenarios.

    Returns a properly-typed ``VerifyResult`` — no ``# type: ignore`` needed
    at the call sites because ``evidence`` is constrained to the ``Evidence``
    Literal at the function boundary.
    """
    return VerifyResult.allow(
        user_id=user_id,
        org_id=org_id,
        org_slug=org_slug,
        evidence=evidence,
    )
