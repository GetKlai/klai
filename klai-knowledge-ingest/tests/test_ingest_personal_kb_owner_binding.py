"""SPEC-MCP-AUTH-001 follow-up — personal-KB owner-binding regression suite.

Audit-finding B2 (klai-security-audit 2026-05-07): user A authenticated
via OAuth could call POST /ingest/v1/document with body
``{user_id: A, kb_slug: "personal-B"}`` — identity-assertion only proves
A is a real user in the org, NOT that A owns ``personal-B``. Result was
silent corruption of B's personal-KB namespace; one regression in the
Qdrant retrieve-time user_id filter would turn this into a confidentiality
breach.

The route handler now guards: when ``kb_slug`` starts with ``personal-``,
it MUST equal ``f"personal-{user_id}"`` or the request is rejected 403
``personal_kb_owner_mismatch`` before any DB write.

Test pattern mirrors ``test_ingest_endpoints_identity_assertion.py`` —
identity is mocked OK so we exclusively exercise the new guard.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

# Reuse the same caller-service header gate as the sibling identity test.
_CALLER = {"X-Caller-Service": "portal-api"}


@pytest.fixture(autouse=True)
def _stub_embedder(monkeypatch):
    """Keep the real TEI client out of these guard tests.

    These cases assert the personal-KB owner guard, not embedding. Without
    the stub the request reaches ``embedder.embed``, TEI is unreachable in
    CI, and the 5-attempt jitter backoff (30s cap) sleeps ~75s per test
    before the assertion is ever evaluated — three tests here were the
    slowest in the whole suite for that reason alone.
    """

    async def _fake_embed(texts: list[str]) -> list[list[float]]:
        return [[0.0] * 8 for _ in texts]

    monkeypatch.setattr("knowledge_ingest.embedder.embed", _fake_embed)


@pytest.fixture()
def _identity_ok(monkeypatch):
    """Identity assertion passes through with the claimed org_id / user_id."""

    async def _ok(request, claimed_org_id, claimed_user_id=None):
        return claimed_org_id

    for module_path in [
        "knowledge_ingest.identity.assert_caller_identity",
        "knowledge_ingest.routes.ingest.assert_caller_identity",
        "knowledge_ingest.identity.assert_caller_identity_tenant_only",
        "knowledge_ingest.routes.ingest.assert_caller_identity_tenant_only",
    ]:
        monkeypatch.setattr(module_path, _ok)


# ---------------------------------------------------------------------------
# B2 cross-user write rejection
# ---------------------------------------------------------------------------


class TestPersonalKbOwnerBinding:
    """B2: ``personal-{X}`` writes by user Y MUST be rejected."""

    def test_user_a_writing_to_personal_b_returns_403(self, _identity_ok, client):
        """User A's request to personal-B namespace blocked at the route."""
        resp = client.post(
            "/ingest/v1/document",
            headers=_CALLER,
            json={
                "org_id": "org-1",
                "kb_slug": "personal-bob",
                "user_id": "alice",  # mismatched: alice ≠ bob
                "path": "stolen-namespace.md",
                "content": "this should not be persisted",
                "content_type": "text/markdown",
            },
        )
        assert resp.status_code == 403, resp.text
        assert "personal_kb_owner_mismatch" in resp.text

    def test_personal_kb_without_user_id_returns_403(self, _identity_ok, client):
        """Service-to-service caller (no user_id) cannot target personal-X.

        The connector path is service-to-service and never carries a
        ``user_id``. A connector trying to write to a personal-* slug is
        either a misconfiguration or a confused-deputy attack — fail
        closed regardless of intent.
        """
        resp = client.post(
            "/ingest/v1/document",
            headers=_CALLER,
            json={
                "org_id": "org-1",
                "kb_slug": "personal-anyone",
                # no user_id — tenant-only assertion path
                "path": "from-connector.md",
                "content": "...",
                "content_type": "text/markdown",
            },
        )
        assert resp.status_code == 403, resp.text
        assert "personal_kb_owner_mismatch" in resp.text

    def test_user_a_writing_to_own_personal_kb_passes_guard(self, _identity_ok, client, mock_pool):
        """Owner writing to their own personal-X passes the route-level guard.

        The downstream pipeline may still 4xx for unrelated reasons (no
        embedder in the test stub, etc.) — we just assert the guard does
        NOT itself raise the owner-mismatch 403.
        """
        mock_pool.fetchrow = AsyncMock(return_value=None)
        mock_pool.execute = AsyncMock(return_value=None)
        resp = client.post(
            "/ingest/v1/document",
            headers=_CALLER,
            json={
                "org_id": "org-1",
                "kb_slug": "personal-alice",
                "user_id": "alice",
                "path": "my-note.md",
                "content": "Hello world",
                "content_type": "text/markdown",
            },
        )
        # Either pass-through (downstream may itself 4xx for non-auth reasons),
        # but it must NOT be the owner-mismatch 403.
        assert "personal_kb_owner_mismatch" not in resp.text, (
            f"Owner-match write was wrongly rejected: {resp.text}"
        )

    def test_org_kb_write_unaffected_by_guard(self, _identity_ok, client, mock_pool):
        """Non-personal kb_slugs are not subject to the owner-binding guard."""
        mock_pool.fetchrow = AsyncMock(return_value=None)
        mock_pool.execute = AsyncMock(return_value=None)
        resp = client.post(
            "/ingest/v1/document",
            headers=_CALLER,
            json={
                "org_id": "org-1",
                "kb_slug": "team-handbook",  # not a personal- slug
                "user_id": "alice",
                "path": "intro.md",
                "content": "Hello",
                "content_type": "text/markdown",
            },
        )
        assert "personal_kb_owner_mismatch" not in resp.text, resp.text

    def test_plain_personal_slug_without_dash_unaffected(self, _identity_ok, client, mock_pool):
        """``kb_slug='personal'`` (no trailing dash) is a generic slug, not owner-bound.

        Anyone using the literal slug ``personal`` (no user-id suffix) bypasses
        the guard. This documents the expected matcher boundary: the guard
        only fires on the ``personal-`` PREFIX, not on the bare word.
        """
        mock_pool.fetchrow = AsyncMock(return_value=None)
        mock_pool.execute = AsyncMock(return_value=None)
        resp = client.post(
            "/ingest/v1/document",
            headers=_CALLER,
            json={
                "org_id": "org-1",
                "kb_slug": "personal",
                "user_id": "alice",
                "path": "x.md",
                "content": "y",
                "content_type": "text/markdown",
            },
        )
        assert "personal_kb_owner_mismatch" not in resp.text, resp.text
