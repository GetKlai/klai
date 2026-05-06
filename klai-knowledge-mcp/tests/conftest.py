"""Stable env for module-level config capture (SPEC-SEC-INTERNAL-001 REQ-9.5)
and shared test helpers.

Module-level env capture
------------------------

``main.py`` captures every required secret env var at import time so a missing
value fails the process loudly at startup. The previous test fixtures use
``monkeypatch.setenv(...)`` from inside per-test fixtures, which runs AFTER
the first ``import main`` has already frozen the module-level constants.

This conftest sets a stable test-only env BEFORE pytest collects any test
module, so the values that the per-test fixtures expect (e.g.
``KNOWLEDGE_INGEST_SECRET=test-secret``, ``DOCS_INTERNAL_SECRET=docs-secret``)
are also what main.py captures on first import. The per-test fixtures stay
in place for documentation -- they happen to be no-ops once the module-level
constants are correct, but they also keep the env values visible at test
read-time.

Shared helpers
--------------

``allow_verify_result`` returns a ``VerifyResult.allow(...)`` for tests that
need to bypass identity verification (SPEC-SEC-IDENTITY-ASSERT-001 REQ-2)
without rolling their own helper. Defaults match the standard
``user1/org1/testorg`` fixture used by every taxonomy/security test; pass
explicit kwargs for spoof scenarios in test_identity_assert.py.
"""

from __future__ import annotations

import os

from klai_identity_assert import VerifyResult
from klai_identity_assert.models import Evidence

# Use unconditional assignment (not setdefault) so a stale value inherited
# from the parent shell does not corrupt the test fixture chain.
os.environ["KLAI_DOCS_API_BASE"] = "http://docs-app:3000"
os.environ["DOCS_INTERNAL_SECRET"] = "docs-secret"
os.environ["KNOWLEDGE_INGEST_URL"] = "http://knowledge-ingest:8000"
os.environ["KNOWLEDGE_INGEST_SECRET"] = "test-secret"
os.environ["PORTAL_API_URL"] = "http://portal-api:8010"
os.environ["PORTAL_INTERNAL_SECRET"] = "portal-test-secret"


def allow_verify_result(
    *,
    user_id: str = "user1",
    org_id: str = "org1",
    org_slug: str = "testorg",
    evidence: Evidence = "jwt",
) -> VerifyResult:
    """Build an allow-style VerifyResult for tests that mock ``_asserter.verify``.

    Defaults match the standard test fixture (``user1/org1/testorg``) used by
    taxonomy, security, and sec-internal tests. Identity-assert tests pass
    explicit kwargs (different user_id/org_id/org_slug/evidence) to exercise
    spoof and membership-fallback scenarios.
    """
    return VerifyResult.allow(
        user_id=user_id,
        org_id=org_id,
        org_slug=org_slug,
        evidence=evidence,
    )
