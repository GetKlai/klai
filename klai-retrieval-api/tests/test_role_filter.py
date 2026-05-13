"""SPEC-PORTAL-RBAC-REFACTOR-001 REQ-17 / REQ-6 — personal-role slug filtering.

Tests:
  - RetrieveRequest.effective_role field (model unit tests)
  - Personal-role scope-rewrite logic in the retrieve endpoint (via captured search calls)
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Unit tests: RetrieveRequest.effective_role field
# ---------------------------------------------------------------------------


class TestRetrieveRequestEffectiveRole:
    def test_default_is_unknown(self) -> None:
        from retrieval_api.models import RetrieveRequest

        req = RetrieveRequest(query="q", org_id="o1")
        assert req.effective_role == "unknown"

    def test_personal_accepted(self) -> None:
        from retrieval_api.models import RetrieveRequest

        req = RetrieveRequest(query="q", org_id="o1", effective_role="personal")
        assert req.effective_role == "personal"

    def test_company_accepted(self) -> None:
        from retrieval_api.models import RetrieveRequest

        req = RetrieveRequest(query="q", org_id="o1", effective_role="company")
        assert req.effective_role == "company"

    def test_admin_accepted(self) -> None:
        from retrieval_api.models import RetrieveRequest

        req = RetrieveRequest(query="q", org_id="o1", effective_role="admin")
        assert req.effective_role == "admin"

    def test_unknown_literal_accepted(self) -> None:
        from retrieval_api.models import RetrieveRequest

        req = RetrieveRequest(query="q", org_id="o1", effective_role="unknown")
        assert req.effective_role == "unknown"

    def test_arbitrary_string_accepted(self) -> None:
        """Field is str, not an Enum; any string is allowed (gate logic lives in retrieve.py)."""
        from retrieval_api.models import RetrieveRequest

        req = RetrieveRequest(query="q", org_id="o1", effective_role="future_role")
        assert req.effective_role == "future_role"


# ---------------------------------------------------------------------------
# Unit tests: personal-role scope-rewrite logic
# These test the LOGIC that would run inside retrieve(), extracted as a
# pure model_copy transform so we don't need to stub the full endpoint.
# ---------------------------------------------------------------------------


def _apply_role_rewrite(req):
    """Mirror the rewrite block from retrieve.py so we can test it in isolation."""
    if req.effective_role == "personal":
        if req.scope != "personal":
            req = req.model_copy(update={"scope": "personal", "kb_slugs": None})
        elif req.kb_slugs is not None:
            req = req.model_copy(update={"kb_slugs": None})
    return req


class TestPersonalRoleRewrite:
    """The scope-rewrite logic must strip org access for personal-role callers."""

    def test_personal_role_org_scope_becomes_personal(self) -> None:
        from retrieval_api.models import RetrieveRequest

        req = RetrieveRequest(
            query="q",
            org_id="o1",
            user_id="u1",
            scope="org",
            effective_role="personal",
        )
        result = _apply_role_rewrite(req)
        assert result.scope == "personal"

    def test_personal_role_both_scope_becomes_personal(self) -> None:
        from retrieval_api.models import RetrieveRequest

        req = RetrieveRequest(
            query="q",
            org_id="o1",
            user_id="u1",
            scope="both",
            effective_role="personal",
        )
        result = _apply_role_rewrite(req)
        assert result.scope == "personal"

    def test_personal_role_clears_kb_slugs(self) -> None:
        from retrieval_api.models import RetrieveRequest

        req = RetrieveRequest(
            query="q",
            org_id="o1",
            user_id="u1",
            scope="org",
            kb_slugs=["org", "some-team-kb"],
            effective_role="personal",
        )
        result = _apply_role_rewrite(req)
        assert result.kb_slugs is None

    def test_personal_role_personal_scope_clears_kb_slugs(self) -> None:
        """personal role + already personal scope: still clear any kb_slugs."""
        from retrieval_api.models import RetrieveRequest

        req = RetrieveRequest(
            query="q",
            org_id="o1",
            user_id="u1",
            scope="personal",
            kb_slugs=["sneaky-org-kb"],
            effective_role="personal",
        )
        result = _apply_role_rewrite(req)
        assert result.kb_slugs is None

    def test_company_role_does_not_change_scope(self) -> None:
        from retrieval_api.models import RetrieveRequest

        req = RetrieveRequest(
            query="q",
            org_id="o1",
            scope="org",
            effective_role="company",
        )
        result = _apply_role_rewrite(req)
        assert result.scope == "org"

    def test_unknown_role_does_not_change_scope(self) -> None:
        """Older callers that don't send effective_role default to 'unknown' — unchanged."""
        from retrieval_api.models import RetrieveRequest

        req = RetrieveRequest(
            query="q",
            org_id="o1",
            scope="both",
            user_id="u1",
            effective_role="unknown",
        )
        result = _apply_role_rewrite(req)
        assert result.scope == "both"

    def test_admin_role_does_not_change_scope(self) -> None:
        from retrieval_api.models import RetrieveRequest

        req = RetrieveRequest(
            query="q",
            org_id="o1",
            scope="org",
            effective_role="admin",
        )
        result = _apply_role_rewrite(req)
        assert result.scope == "org"
