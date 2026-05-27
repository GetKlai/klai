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


# ---------------------------------------------------------------------------
# Integration: role-rewrite + scope-filter chain (SPEC-RAG-PERSONAL-SCOPE-001 REQ-5)
# ---------------------------------------------------------------------------


class TestPersonalRoleRewriteChainsIntoCanonicalNarrow:
    """The personal-role kb_slugs strip (RBAC, REQ-17 of
    SPEC-PORTAL-RBAC-REFACTOR-001) MUST NOT defeat the canonical-slug
    narrowing introduced by SPEC-RAG-PERSONAL-SCOPE-001 REQ-2.

    Chain: client request → ``_apply_role_rewrite`` → ``_scope_filter``.
    Even though the strip removes ``kb_slugs`` for personal-role callers,
    ``_scope_filter`` must still append the canonical-slug filter.
    """

    def test_personal_role_stripped_slugs_still_canonical_narrowed(self) -> None:
        from qdrant_client.models import FieldCondition

        from retrieval_api.models import RetrieveRequest
        from retrieval_api.services.search import _scope_filter

        # Start from a personal-role caller's "I want canonical Persoonlijk
        # only" intent — but with org-side kb_slugs that the strip should
        # remove. Pre-SPEC, this leaked: scope became personal, kb_slugs
        # became None, and _scope_filter's user_id-OR-kb_slug let test2
        # chunks through via the user_id branch.
        req = RetrieveRequest(
            query="wie is jantine?",
            org_id="o1",
            scope="org",
            kb_slugs=["sneaky-org-kb"],
            user_id="u1",
            effective_role="personal",
        )

        # Apply the rewrite (strips kb_slugs, forces scope to personal).
        rewritten = _apply_role_rewrite(req)
        assert rewritten.scope == "personal"
        assert rewritten.kb_slugs is None

        # The canonical narrow MUST be present in the filter conditions.
        conditions = _scope_filter(rewritten)
        slug_conditions = [
            c for c in conditions if isinstance(c, FieldCondition) and c.key == "kb_slug"
        ]
        assert len(slug_conditions) == 1, (
            f"expected exactly one canonical kb_slug filter after the chain, "
            f"got {len(slug_conditions)}"
        )
        assert slug_conditions[0].match.value == "personal-u1"

    def test_personal_role_with_canonical_kb_slugs_strip_then_narrow(self) -> None:
        from qdrant_client.models import FieldCondition

        from retrieval_api.models import RetrieveRequest
        from retrieval_api.services.search import _scope_filter

        # Edge case: the LiteLLM hook (post-PR-#715) sends
        # kb_slugs=["personal-u1"] together with scope=personal AND
        # effective_role=personal. The RBAC strip removes kb_slugs (it
        # doesn't distinguish canonical from non-canonical). Server-side
        # canonical narrow then re-applies it. End-state: caller's
        # intent is preserved despite the strip.
        req = RetrieveRequest(
            query="q",
            org_id="o1",
            scope="personal",
            kb_slugs=["personal-u1"],
            user_id="u1",
            effective_role="personal",
        )
        rewritten = _apply_role_rewrite(req)
        # Strip removed the client-supplied filter
        assert rewritten.kb_slugs is None
        # Server-side canonical narrow recovers it
        conditions = _scope_filter(rewritten)
        slug_conditions = [
            c for c in conditions if isinstance(c, FieldCondition) and c.key == "kb_slug"
        ]
        assert len(slug_conditions) == 1
        assert slug_conditions[0].match.value == "personal-u1"
