"""Unit tests for app.services.identity_verifier.verify_identity_claim.

Service-layer tests; the HTTP endpoint and Redis cache are tested separately
in test_internal_identity_verify.py. JWT validation is mocked via a fake
``JwksResolver``; DB is mocked via ``AsyncMock`` on the ``execute`` method.

SPEC-SEC-IDENTITY-ASSERT-001 REQ-1 acceptance criteria coverage:
- AC-5a: verified JWT + matching claims → allow with evidence='jwt'
- AC-5b: JWT sub != claimed_user_id → deny with reason='jwt_identity_mismatch'
- AC-5c: bearer_jwt=None + active membership → allow with evidence='membership'
- AC-5d: bearer_jwt=None + no membership → deny with reason='no_membership'
- REQ-1.2: unknown caller_service → deny with reason='unknown_caller_service'
- REQ-1.8: invalid JWT signature → deny with reason='invalid_jwt' (no fallthrough)

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

    async def test_deny_when_jwt_resourceowner_does_not_match_claimed_org(
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

        decision = await verify_identity_claim(
            db=mock_db,
            jwks_resolver=_FakeJwksResolver(),
            caller_service="scribe",
            claimed_user_id="u-1",
            claimed_org_id="o-2",  # cross-org claim
            bearer_jwt="any.jwt.value",
        )

        assert decision.verified is False
        assert decision.reason == "jwt_identity_mismatch"

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

    async def test_deny_when_jwt_claims_have_wrong_types(
        self, mock_db: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Defensive: malformed JWT with sub/resourceowner not strings.
        monkeypatch.setattr(
            "app.services.identity_verifier.jwt.decode",
            lambda *_args, **_kwargs: {
                "sub": 12345,  # int, not str
                "iss": "https://zitadel.example.com",
                "exp": 9999999999,
                "urn:zitadel:iam:user:resourceowner:id": ["o-1"],  # list, not str
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

    async def test_jwt_path_denies_on_slug_mismatch(
        self, mock_db: AsyncMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
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
