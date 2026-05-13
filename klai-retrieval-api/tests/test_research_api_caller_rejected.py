"""SPEC-DECOMM-FOCUS-001 D4: receiver-side regression test.

Locks in that ``research-api`` is no longer in ``KNOWN_CALLER_SERVICES``.
If a future PR adds it back to the allowlist (or any other consumer of
``klai_identity_assert.KNOWN_CALLER_SERVICES`` resurrects the entry),
this test fails so the regression cannot land silently.

A direct API-level smoke (POST /retrieve with X-Caller-Service:
research-api → 400) was considered but the conftest's ``_auto_allow_identity_assert``
fixture stubs the asserter for every test, so the API-level path is
not the right guard. The set membership test below is the canonical
contract.

See:
- ``.claude/rules/klai/pitfalls/process-rules.md`` →
  ``retrieve-caller-service-header-mismatch``
- ``.moai/specs/SPEC-DECOMM-FOCUS-001/spec.md`` D4
"""

from __future__ import annotations


def test_research_api_caller_is_not_known() -> None:
    """``research-api`` MUST NOT be a recognised caller service."""
    from klai_identity_assert import KNOWN_CALLER_SERVICES

    assert "research-api" not in KNOWN_CALLER_SERVICES


def test_known_callers_still_present() -> None:
    """Sanity check: removing research-api did not accidentally remove the
    services that ARE supposed to remain in the allowlist.
    """
    from klai_identity_assert import KNOWN_CALLER_SERVICES

    expected_callers = {
        "knowledge-mcp",
        "scribe",
        "retrieval-api",
        "connector",
        "mailer",
        "litellm",
        "portal-api",
    }
    missing = expected_callers - KNOWN_CALLER_SERVICES
    assert not missing, f"Expected callers missing from allowlist: {missing}"
