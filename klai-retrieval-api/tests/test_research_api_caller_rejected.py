"""SPEC-DECOMM-FOCUS-001 D4: receiver-side regression test.

Locks in that ``research-api`` is no longer in ``KNOWN_CALLER_SERVICES``.
If a future PR adds it back to the allowlist (or any other consumer of
``klai_identity_assert.KNOWN_CALLER_SERVICES`` resurrects the entry),
this test fails so the regression cannot land silently.

See:
- ``.claude/rules/klai/pitfalls/process-rules.md`` →
  ``retrieve-caller-service-header-mismatch``
- ``.moai/specs/SPEC-DECOMM-FOCUS-001/spec.md`` D4
"""

from __future__ import annotations

import os


def test_research_api_caller_is_not_known() -> None:
    """``research-api`` MUST NOT be a recognised caller service."""
    from klai_identity_assert import KNOWN_CALLER_SERVICES

    assert "research-api" not in KNOWN_CALLER_SERVICES


def test_unknown_caller_service_returns_400(client) -> None:
    """A request with X-Caller-Service: research-api is rejected with 400.

    The middleware short-circuits before any business logic. The exact
    body is the one set by ``klai_identity_assert.AssertResult`` when the
    caller_service is unknown — we assert on the status code and on the
    presence of the discriminator in the body to keep the test robust
    against future copy changes.
    """
    resp = client.post(
        "/retrieve",
        json={"query": "test", "org_id": "org-1", "scope": "org"},
        headers={
            "X-Internal-Secret": os.environ["INTERNAL_SECRET"],
            "X-Caller-Service": "research-api",
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    detail = body.get("detail", "")
    assert "caller" in str(detail).lower() or "service" in str(detail).lower()
