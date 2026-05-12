"""Unit tests for app.services.identity_verifier.verify_identity_claim.

Service-layer tests; the HTTP endpoint and Redis cache are tested separately
in test_internal_identity_verify.py. JWT validation is mocked via a fake
``JwksResolver``; DB is mocked via ``AsyncMock`` on the ``execute`` method.

SPEC-SEC-IDENTITY-ASSERT-001 REQ-1 acceptance criteria coverage:
- AC-5a: verified JWT + matching sub + active membership → allow with evidence='jwt'
- AC-5b: JWT sub != claimed_user_id → deny with reason='jwt_identity_mismatch'
- AC-5c: bearer_jwt=None + active membership → allow with evidence='membership'
- AC-5d: bearer_jwt=None + no membership → deny with reason='no_membership'
- REQ-1.2: unknown caller_service → deny with reason='unknown_caller_service'
- REQ-1.8: invalid JWT signature → deny with reason='invalid_jwt' (no fallthrough)

SPEC-SEC-IDENTITY-ASSERT-002 (membership-authoritative identity):
- A1: JWT lacks resourceowner claim + matching sub + active membership → allow
- A6: JWT carries an UNMATCHING resourceowner value + matching sub + membership → allow (claim ignored)
- A9: Multi-org user — same JWT can authorise on either of two orgs they belong to
- A2: JWT valid sub + claimed_org_id without active membership → deny no_membership

REQ-2.6 (Phase B):
- claimed_org_slug provided + matches canonical → allow includes canonical org_slug
- claimed_org_slug provided + mismatch → deny with reason='org_slug_mismatch'
- claimed_org_slug=None → allow still includes canonical org_slug for cache hit re-checking
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest

from app.services.identity_verifier import (
    KNOWN_CALLER_SERVICES,
    VerifyDecision,
    verify_identity_claim,
    verify_tenant_claim,
)


class _FakeSigningKey:
    """Minimal stand-in for jwt.api_jwk.PyJWK.key — only the attribute matters."""

    key = "fake-signing-key"


class _FakeJwksResolver:
    """Test-only resolver that returns a constant signing key.

    The test provides the *signed* JWT; the resolver merely yields the same
    HMAC secret used to sign it. PyJWT will validate or reject the signature
    at decode time — we drive both paths via the JWT contents.
    """

    def __init__(self, signing_key: Any = _FakeSigningKey()) -> None:
        self._signing_key = signing_key

    def get_signing_key_from_jwt(self, _token: str) -> Any:
        return self._signing_key


@pytest.fixture
def real_jwks_resolver() -> _FakeJwksResolver:
    """Resolver that returns an HMAC-style key compatible with HS256 signing.

    For test ergonomics we sign tokens with HS256 (jwt.encode) and decode them
    with the same secret. ``identity_verifier`` allows ``RS256`` only — so we
    need the resolver to return a string key the way the verifier expects.
    """

    return _FakeJwksResolver(signing_key="hmac-secret")


@pytest.fixture
def mock_db() -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock()
    return db


def _signed_jwt(*, sub: str, resourceowner: str, secret: str = "hmac-secret", **extra: Any) -> str:
    """Sign a fake Zitadel JWT with HS256 for tests.

    The verifier configures jwt.decode for RS256 only — so any HS256 token
    fails signature validation, which is exactly what AC-5b's 'invalid JWT'
    branch needs. To exercise the *valid* path we monkey-patch
    ``jwt.decode`` directly in tests rather than wrestling with a real RSA key.
    """

    payload = {
        "sub": sub,
        "iss": "https://zitadel.example.com",
        "exp": 9999999999,
        "urn:zitadel:iam:user:resourceowner:id": resourceowner,
        **extra,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


class TestUnknownCallerService:
    """REQ-1.2: caller_service not in allowlist → deny with stable reason."""

    async def test_deny_for_unknown_service(self, mock_db: AsyncMock) -> None:
        decision = await verify_identity_claim(
            db=mock_db,
            jwks_resolver=_FakeJwksResolver(),
            caller_service="not-a-real-service",
            claimed_user_id="u-1",
            claimed_org_id="o-1",
            bearer_jwt=None,
        )

        assert decision.verified is False
        assert decision.reason == "unknown_caller_service"
        assert decision.evidence is None
        mock_db.execute.assert_not_called()

    def test_known_callers_include_required_set(self) -> None:
        # Mirrors the library-side test; an asymmetric change between the two
        # sides would leave one consumer fail-closed and the other not.
        for required in ("knowledge-mcp", "scribe", "retrieval-api", "connector", "mailer"):
            assert required in KNOWN_CALLER_SERVICES


class TestJwtPath:
    """REQ-1.3 / REQ-1.8: JWT validation, identity mismatch, invalid JWT."""

    async def test_allow_when_jwt_sub_and_resourceowner_match(
        self, mock_db: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.services.identity_verifier.jwt.decode",
            lambda *_args, **_kwargs: {
                "sub": "u-1",
                "iss": "https://zitadel.example.com",
                "exp": 9999999999,
                "urn:zitadel:iam:user:resourceowner:id": "o-1",
            },
        )
        # JWT path now resolves the canonical org_slug for the verified org
        # (REQ-2.6) so cache hits can re-check the slug without a DB round
        # trip. One DB call: PortalOrg lookup keyed on zitadel_org_id.
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value="acme")
        mock_db.execute.return_value = mock_result

        decision = await verify_identity_claim(
            db=mock_db,
            jwks_resolver=_FakeJwksResolver(),
            caller_service="scribe",
            claimed_user_id="u-1",
            claimed_org_id="o-1",
            bearer_jwt="any.jwt.value",
        )

        assert decision.verified is True
        assert decision.evidence == "jwt"
        assert decision.user_id == "u-1"
        assert decision.org_id == "o-1"
        assert decision.org_slug == "acme"
        # JWT path consults DB exactly once for the slug lookup.
        mock_db.execute.assert_awaited_once()

    async def test_deny_when_jwt_sub_does_not_match_claimed_user(
        self, mock_db: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.services.identity_verifier.jwt.decode",
            lambda *_args, **_kwargs: {
                "sub": "u-1",  # JWT belongs to user u-1
                "iss": "https://zitadel.example.com",
                "exp": 9999999999,
                "urn:zitadel:iam:user:resourceowner:id": "o-1",
            },
        )

        # Caller claims to be user u-2 with the SAME org — JWT mismatch.
        decision = await verify_identity_claim(
            db=mock_db,
            jwks_resolver=_FakeJwksResolver(),
            caller_service="scribe",
            claimed_user_id="u-2",
            claimed_org_id="o-1",
            bearer_jwt="any.jwt.value",
        )

        assert decision.verified is False
        assert decision.reason == "jwt_identity_mismatch"
        assert decision.evidence is None

    async def test_deny_with_invalid_jwt_does_not_fall_back_to_membership(
        self, mock_db: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # REQ-1.8: an invalid JWT is a STRICTLY STRONGER deny signal than
        # an absent JWT. Must NOT fall through to the membership path.
        def _raise(*_args: Any, **_kwargs: Any) -> None:
            raise jwt.ExpiredSignatureError("token expired")

        monkeypatch.setattr("app.services.identity_verifier.jwt.decode", _raise)

        # Even if the membership lookup *would* succeed, REQ-1.8 forbids
        # falling through to it — assert that DB is NEVER called.
        decision = await verify_identity_claim(
            db=mock_db,
            jwks_resolver=_FakeJwksResolver(),
            caller_service="scribe",
            claimed_user_id="u-1",
            claimed_org_id="o-1",
            bearer_jwt="expired.jwt.token",
        )

        assert decision.verified is False
        assert decision.reason == "invalid_jwt"
        mock_db.execute.assert_not_called()

    async def test_deny_when_jwt_sub_has_wrong_type(self, mock_db: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> None:
        # Defensive: malformed JWT with sub not a string.
        # SPEC-SEC-IDENTITY-ASSERT-002 REQ-1.2: resourceowner is no longer
        # type-checked or read; only sub matters for invalid_jwt.
        monkeypatch.setattr(
            "app.services.identity_verifier.jwt.decode",
            lambda *_args, **_kwargs: {
                "sub": 12345,  # int, not str
                "iss": "https://zitadel.example.com",
                "exp": 9999999999,
            },
        )

        decision = await verify_identity_claim(
            db=mock_db,
            jwks_resolver=_FakeJwksResolver(),
            caller_service="scribe",
            claimed_user_id="u-1",
            claimed_org_id="o-1",
            bearer_jwt="any.jwt.value",
        )

        assert decision.verified is False
        assert decision.reason == "invalid_jwt"
        # No DB call should have happened — invalid JWT short-circuits.
        mock_db.execute.assert_not_called()

    async def test_allow_when_jwt_lacks_resourceowner_claim(
        self, mock_db: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # SPEC-SEC-IDENTITY-ASSERT-002 A1: a JWT that does NOT carry the
        # urn:zitadel:iam:user:resourceowner:id claim (the actual production
        # state for Klai's BFF — scope set never requested it) MUST still
        # allow when sub matches and membership exists.
        monkeypatch.setattr(
            "app.services.identity_verifier.jwt.decode",
            lambda *_args, **_kwargs: {
                "sub": "u-1",
                "iss": "https://zitadel.example.com",
                "exp": 9999999999,
                # NO urn:zitadel:iam:user:resourceowner:id key.
            },
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value="acme")
        mock_db.execute.return_value = mock_result

        decision = await verify_identity_claim(
            db=mock_db,
            jwks_resolver=_FakeJwksResolver(),
            caller_service="scribe",
            claimed_user_id="u-1",
            claimed_org_id="o-1",
            bearer_jwt="any.jwt.value",
        )

        assert decision.verified is True
        assert decision.evidence == "jwt"
        assert decision.user_id == "u-1"
        assert decision.org_id == "o-1"
        assert decision.org_slug == "acme"

    async def test_jwt_resourceowner_value_is_ignored_when_present(
        self, mock_db: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # SPEC-SEC-IDENTITY-ASSERT-002 A6: even when a JWT happens to carry
        # the resourceowner claim with a value DIFFERENT from claimed_org_id,
        # the verifier must IGNORE it. Authoritative org-resolution is
        # portal_users membership, never the JWT-side claim.
        monkeypatch.setattr(
            "app.services.identity_verifier.jwt.decode",
            lambda *_args, **_kwargs: {
                "sub": "u-1",
                "iss": "https://zitadel.example.com",
                "exp": 9999999999,
                # Claim present but with a value that does NOT match claimed_org_id.
                # Under v1 SPEC this would have triggered jwt_identity_mismatch.
                # Under v2 SPEC the claim is ignored entirely.
                "urn:zitadel:iam:user:resourceowner:id": "some-other-org",
            },
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value="acme")
        mock_db.execute.return_value = mock_result

        decision = await verify_identity_claim(
            db=mock_db,
            jwks_resolver=_FakeJwksResolver(),
            caller_service="scribe",
            claimed_user_id="u-1",
            claimed_org_id="o-1",  # NOT equal to JWT's resourceowner
            bearer_jwt="any.jwt.value",
        )

        assert decision.verified is True
        assert decision.evidence == "jwt"
        assert decision.org_slug == "acme"

    async def test_multi_org_user_can_authorise_on_either_membership(
        self, mock_db: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # SPEC-SEC-IDENTITY-ASSERT-002 A9: a user with active memberships in
        # multiple orgs (data-model already supports this via portal_users
        # row-per-org) MUST be authorisable on each of those orgs with the
        # same JWT. The v1 SPEC's resourceowner equality blocked this for
        # non-primary orgs.
        monkeypatch.setattr(
            "app.services.identity_verifier.jwt.decode",
            lambda *_args, **_kwargs: {
                "sub": "u-1",
                "iss": "https://zitadel.example.com",
                "exp": 9999999999,
            },
        )
        # First call: claim org-A → membership returns "voys"
        # Second call: claim org-B → membership returns "acme"
        mock_result_voys = MagicMock()
        mock_result_voys.scalar_one_or_none = MagicMock(return_value="voys")
        mock_result_acme = MagicMock()
        mock_result_acme.scalar_one_or_none = MagicMock(return_value="acme")
        mock_db.execute.side_effect = [mock_result_voys, mock_result_acme]

        first = await verify_identity_claim(
            db=mock_db,
            jwks_resolver=_FakeJwksResolver(),
            caller_service="scribe",
            claimed_user_id="u-1",
            claimed_org_id="org-A",
            bearer_jwt="any.jwt.value",
        )
        second = await verify_identity_claim(
            db=mock_db,
            jwks_resolver=_FakeJwksResolver(),
            caller_service="scribe",
            claimed_user_id="u-1",
            claimed_org_id="org-B",
            bearer_jwt="any.jwt.value",
        )

        assert first.verified is True
        assert first.org_id == "org-A"
        assert first.org_slug == "voys"
        assert second.verified is True
        assert second.org_id == "org-B"
        assert second.org_slug == "acme"

    async def test_deny_when_jwt_valid_but_no_active_membership(
        self, mock_db: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # SPEC-SEC-IDENTITY-ASSERT-002 A2: JWT signature valid, sub matches,
        # but the user has NO active portal_users row for the claimed org →
        # deny with reason='no_membership'. (Replaces the v1 SPEC's
        # 'jwt_identity_mismatch' for cross-org JWTs — same outcome class,
        # but the membership lookup is now the authority.)
        monkeypatch.setattr(
            "app.services.identity_verifier.jwt.decode",
            lambda *_args, **_kwargs: {
                "sub": "u-1",
                "iss": "https://zitadel.example.com",
                "exp": 9999999999,
            },
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_db.execute.return_value = mock_result

        decision = await verify_identity_claim(
            db=mock_db,
            jwks_resolver=_FakeJwksResolver(),
            caller_service="scribe",
            claimed_user_id="u-1",
            claimed_org_id="o-2",  # user has no membership here
            bearer_jwt="any.jwt.value",
        )

        assert decision.verified is False
        assert decision.reason == "no_membership"


class TestMembershipPath:
    """REQ-1.4: bearer_jwt=None → membership lookup."""

    async def test_allow_when_active_membership_exists(self, mock_db: AsyncMock) -> None:
        # _resolve_active_membership_org_slug returns the canonical slug as a
        # single combined query — a hit means both "active membership exists"
        # and "this is the slug to return".
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value="acme")
        mock_db.execute.return_value = mock_result

        decision = await verify_identity_claim(
            db=mock_db,
            jwks_resolver=_FakeJwksResolver(),
            caller_service="scribe",
            claimed_user_id="u-1",
            claimed_org_id="o-1",
            bearer_jwt=None,
        )

        assert decision.verified is True
        assert decision.evidence == "membership"
        assert decision.user_id == "u-1"
        assert decision.org_id == "o-1"
        assert decision.org_slug == "acme"
        mock_db.execute.assert_awaited_once()

    async def test_deny_when_membership_lookup_returns_none(self, mock_db: AsyncMock) -> None:
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_db.execute.return_value = mock_result

        decision = await verify_identity_claim(
            db=mock_db,
            jwks_resolver=_FakeJwksResolver(),
            caller_service="scribe",
            claimed_user_id="u-1",
            claimed_org_id="o-1",
            bearer_jwt=None,
        )

        assert decision.verified is False
        assert decision.reason == "no_membership"
        assert decision.evidence is None


class TestVerifyDecisionDataclass:
    """Frozen-dataclass invariants on the service-layer result type."""

    def test_allow_factory_populates_identity(self) -> None:
        decision = VerifyDecision.allow(user_id="u-1", org_id="o-1", org_slug="acme", evidence="jwt")
        assert decision.verified is True
        assert decision.user_id == "u-1"
        assert decision.org_slug == "acme"
        assert decision.evidence == "jwt"
        assert decision.reason is None

    def test_deny_factory_clears_identity(self) -> None:
        decision = VerifyDecision.deny("no_membership")
        assert decision.verified is False
        assert decision.reason == "no_membership"
        assert decision.user_id is None
        assert decision.org_slug is None
        assert decision.evidence is None


class TestOrgSlugCheck:
    """REQ-2.6: claimed_org_slug must match canonical portal_orgs.slug."""

    async def test_jwt_path_returns_canonical_slug_when_no_claim(
        self, mock_db: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # No claimed_org_slug → still resolves canonical and returns it on
        # the VerifyDecision so cache hits can re-check the slug.
        monkeypatch.setattr(
            "app.services.identity_verifier.jwt.decode",
            lambda *_args, **_kwargs: {
                "sub": "u-1",
                "iss": "https://zitadel.example.com",
                "exp": 9999999999,
                "urn:zitadel:iam:user:resourceowner:id": "o-1",
            },
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value="canonical-slug")
        mock_db.execute.return_value = mock_result

        decision = await verify_identity_claim(
            db=mock_db,
            jwks_resolver=_FakeJwksResolver(),
            caller_service="scribe",
            claimed_user_id="u-1",
            claimed_org_id="o-1",
            bearer_jwt="any.jwt.value",
            claimed_org_slug=None,
        )

        assert decision.verified is True
        assert decision.org_slug == "canonical-slug"

    async def test_jwt_path_allows_when_claimed_slug_matches(
        self, mock_db: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "app.services.identity_verifier.jwt.decode",
            lambda *_args, **_kwargs: {
                "sub": "u-1",
                "iss": "https://zitadel.example.com",
                "exp": 9999999999,
                "urn:zitadel:iam:user:resourceowner:id": "o-1",
            },
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value="acme")
        mock_db.execute.return_value = mock_result

        decision = await verify_identity_claim(
            db=mock_db,
            jwks_resolver=_FakeJwksResolver(),
            caller_service="knowledge-mcp",
            claimed_user_id="u-1",
            claimed_org_id="o-1",
            bearer_jwt="any.jwt.value",
            claimed_org_slug="acme",
        )

        assert decision.verified is True
        assert decision.org_slug == "acme"

    async def test_jwt_path_denies_on_slug_mismatch(self, mock_db: AsyncMock, monkeypatch: pytest.MonkeyPatch) -> None:
        # JWT verifies user-org binding correctly, but the LibreChat-asserted
        # X-Org-Slug names a different org's slug — REQ-2.6 says reject.
        monkeypatch.setattr(
            "app.services.identity_verifier.jwt.decode",
            lambda *_args, **_kwargs: {
                "sub": "u-1",
                "iss": "https://zitadel.example.com",
                "exp": 9999999999,
                "urn:zitadel:iam:user:resourceowner:id": "o-1",
            },
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value="acme")  # canonical for o-1
        mock_db.execute.return_value = mock_result

        decision = await verify_identity_claim(
            db=mock_db,
            jwks_resolver=_FakeJwksResolver(),
            caller_service="knowledge-mcp",
            claimed_user_id="u-1",
            claimed_org_id="o-1",
            bearer_jwt="any.jwt.value",
            claimed_org_slug="impostor-slug",
        )

        assert decision.verified is False
        assert decision.reason == "org_slug_mismatch"
        assert decision.org_slug is None

    async def test_jwt_path_denies_when_org_row_missing(
        self, mock_db: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Sync drift edge case: JWT validates but portal_orgs has no row for
        # the resourceowner. Fail closed as no_membership (the user has no
        # provable entitlement we can vouch for).
        monkeypatch.setattr(
            "app.services.identity_verifier.jwt.decode",
            lambda *_args, **_kwargs: {
                "sub": "u-1",
                "iss": "https://zitadel.example.com",
                "exp": 9999999999,
                "urn:zitadel:iam:user:resourceowner:id": "o-1",
            },
        )
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_db.execute.return_value = mock_result

        decision = await verify_identity_claim(
            db=mock_db,
            jwks_resolver=_FakeJwksResolver(),
            caller_service="scribe",
            claimed_user_id="u-1",
            claimed_org_id="o-1",
            bearer_jwt="any.jwt.value",
        )

        assert decision.verified is False
        assert decision.reason == "no_membership"

    async def test_membership_path_denies_on_slug_mismatch(self, mock_db: AsyncMock) -> None:
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value="acme")
        mock_db.execute.return_value = mock_result

        decision = await verify_identity_claim(
            db=mock_db,
            jwks_resolver=_FakeJwksResolver(),
            caller_service="knowledge-mcp",
            claimed_user_id="u-1",
            claimed_org_id="o-1",
            bearer_jwt=None,
            claimed_org_slug="impostor-slug",
        )

        assert decision.verified is False
        assert decision.reason == "org_slug_mismatch"

    async def test_membership_path_allows_when_claimed_slug_matches(self, mock_db: AsyncMock) -> None:
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value="acme")
        mock_db.execute.return_value = mock_result

        decision = await verify_identity_claim(
            db=mock_db,
            jwks_resolver=_FakeJwksResolver(),
            caller_service="knowledge-mcp",
            claimed_user_id="u-1",
            claimed_org_id="o-1",
            bearer_jwt=None,
            claimed_org_slug="acme",
        )

        assert decision.verified is True
        assert decision.org_slug == "acme"
        assert decision.evidence == "membership"


class TestPartnerKeyPath:
    """F2 fix-forward (retrieval coupling audit 2026-05-06):
    `partner:<key_id>` claims are verified against partner_api_keys + portal_orgs.

    Tests pin the security contract:

    1. Allow only when key exists AND key.org_id maps to claimed_zitadel_org_id.
    2. Deny on missing key, malformed key, soft-deleted org, or org mismatch.
    3. Restricted to caller_service="portal-api" (only that service mints
       partner identities) — defense against cross-service prefix abuse.
    4. Bearer JWT + partner: prefix → invalid_jwt (defensive guard).
    5. DB errors fail closed to partner_key_not_found.
    """

    async def test_allow_when_key_exists_and_org_matches(self, mock_db: AsyncMock) -> None:
        # _resolve_partner_key_org_slug runs `result.one_or_none()` and gets
        # a (zitadel_org_id, slug) tuple. Mock that.
        mock_result = MagicMock()
        mock_result.one_or_none = MagicMock(return_value=("zitadel-org-acme", "acme"))
        mock_db.execute.return_value = mock_result

        decision = await verify_identity_claim(
            db=mock_db,
            jwks_resolver=_FakeJwksResolver(),
            caller_service="portal-api",
            claimed_user_id="partner:11111111-1111-1111-1111-111111111111",
            claimed_org_id="zitadel-org-acme",
            bearer_jwt=None,
        )

        assert decision.verified is True
        assert decision.evidence == "partner_key"
        assert decision.user_id == "partner:11111111-1111-1111-1111-111111111111"
        assert decision.org_id == "zitadel-org-acme"
        assert decision.org_slug == "acme"

    async def test_deny_when_key_not_found(self, mock_db: AsyncMock) -> None:
        mock_result = MagicMock()
        mock_result.one_or_none = MagicMock(return_value=None)
        mock_db.execute.return_value = mock_result

        decision = await verify_identity_claim(
            db=mock_db,
            jwks_resolver=_FakeJwksResolver(),
            caller_service="portal-api",
            claimed_user_id="partner:22222222-2222-2222-2222-222222222222",
            claimed_org_id="zitadel-org-acme",
            bearer_jwt=None,
        )

        assert decision.verified is False
        assert decision.reason == "partner_key_not_found"
        assert decision.evidence is None
        assert decision.org_id is None

    async def test_deny_on_org_mismatch(self, mock_db: AsyncMock) -> None:
        # Real key exists but its owning org's Zitadel id does not match the
        # claim — the shape of a deliberate cross-tenant probe.
        mock_result = MagicMock()
        mock_result.one_or_none = MagicMock(return_value=("zitadel-org-actual", "actual"))
        mock_db.execute.return_value = mock_result

        decision = await verify_identity_claim(
            db=mock_db,
            jwks_resolver=_FakeJwksResolver(),
            caller_service="portal-api",
            claimed_user_id="partner:11111111-1111-1111-1111-111111111111",
            claimed_org_id="zitadel-org-victim",  # forged claim
            bearer_jwt=None,
        )

        assert decision.verified is False
        assert decision.reason == "partner_key_org_mismatch"

    async def test_deny_when_caller_service_not_portal_api(self, mock_db: AsyncMock) -> None:
        # Bypass-prevention: only portal-api mints partner identities.
        # Other callers presenting a `partner:` prefix are denied without
        # ever hitting partner_api_keys — short-circuits would-be probes.
        decision = await verify_identity_claim(
            db=mock_db,
            jwks_resolver=_FakeJwksResolver(),
            caller_service="knowledge-mcp",  # NOT portal-api
            claimed_user_id="partner:11111111-1111-1111-1111-111111111111",
            claimed_org_id="zitadel-org-acme",
            bearer_jwt=None,
        )

        assert decision.verified is False
        assert decision.reason == "partner_key_not_found"
        # No DB call should have happened on the gating reject.
        mock_db.execute.assert_not_called()

    async def test_deny_when_partner_key_id_not_uuid(self, mock_db: AsyncMock) -> None:
        """2026-05-12 HOTFIX guard: a partner_key_id that is not a UUID (e.g.
        the widget-id ``wgt_<hex>`` accidentally forwarded as a synthetic
        partner claim) MUST be rejected pre-DB with ``partner_key_not_found``.
        Otherwise asyncpg raises DataError, SQLAlchemy wraps it as
        DBAPIError (not DataError), the narrow except is bypassed, the
        endpoint 5xx's, and the SDK collapses to ``portal_unreachable`` —
        masking every widget chat call as a generic "Er ging iets mis"
        outage. Pre-check is fail-fast + fail-safe.
        """
        decision = await verify_identity_claim(
            db=mock_db,
            jwks_resolver=_FakeJwksResolver(),
            caller_service="portal-api",
            claimed_user_id="partner:wgt_47b8c9c46d10b17c527923e0a5454bef3285b71f",
            claimed_org_id="zitadel-org-acme",
            bearer_jwt=None,
        )

        assert decision.verified is False
        assert decision.reason == "partner_key_not_found"
        # No DB call must have happened on the gating reject.
        mock_db.execute.assert_not_called()

    async def test_deny_when_malformed_key_length(self, mock_db: AsyncMock) -> None:
        # Fast reject: avoid forwarding garbage into asyncpg's UUID parser.
        decision = await verify_identity_claim(
            db=mock_db,
            jwks_resolver=_FakeJwksResolver(),
            caller_service="portal-api",
            claimed_user_id="partner:" + "x" * 65,
            claimed_org_id="zitadel-org-acme",
            bearer_jwt=None,
        )

        assert decision.verified is False
        assert decision.reason == "partner_key_not_found"
        mock_db.execute.assert_not_called()

    async def test_deny_when_partner_prefix_with_bearer_jwt(self, mock_db: AsyncMock) -> None:
        # Defensive guard: partner credentials are the partner-key, never a
        # bearer JWT. Mixing the two indicates a malformed call.
        decision = await verify_identity_claim(
            db=mock_db,
            jwks_resolver=_FakeJwksResolver(),
            caller_service="portal-api",
            claimed_user_id="partner:11111111-1111-1111-1111-111111111111",
            claimed_org_id="zitadel-org-acme",
            bearer_jwt="some.jwt.value",
        )

        assert decision.verified is False
        assert decision.reason == "invalid_jwt"
        mock_db.execute.assert_not_called()

    async def test_deny_on_data_error_returns_not_found(self, mock_db: AsyncMock) -> None:
        """Malformed UUID input surfaces as ``sqlalchemy.exc.DataError``;
        treat as ``partner_key_not_found`` rather than leaking internal
        error state to the consumer."""
        from sqlalchemy.exc import DataError

        # The DataError constructor needs (statement, params, orig). For a
        # test we only care that the exception type matches.
        mock_db.execute = AsyncMock(
            side_effect=DataError(
                "INSERT INTO ...",
                {},
                Exception("invalid input syntax for type uuid: 'not-a-uuid'"),
            )
        )

        decision = await verify_identity_claim(
            db=mock_db,
            jwks_resolver=_FakeJwksResolver(),
            caller_service="portal-api",
            claimed_user_id="partner:33333333-3333-3333-3333-333333333333",
            claimed_org_id="zitadel-org-acme",
            bearer_jwt=None,
        )

        assert decision.verified is False
        assert decision.reason == "partner_key_not_found"

    async def test_real_db_outage_propagates(self, mock_db: AsyncMock) -> None:
        """A real DB outage (connection refused, OperationalError) MUST
        propagate up to the endpoint layer so it returns 503
        ``cache_unavailable`` — not silently masquerade as a partner-key
        rejection.

        Polish guard added 2026-05-06: the previous bare ``except Exception``
        in ``_resolve_partner_key_org_slug`` was tightened to ``DataError``
        only. Without this test, a future regression that re-broadens the
        catch could silently downgrade a 503 to a 403 partner_key_not_found.
        """
        from sqlalchemy.exc import OperationalError

        mock_db.execute = AsyncMock(side_effect=OperationalError("SELECT ...", {}, Exception("connection refused")))

        with pytest.raises(OperationalError):
            await verify_identity_claim(
                db=mock_db,
                jwks_resolver=_FakeJwksResolver(),
                caller_service="portal-api",
                claimed_user_id="partner:11111111-1111-1111-1111-111111111111",
                claimed_org_id="zitadel-org-acme",
                bearer_jwt=None,
            )

    async def test_deny_on_org_slug_mismatch_after_allow(self, mock_db: AsyncMock) -> None:
        # Key + org match, but caller asserts a different X-Org-Slug than the
        # canonical one — REQ-2.6 still applies.
        mock_result = MagicMock()
        mock_result.one_or_none = MagicMock(return_value=("zitadel-org-acme", "acme"))
        mock_db.execute.return_value = mock_result

        decision = await verify_identity_claim(
            db=mock_db,
            jwks_resolver=_FakeJwksResolver(),
            caller_service="portal-api",
            claimed_user_id="partner:11111111-1111-1111-1111-111111111111",
            claimed_org_id="zitadel-org-acme",
            bearer_jwt=None,
            claimed_org_slug="impostor-slug",
        )

        assert decision.verified is False
        assert decision.reason == "org_slug_mismatch"

    async def test_returns_canonical_slug_when_no_org_slug_claimed(self, mock_db: AsyncMock) -> None:
        # Same shape as TestOrgSlugCheck::test_jwt_path_returns_canonical_slug_when_no_claim:
        # the canonical slug always flows back so cache re-checks work.
        mock_result = MagicMock()
        mock_result.one_or_none = MagicMock(return_value=("zitadel-org-acme", "canonical"))
        mock_db.execute.return_value = mock_result

        decision = await verify_identity_claim(
            db=mock_db,
            jwks_resolver=_FakeJwksResolver(),
            caller_service="portal-api",
            claimed_user_id="partner:11111111-1111-1111-1111-111111111111",
            claimed_org_id="zitadel-org-acme",
            bearer_jwt=None,
            claimed_org_slug=None,
        )

        assert decision.verified is True
        assert decision.org_slug == "canonical"


class TestVerifyTenantClaim:
    """Tests for verify_tenant_claim — the tenant-only service-to-service path."""

    async def test_allow_tenant_when_org_exists(self, mock_db: AsyncMock) -> None:
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value="acme")
        mock_db.execute.return_value = mock_result

        decision = await verify_tenant_claim(
            db=mock_db,
            caller_service="portal-api",
            claimed_org_id="o-1",
        )

        assert decision.verified is True
        assert decision.evidence == "tenant_only"
        assert decision.org_id == "o-1"
        assert decision.org_slug == "acme"
        assert decision.user_id is None
        assert decision.reason is None

    async def test_deny_tenant_not_found_when_org_missing(self, mock_db: AsyncMock) -> None:
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=None)
        mock_db.execute.return_value = mock_result

        decision = await verify_tenant_claim(
            db=mock_db,
            caller_service="portal-api",
            claimed_org_id="o-missing",
        )

        assert decision.verified is False
        assert decision.reason == "tenant_not_found"

    async def test_deny_org_slug_mismatch_when_slug_differs(self, mock_db: AsyncMock) -> None:
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value="acme")
        mock_db.execute.return_value = mock_result

        decision = await verify_tenant_claim(
            db=mock_db,
            caller_service="portal-api",
            claimed_org_id="o-1",
            claimed_org_slug="wrong-slug",
        )

        assert decision.verified is False
        assert decision.reason == "org_slug_mismatch"

    async def test_deny_unknown_caller_service(self, mock_db: AsyncMock) -> None:
        decision = await verify_tenant_claim(
            db=mock_db,
            caller_service="unknown-service",
            claimed_org_id="o-1",
        )

        assert decision.verified is False
        assert decision.reason == "unknown_caller_service"
        # No DB call should have happened — allowlist check is the first gate.
        mock_db.execute.assert_not_called()

    async def test_allow_when_org_slug_matches(self, mock_db: AsyncMock) -> None:
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value="acme")
        mock_db.execute.return_value = mock_result

        decision = await verify_tenant_claim(
            db=mock_db,
            caller_service="portal-api",
            claimed_org_id="o-1",
            claimed_org_slug="acme",
        )

        assert decision.verified is True
        assert decision.org_slug == "acme"


class TestLibraryPortalSymmetry:
    """Polish guard added 2026-05-06: the consumer-side library
    (``klai_identity_assert``) must accept every ``Evidence`` and
    ``ReasonCode`` value that portal-side ``identity_verifier`` can emit.

    Without this guard a future PR can extend portal's Literal without
    extending the library's, and the library's Pydantic-style response
    parsing would silently drop the new value into a generic deny — or
    worse, fail JSON parsing at runtime against a real prod payload.
    """

    def test_evidence_literal_is_identical(self) -> None:
        """``Evidence`` is what portal emits — both sides MUST agree."""
        from typing import get_args

        from klai_identity_assert.models import Evidence as LibEvidence

        from app.services.identity_verifier import Evidence as PortalEvidence

        assert set(get_args(PortalEvidence)) == set(get_args(LibEvidence)), (
            "Evidence Literal drift between portal-side identity_verifier "
            "and klai_identity_assert library. The library will silently "
            "miss any new evidence the portal starts emitting."
        )

    def test_portal_reason_codes_are_subset_of_library(self) -> None:
        """``ReasonCode`` from portal is what gets returned over the wire.
        The library extends it with consumer-side codes (`portal_unreachable`,
        `library_misconfigured`, `cache_unavailable`) but MUST contain every
        portal-emitted code."""
        from typing import get_args

        from klai_identity_assert.models import ReasonCode as LibReasonCode

        from app.services.identity_verifier import ReasonCode as PortalReasonCode

        portal_codes = set(get_args(PortalReasonCode))
        lib_codes = set(get_args(LibReasonCode))

        missing = portal_codes - lib_codes
        assert not missing, (
            f"Portal emits ReasonCodes the library does not accept: {sorted(missing)}. "
            "Add them to klai-libs/identity-assert/klai_identity_assert/models.py."
        )
