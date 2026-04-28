"""SPEC-SEC-IDENTITY-ASSERT-001 REQ-2 tests for klai-knowledge-mcp.

These tests prove the knowledge-mcp tools refuse to forward upstream calls
unless the (X-User-ID, X-Org-ID, X-Org-Slug) tuple has been verified by
portal-api's /internal/identity/verify. Until REQ-2 lands the tools forward
caller-asserted identity verbatim — those calls would succeed today against
the unpatched code (this is the M1 + D1 chain in spec.md).

Acceptance coverage:
- AC-1: spoof attempt rejected (M1 + D1 closure)
- AC-7 partial: happy-path save_personal / save_org / save_to_docs still works
- REQ-2.3: outgoing X-User-ID / X-Org-ID to klai-docs come from VERIFIED identity
- REQ-2.6: missing X-Org-Slug rejects (no DEFAULT_ORG_SLUG fallback)
- REQ-2.6: claimed slug mismatch is rejected via portal-side org_slug_mismatch
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from klai_identity_assert import VerifyResult

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _make_ctx(headers: dict[str, str] | None = None) -> MagicMock:
    """Mock FastMCP Context with overridable request headers."""
    ctx = MagicMock()
    ctx.request_context.request.headers = headers or {}
    return ctx


@pytest.fixture(autouse=True)
def _patch_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set required environment variables for main.py import.

    PORTAL_API_URL / PORTAL_INTERNAL_SECRET are new in REQ-2 — without them
    the IdentityAsserter constructor raises at module load.
    """
    monkeypatch.setenv("KLAI_DOCS_API_BASE", "http://docs-app:3000")
    monkeypatch.setenv("DOCS_INTERNAL_SECRET", "docs-secret")
    monkeypatch.setenv("KNOWLEDGE_INGEST_URL", "http://knowledge-ingest:8000")
    monkeypatch.setenv("KNOWLEDGE_INGEST_SECRET", "test-secret")
    monkeypatch.setenv("PORTAL_API_URL", "http://portal-api:8010")
    monkeypatch.setenv("PORTAL_INTERNAL_SECRET", "portal-test-secret")


def _allow(
    *, user_id: str, org_id: str, org_slug: str = "acme", evidence: str = "jwt"
) -> VerifyResult:
    return VerifyResult.allow(user_id=user_id, org_id=org_id, org_slug=org_slug, evidence=evidence)  # type: ignore[arg-type]


def _spoof_headers(
    *, attacker_jwt: str | None, victim_user_id: str, victim_org_id: str
) -> dict[str, str]:
    """Headers a malicious LibreChat client could inject — JWT for caller A,
    claimed identity (X-User-ID / X-Org-ID) for victim B."""
    headers: dict[str, str] = {
        "x-user-id": victim_user_id,
        "x-org-id": victim_org_id,
        "x-org-slug": "victim-slug",
        "x-internal-secret": "test-secret",
    }
    if attacker_jwt is not None:
        headers["authorization"] = f"Bearer {attacker_jwt}"
    return headers


def _legit_headers(
    *, user_id: str, org_id: str, org_slug: str = "acme", jwt_value: str | None = "user-jwt"
) -> dict[str, str]:
    headers: dict[str, str] = {
        "x-user-id": user_id,
        "x-org-id": org_id,
        "x-org-slug": org_slug,
        "x-internal-secret": "test-secret",
    }
    if jwt_value is not None:
        headers["authorization"] = f"Bearer {jwt_value}"
    return headers


# ---------------------------------------------------------------------------
# AC-1 — spoof attempt rejected (M1 + D1 closure)
# ---------------------------------------------------------------------------


class TestSpoofRejection:
    """AC-1: LibreChat-injected X-User-ID / X-Org-ID must be cross-checked
    against the end-user JWT (or membership) before any upstream forwarding."""

    @pytest.mark.asyncio
    async def test_save_personal_with_jwt_mismatch_rejected(self) -> None:
        from main import save_personal_knowledge

        # Portal returns deny — JWT belongs to user_a but caller claims user_b.
        deny_jwt_mismatch = VerifyResult.deny("jwt_identity_mismatch")
        with (
            patch(
                "main._asserter.verify", new_callable=AsyncMock, return_value=deny_jwt_mismatch
            ) as verify_mock,
            patch("main._save_to_ingest", new_callable=AsyncMock, return_value=True) as ingest_mock,
        ):
            ctx = _make_ctx(
                _spoof_headers(
                    attacker_jwt="attacker-jwt",
                    victim_user_id="user-b-uuid",
                    victim_org_id="org-y-uuid",
                )
            )

            result = await save_personal_knowledge(
                title="Spoof attempt",
                content="ssn: 123",
                assertion_mode="factual",
                tags=["test"],
                ctx=ctx,
            )

        # Tool returns an error (not the success message) and never reaches
        # knowledge-ingest. Reason code MUST NOT leak to the MCP client.
        assert "Opgeslagen" not in result
        assert "jwt_identity_mismatch" not in result
        ingest_mock.assert_not_awaited()
        verify_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_save_personal_no_jwt_falls_back_to_membership_check(self) -> None:
        """REQ-2.5 (without retry, per Phase B decision): when LibreChat
        forwards no JWT, the MCP calls verify with bearer_jwt=None and the
        portal does a membership check. A spoof where the claimed user is
        in the claimed org but the caller is acting as a different user
        STILL passes the membership check — which is the documented gap.
        Knowledge-mcp's job is only to refuse calls that the verify denies.
        """
        from main import save_personal_knowledge

        # Portal denies (e.g. claimed user has no membership in claimed org).
        deny_no_membership = VerifyResult.deny("no_membership")
        with (
            patch(
                "main._asserter.verify", new_callable=AsyncMock, return_value=deny_no_membership
            ) as verify_mock,
            patch("main._save_to_ingest", new_callable=AsyncMock, return_value=True) as ingest_mock,
        ):
            ctx = _make_ctx(
                _spoof_headers(
                    attacker_jwt=None, victim_user_id="user-b-uuid", victim_org_id="org-y-uuid"
                )
            )

            result = await save_personal_knowledge(
                title="X",
                content="Y",
                assertion_mode="factual",
                tags=["t"],
                ctx=ctx,
            )

        assert "Opgeslagen" not in result
        assert "no_membership" not in result
        ingest_mock.assert_not_awaited()
        # Library called with bearer_jwt=None (Authorization absent).
        kwargs = verify_mock.call_args.kwargs
        assert kwargs["bearer_jwt"] is None


# ---------------------------------------------------------------------------
# REQ-2.3 — outgoing identity headers come from verified, not claimed
# ---------------------------------------------------------------------------


class TestVerifiedIdentityForwarded:
    @pytest.mark.asyncio
    async def test_save_to_ingest_receives_verified_identity(self) -> None:
        """REQ-2.1 / REQ-2.3: verified user_id and org_id are passed to
        ``_save_to_ingest`` — not the values lifted from incoming headers."""
        from main import save_personal_knowledge

        # Verifier returns canonical (user_id, org_id) values different from
        # whatever the headers asserted — proves we're sourcing from verified.
        verified = _allow(user_id="VERIFIED-USER", org_id="VERIFIED-ORG", org_slug="acme")

        with (
            patch("main._asserter.verify", new_callable=AsyncMock, return_value=verified),
            patch("main._save_to_ingest", new_callable=AsyncMock, return_value=True) as ingest_mock,
        ):
            # Headers carry different values — the verifier overrides.
            ctx = _make_ctx(
                {
                    "x-user-id": "header-user",
                    "x-org-id": "header-org",
                    "x-org-slug": "acme",
                    "x-internal-secret": "test-secret",
                    "authorization": "Bearer some-jwt",
                }
            )

            result = await save_personal_knowledge(
                title="Note",
                content="content",
                assertion_mode="factual",
                tags=["t"],
                ctx=ctx,
            )

        assert "Opgeslagen" in result
        ingest_mock.assert_awaited_once()
        kwargs = ingest_mock.call_args.kwargs
        assert kwargs["org_id"] == "VERIFIED-ORG"
        assert kwargs["user_id"] == "VERIFIED-USER"
        # Personal KB slug derived from verified user_id (not header).
        assert kwargs["kb_slug"] == "personal-VERIFIED-USER"

    @pytest.mark.asyncio
    async def test_save_to_docs_forwards_verified_identity_in_outgoing_headers(self) -> None:
        """REQ-2.3: outgoing klai-docs PUT carries verified X-User-ID / X-Org-ID."""
        from main import save_to_docs

        verified = _allow(user_id="VERIFIED-USER", org_id="VERIFIED-ORG", org_slug="acme")

        # Capture the outgoing headers from httpx PUT.
        captured: dict[str, dict[str, str]] = {}

        with (
            patch("main._asserter.verify", new_callable=AsyncMock, return_value=verified),
            patch("main.httpx.AsyncClient") as mock_client_cls,
        ):
            mock_kbs_resp = MagicMock()
            mock_kbs_resp.status_code = 200
            mock_kbs_resp.json.return_value = [{"slug": "docs", "name": "Docs KB"}]
            mock_kbs_resp.text = ""

            mock_put_resp = MagicMock()
            mock_put_resp.status_code = 200
            mock_put_resp.text = "ok"

            mock_client = AsyncMock()

            async def fake_get(url: str, headers: dict[str, str] | None = None) -> MagicMock:
                captured.setdefault("get_headers", headers or {})
                return mock_kbs_resp

            async def fake_put(
                url: str, json: dict | None = None, headers: dict[str, str] | None = None
            ) -> MagicMock:
                captured["put_headers"] = headers or {}
                return mock_put_resp

            mock_client.get = AsyncMock(side_effect=fake_get)
            mock_client.put = AsyncMock(side_effect=fake_put)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            ctx = _make_ctx(
                {
                    "x-user-id": "header-user-attacker",
                    "x-org-id": "header-org-attacker",
                    "x-org-slug": "acme",
                    "x-internal-secret": "test-secret",
                    "authorization": "Bearer some-jwt",
                }
            )

            result = await save_to_docs(
                title="A page",
                content="content",
                ctx=ctx,
                kb_name="docs",
                page_path="inbox/note",
            )

        assert "Error" not in result
        # Outgoing headers carry VERIFIED values, never the LibreChat-asserted
        # header values. This is the M1 -> D1 closure.
        put_headers = captured["put_headers"]
        assert put_headers["X-User-ID"] == "VERIFIED-USER"
        assert put_headers["X-Org-ID"] == "VERIFIED-ORG"
        assert put_headers["X-Org-ID"] != "header-org-attacker"


# ---------------------------------------------------------------------------
# REQ-2.6 — DEFAULT_ORG_SLUG fallback removed + slug mismatch
# ---------------------------------------------------------------------------


class TestOrgSlugCheck:
    @pytest.mark.asyncio
    async def test_missing_org_slug_header_rejected_no_default_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """REQ-2.6: silent DEFAULT_ORG_SLUG fallback is removed. A missing
        X-Org-Slug header must error rather than impersonate a default org."""
        # Even if DEFAULT_ORG_SLUG is set, the missing header must reject.
        monkeypatch.setenv("DEFAULT_ORG_SLUG", "default-tenant")

        from main import save_personal_knowledge

        with (
            patch("main._asserter.verify", new_callable=AsyncMock) as verify_mock,
            patch("main._save_to_ingest", new_callable=AsyncMock, return_value=True) as ingest_mock,
        ):
            ctx = _make_ctx(
                {
                    "x-user-id": "u-1",
                    "x-org-id": "o-1",
                    # NO x-org-slug
                    "x-internal-secret": "test-secret",
                }
            )

            result = await save_personal_knowledge(
                title="Test",
                content="x",
                assertion_mode="factual",
                tags=["t"],
                ctx=ctx,
            )

        assert "Error" in result
        assert "X-Org-Slug" in result or "org_slug" in result.lower()
        # Identity verify must NOT run if we can't even produce a complete
        # claimed-identity tuple — fail loud, fast.
        verify_mock.assert_not_awaited()
        ingest_mock.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_org_slug_mismatch_from_portal_rejected(self) -> None:
        """REQ-2.6: portal returns 403 + reason='org_slug_mismatch' when the
        LibreChat-asserted X-Org-Slug doesn't match the canonical slug. The
        MCP must propagate the rejection to the client without forwarding."""
        from main import save_personal_knowledge

        deny_slug = VerifyResult.deny("org_slug_mismatch")
        with (
            patch(
                "main._asserter.verify", new_callable=AsyncMock, return_value=deny_slug
            ) as verify_mock,
            patch("main._save_to_ingest", new_callable=AsyncMock, return_value=True) as ingest_mock,
        ):
            ctx = _make_ctx(_legit_headers(user_id="u-1", org_id="o-1", org_slug="impostor-slug"))

            result = await save_personal_knowledge(
                title="x",
                content="y",
                assertion_mode="factual",
                tags=["t"],
                ctx=ctx,
            )

        assert "Opgeslagen" not in result
        # Reason code stays in logs, never echoed to the MCP client.
        assert "org_slug_mismatch" not in result
        ingest_mock.assert_not_awaited()
        # Verifier was called with the asserted slug as claimed_org_slug.
        kwargs = verify_mock.call_args.kwargs
        assert kwargs["claimed_org_slug"] == "impostor-slug"


# ---------------------------------------------------------------------------
# AC-7 partial — happy paths still work after migration
# ---------------------------------------------------------------------------


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_save_personal_knowledge_happy_path(self) -> None:
        from main import save_personal_knowledge

        verified = _allow(user_id="u-1", org_id="o-1", org_slug="acme", evidence="jwt")
        with (
            patch("main._asserter.verify", new_callable=AsyncMock, return_value=verified),
            patch("main._save_to_ingest", new_callable=AsyncMock, return_value=True) as ingest_mock,
        ):
            ctx = _make_ctx(_legit_headers(user_id="u-1", org_id="o-1", org_slug="acme"))

            result = await save_personal_knowledge(
                title="My note",
                content="hello",
                assertion_mode="factual",
                tags=["t"],
                ctx=ctx,
            )

        assert "Opgeslagen" in result
        ingest_mock.assert_awaited_once()
        kwargs = ingest_mock.call_args.kwargs
        assert kwargs["user_id"] == "u-1"
        assert kwargs["org_id"] == "o-1"

    @pytest.mark.asyncio
    async def test_save_org_knowledge_happy_path(self) -> None:
        from main import save_org_knowledge

        verified = _allow(user_id="u-1", org_id="o-1", org_slug="acme", evidence="membership")
        with (
            patch("main._asserter.verify", new_callable=AsyncMock, return_value=verified),
            patch("main._save_to_ingest", new_callable=AsyncMock, return_value=True) as ingest_mock,
        ):
            ctx = _make_ctx(
                _legit_headers(user_id="u-1", org_id="o-1", org_slug="acme", jwt_value=None)
            )

            result = await save_org_knowledge(
                title="Team notes",
                content="hello team",
                assertion_mode="factual",
                tags=["t"],
                ctx=ctx,
            )

        assert "Opgeslagen" in result
        ingest_mock.assert_awaited_once()
        # Org KB carries verified org_id, NOT user_id.
        kwargs = ingest_mock.call_args.kwargs
        assert kwargs["org_id"] == "o-1"
        assert kwargs.get("user_id") is None
