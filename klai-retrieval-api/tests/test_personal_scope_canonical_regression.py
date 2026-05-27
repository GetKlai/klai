"""SPEC-RAG-PERSONAL-SCOPE-001 REQ-7 — Jantine scenario regression.

The Jantine incident (2026-05-27 ~17:00 CEST): a user with two private
knowledge bases — canonical Persoonlijk (slug ``personal-<user_id>``)
and a user-created KB called ``test2`` — selected only "Persoonlijk" in
the chat dropdown but received chunks from ``test2`` anyway. The leak
was kept alive for personal-role callers by retrieval-api's
``_apply_role_rewrite`` stripping the client-side kb_slugs filter (PR #715
fix landed but was effectively bypassed for the default role).

These tests pin the post-SPEC-RAG-PERSONAL-SCOPE-001 contract: scope=personal
ALWAYS narrows to the canonical Persoonlijk-KB slug at the
``_scope_filter`` layer, regardless of ``effective_role`` and regardless
of whether the client supplied ``kb_slugs``.
"""

from __future__ import annotations

import pytest
from klai_kb_slugs import personal_kb_slug
from qdrant_client.models import FieldCondition

from retrieval_api.models import RetrieveRequest
from retrieval_api.services.search import _scope_filter


@pytest.mark.parametrize(
    "role",
    ["personal", "company", "kb_manager", "group_manager", "admin", "unknown"],
)
def test_scope_personal_canonical_filter_applies_for_every_role(role: str) -> None:
    """Across the entire ProfileRole ladder, scope=personal MUST end up
    with exactly one canonical-slug filter in the conditions list.

    The bug shape we close: personal-role callers had their kb_slugs
    stripped server-side, leaving scope=personal as "any chunk where
    user_id matches", which leaked test2 chunks. Now the canonical-slug
    filter is added by ``_scope_filter`` itself, downstream of any
    role-driven strip.
    """
    req = RetrieveRequest(
        query="wie is jantine?",
        org_id="o1",
        scope="personal",
        user_id="jantine-zitadel-sub",
        effective_role=role,
    )
    conditions = _scope_filter(req)
    slug_conditions = [
        c for c in conditions if isinstance(c, FieldCondition) and c.key == "kb_slug"
    ]
    assert len(slug_conditions) == 1, (
        f"expected exactly one kb_slug filter for role={role!r}, got {len(slug_conditions)}"
    )
    assert slug_conditions[0].match.value == personal_kb_slug("jantine-zitadel-sub"), (
        f"role={role!r}: expected canonical slug filter, got {slug_conditions[0].match.value!r}"
    )


def test_scope_personal_without_kb_slugs_still_narrows_to_canonical() -> None:
    """The personal-role kb_slugs strip leaves kb_slugs=None at the
    ``_scope_filter`` call site. Even then, the canonical narrowing
    must fire (it does not depend on req.kb_slugs)."""
    req = RetrieveRequest(
        query="q",
        org_id="o1",
        scope="personal",
        user_id="u1",
        effective_role="personal",
        kb_slugs=None,
    )
    conditions = _scope_filter(req)
    slug_conditions = [
        c for c in conditions if isinstance(c, FieldCondition) and c.key == "kb_slug"
    ]
    assert len(slug_conditions) == 1
    assert slug_conditions[0].match.value == "personal-u1"


def test_scope_personal_with_client_supplied_canonical_slugs_no_duplicate() -> None:
    """If the LiteLLM hook (post-PR-#715) also sends
    ``kb_slugs=["personal-u1"]`` as a redundant client-side filter, the
    conditions end up with TWO ``kb_slug`` FieldConditions — the
    server-side canonical narrowing (MatchValue) AND the client-supplied
    filter (MatchAny). Both target the same value; the search still
    works. Defense in depth.
    """
    req = RetrieveRequest(
        query="q",
        org_id="o1",
        scope="personal",
        user_id="u1",
        kb_slugs=["personal-u1"],
        effective_role="admin",  # admin role does not trigger strip
    )
    conditions = _scope_filter(req)
    slug_conditions = [
        c for c in conditions if isinstance(c, FieldCondition) and c.key == "kb_slug"
    ]
    assert len(slug_conditions) == 2, (
        f"expected canonical (MatchValue) + client (MatchAny) — got {len(slug_conditions)}"
    )
    # The canonical narrow is a MatchValue
    match_value = next(c for c in slug_conditions if hasattr(c.match, "value"))
    assert match_value.match.value == "personal-u1"
    # The client-supplied filter is a MatchAny
    match_any = next(c for c in slug_conditions if hasattr(c.match, "any"))
    assert match_any.match.any == ["personal-u1"]


def test_scope_personal_non_canonical_kb_slug_filter_still_appends_canonical() -> None:
    """A future caller (or wrong-headed PR) sets ``kb_slugs=["test2"]``
    together with ``scope=personal``. Server-side, the canonical
    narrowing MUST still apply — the resulting filter has both
    ``kb_slug=canonical`` (REQ-2) AND ``kb_slug=test2`` (client-supplied).
    Both must hold via AND → empty result set. User explicitly receives
    nothing rather than silently leaking test2.
    """
    req = RetrieveRequest(
        query="q",
        org_id="o1",
        scope="personal",
        user_id="u1",
        kb_slugs=["test2"],
        effective_role="admin",
    )
    conditions = _scope_filter(req)
    slug_conditions = [
        c for c in conditions if isinstance(c, FieldCondition) and c.key == "kb_slug"
    ]
    # One canonical (MatchValue), one client-supplied (MatchAny)
    match_value = next(c for c in slug_conditions if hasattr(c.match, "value"))
    assert match_value.match.value == "personal-u1"
    match_any = next(c for c in slug_conditions if hasattr(c.match, "any"))
    assert match_any.match.any == ["test2"]
