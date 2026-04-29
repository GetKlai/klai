"""SPEC-SEC-AUDIT-2026-04 B1 tests for klai-scribe identity resolution.

Fixes the B1 re-audit finding: ``get_authenticated_caller`` previously trusted
the JWT ``resourceowner`` claim as the authoritative org, violating the
``klai/platform/zitadel.md`` rule ("Never use resourceowner — not always
present. Use sub → portal_users → portal_orgs join for reliable org
resolution.").

The fix replaces the resourceowner fast-path with a portal
``/internal/identity/verify`` call via ``klai-identity-assert``. The JWT's
``sub`` claim is used as ``claimed_user_id`` and the ``resourceowner`` (if
present) as ``claimed_org_id``; portal returns the canonical ``org_id`` from
``portal_users`` / ``portal_orgs``.

Acceptance coverage (B1):

- CROSS-TENANT: a JWT whose ``resourceowner`` points at org-A but portal
  lookup resolves to org-B → ``org_id`` in CallerIdentity is org-B (portal
  wins, not the claim).
- PORTAL-404: portal returns ``no_membership`` → 403 ``unknown_user``.
- PORTAL-5XX: portal is unreachable → 503 ``portal_unreachable`` (fail-closed).
- EMPTY-RESOURCEOWNER: JWT without resourceowner → portal is still called with
  ``claimed_org_id=""``; portal returns the canonical org from the membership
  lookup (bearer_jwt path).
- HAPPY-PATH: JWT verifies, portal lookup returns canonical org → CallerIdentity
  carries portal's org_id.

Legacy coverage (REQ-3 from original SPEC-SEC-IDENTITY-ASSERT-001, kept for
regression detection):

- AC-2: cross-org ingest attempt is rejected (schema-level closure, no body.org_id).
- AC-7 partial: legitimate flow still works after migration.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from klai_identity_assert import VerifyResult

# ---------------------------------------------------------------------------
# JWT decode stubs (same shape as test_auth_sub_validation.py)
# ---------------------------------------------------------------------------


async def _fake_get_jwks(force_refresh: bool = False) -> dict:
    return {"keys": [{"kid": "test", "kty": "RSA", "n": "x", "e": "AQAB"}]}


def _fake_find_key(jwks: dict, kid: str | None) -> dict:
    return {"kid": "test"}


def _fake_get_unverified_header(token: str) -> dict:
    return {"kid": "test"}


@pytest.fixture
def patch_jwt(monkeypatch):
    """Stub the Zitadel JWKS pipeline; return a setter for the decoded claim set."""

    monkeypatch.setattr("app.core.auth._get_jwks", _fake_get_jwks)
    monkeypatch.setattr("app.core.auth._find_key", _fake_find_key)
    monkeypatch.setattr(
        "app.core.auth.jwt.get_unverified_header", _fake_get_unverified_header
    )

    def _set_payload(payload: dict) -> None:
        def _fake_decode(*_a, **_kw) -> dict:
            return payload

        monkeypatch.setattr("app.core.auth.jwt.decode", _fake_decode)

    return _set_payload


_STUB_JWT = "stub.jwt.value"  # placeholder for monkey-patched decode


def _credentials(value: str = _STUB_JWT) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=value)


# ---------------------------------------------------------------------------
# get_authenticated_caller — the dependency that derives (user_id, org_id)
# from the JWT.
# ---------------------------------------------------------------------------


class TestAuthenticatedCallerPortalVerify:
    """SPEC-SEC-AUDIT-2026-04 B1: get_authenticated_caller uses portal verify.

    The org_id in CallerIdentity is always resolved by portal-api, never
    trusted from the JWT resourceowner claim.
    """

    @pytest.fixture
    def portal_verify_allow(self, monkeypatch):
        """Stub IdentityAsserter.verify to return a successful allow result."""

        async def _fake_verify(self_inner, **kwargs):
            return VerifyResult.allow(
                user_id=kwargs["claimed_user_id"],
                org_id="portal-resolved-org",
                org_slug="portal-resolved-slug",
                evidence="membership",
            )

        monkeypatch.setattr(
            "app.core.auth.IdentityAsserter.verify",
            _fake_verify,
        )

    @pytest.fixture
    def portal_verify_deny_no_membership(self, monkeypatch):
        """Stub IdentityAsserter.verify to return no_membership denial."""

        async def _fake_verify(self_inner, **kwargs):
            return VerifyResult.deny("no_membership")

        monkeypatch.setattr(
            "app.core.auth.IdentityAsserter.verify",
            _fake_verify,
        )

    @pytest.fixture
    def portal_verify_unreachable(self, monkeypatch):
        """Stub IdentityAsserter.verify to return portal_unreachable denial."""

        async def _fake_verify(self_inner, **kwargs):
            return VerifyResult.deny("portal_unreachable")

        monkeypatch.setattr(
            "app.core.auth.IdentityAsserter.verify",
            _fake_verify,
        )

    async def test_happy_path_returns_portal_org_id(
        self, patch_jwt, portal_verify_allow
    ) -> None:
        """HAPPY-PATH: portal-resolved org_id is used, not the JWT claim."""
        from app.core.auth import CallerIdentity, get_authenticated_caller

        patch_jwt(
            {
                "sub": "user-aaaa",
                "urn:zitadel:iam:user:resourceowner:id": "jwt-claim-org",
            }
        )

        caller = await get_authenticated_caller(credentials=_credentials())

        assert isinstance(caller, CallerIdentity)
        assert caller.user_id == "user-aaaa"
        # Portal's answer overrides the JWT claim.
        assert caller.org_id == "portal-resolved-org"

    async def test_cross_tenant_token_confusion_is_closed(
        self, patch_jwt, monkeypatch
    ) -> None:
        """CROSS-TENANT (the B1 finding): JWT resourceowner differs from portal org.

        A multi-org user's token carries ``resourceowner=org-A``. If scribe
        trusted that directly, the transcript would land in org-A's KB even
        though the user's current active membership is org-B. Portal's lookup
        returns org-B, which is what CallerIdentity MUST carry.
        """
        from klai_identity_assert import IdentityAsserter

        from app.core.auth import CallerIdentity, get_authenticated_caller

        resourceowner_in_jwt = "org-A-from-jwt-claim"
        portal_canonical_org = "org-B-from-portal-lookup"

        captured_claimed_org = {}

        async def _fake_verify(self_inner, **kwargs):
            # Record what was passed as the claim so we can verify it came
            # from the JWT's resourceowner (used as a hint, not as truth).
            captured_claimed_org["claimed_org_id"] = kwargs.get("claimed_org_id")
            # Portal returns a DIFFERENT org than what the JWT carries.
            return VerifyResult.allow(
                user_id=kwargs["claimed_user_id"],
                org_id=portal_canonical_org,
                org_slug="org-b-slug",
                evidence="membership",
            )

        monkeypatch.setattr(IdentityAsserter, "verify", _fake_verify)

        patch_jwt(
            {
                "sub": "user-multi-org",
                "urn:zitadel:iam:user:resourceowner:id": resourceowner_in_jwt,
            }
        )

        caller = await get_authenticated_caller(credentials=_credentials())

        assert isinstance(caller, CallerIdentity)
        assert caller.user_id == "user-multi-org"
        # The CRITICAL assertion: org_id is portal's canonical answer,
        # not the JWT resourceowner. This is the B1 fix.
        assert caller.org_id == portal_canonical_org
        assert caller.org_id != resourceowner_in_jwt

    async def test_portal_no_membership_returns_403_unknown_user(
        self, patch_jwt, portal_verify_deny_no_membership
    ) -> None:
        """PORTAL-404: user not found in portal_users → 403 unknown_user."""
        from app.core.auth import get_authenticated_caller

        patch_jwt({"sub": "user-not-in-portal"})

        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_caller(credentials=_credentials())

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "unknown_user"

    async def test_portal_unreachable_returns_503(
        self, patch_jwt, portal_verify_unreachable
    ) -> None:
        """PORTAL-5XX: fail-closed — portal unreachable → 503 portal_unreachable."""
        from app.core.auth import get_authenticated_caller

        patch_jwt({"sub": "user-aaaa"})

        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_caller(credentials=_credentials())

        assert exc_info.value.status_code == 503
        assert exc_info.value.detail == "portal_unreachable"

    async def test_empty_resourceowner_still_calls_portal(
        self, patch_jwt, monkeypatch
    ) -> None:
        """EMPTY-RESOURCEOWNER: JWT without resourceowner still resolves via portal.

        The old code raised 403 immediately. The new code passes claimed_org_id=""
        and lets portal do the membership lookup using the bearer_jwt path.
        """
        from klai_identity_assert import IdentityAsserter

        from app.core.auth import CallerIdentity, get_authenticated_caller

        portal_was_called = {}

        async def _fake_verify(self_inner, **kwargs):
            portal_was_called["yes"] = True
            portal_was_called["claimed_org_id"] = kwargs.get("claimed_org_id")
            return VerifyResult.allow(
                user_id=kwargs["claimed_user_id"],
                org_id="portal-org-from-membership",
                org_slug="org-slug",
                evidence="membership",
            )

        monkeypatch.setattr(IdentityAsserter, "verify", _fake_verify)

        # JWT has no resourceowner claim at all.
        patch_jwt({"sub": "user-aaaa"})

        caller = await get_authenticated_caller(credentials=_credentials())

        assert portal_was_called.get("yes"), (
            "portal.verify must be called even without resourceowner"
        )
        assert isinstance(caller, CallerIdentity)
        assert caller.org_id == "portal-org-from-membership"

    async def test_returns_401_when_sub_claim_malformed(self, patch_jwt) -> None:
        """HY-34 sub-charset whitelist fires before the portal call."""
        from app.core.auth import get_authenticated_caller

        patch_jwt(
            {
                "sub": "user/with/slashes",  # rejected by _ZITADEL_SUB_PATTERN
                "urn:zitadel:iam:user:resourceowner:id": "org-xxxx",
            }
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_caller(credentials=_credentials())

        assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# IngestToKBRequest — schema-level closure of the S1 body-org_id finding
# ---------------------------------------------------------------------------


class TestIngestToKBRequestSchema:
    def test_request_no_longer_accepts_org_id_field(self) -> None:
        # REQ-3.1: org_id is removed from the schema. Pydantic by default
        # ignores unknown fields, but the canonical schema MUST not
        # advertise an org_id input — that's the contract change.
        from app.api.transcribe import IngestToKBRequest

        fields = IngestToKBRequest.model_fields
        assert "org_id" not in fields, (
            "IngestToKBRequest must not declare org_id (REQ-3.1) — it would "
            "let any caller with a valid JWT push into any org's KB."
        )
        assert "kb_slug" in fields

    def test_kb_slug_remains_required(self) -> None:
        from pydantic import ValidationError

        from app.api.transcribe import IngestToKBRequest

        with pytest.raises(ValidationError):
            IngestToKBRequest()  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# ingest_transcription_to_kb handler — verifies that ingest_scribe_transcript
# is called with the JWT-derived org_id, not anything from the request body.
# ---------------------------------------------------------------------------


class TestIngestHandlerUsesJwtDerivedOrgId:
    @pytest.fixture
    def stub_transcription(self) -> MagicMock:
        record = MagicMock()
        record.id = "txn-abc"
        record.user_id = "user-aaaa"
        record.text = "hello world"
        record.name = "Standup"
        record.duration_seconds = 30
        record.segments_json = None
        return record

    @pytest.fixture
    def db_returning_transcription(self, stub_transcription: MagicMock) -> MagicMock:
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=stub_transcription)
        db = MagicMock()
        db.execute = AsyncMock(return_value=result)
        return db

    async def test_handler_passes_jwt_org_id_to_ingest_adapter(
        self, monkeypatch: pytest.MonkeyPatch, db_returning_transcription: MagicMock
    ) -> None:
        from app.api.transcribe import IngestToKBRequest, ingest_transcription_to_kb
        from app.core.auth import CallerIdentity

        captured = {}

        async def fake_ingest(*, org_id: str, kb_slug: str, transcription) -> str:
            captured["org_id"] = org_id
            captured["kb_slug"] = kb_slug
            return "art-001"

        monkeypatch.setattr(
            "app.services.knowledge_adapter.ingest_scribe_transcript", fake_ingest
        )

        caller = CallerIdentity(user_id="user-aaaa", org_id="VERIFIED-ORG")

        response = await ingest_transcription_to_kb(
            txn_id="txn-abc",
            body=IngestToKBRequest(kb_slug="team-notes"),
            caller=caller,
            db=db_returning_transcription,
        )

        # Ingest was called with the JWT-derived org_id, not anything
        # the request body could carry.
        assert captured["org_id"] == "VERIFIED-ORG"
        assert captured["kb_slug"] == "team-notes"
        assert response.artifact_id == "art-001"
        assert response.status == "ok"

    async def test_handler_returns_404_when_transcription_owned_by_another_user(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Existing transcription-ownership check MUST still hold: a caller
        # cannot ingest someone else's recording even when JWT identity is
        # otherwise valid.
        from app.api.transcribe import IngestToKBRequest, ingest_transcription_to_kb
        from app.core.auth import CallerIdentity

        # DB returns no row (Transcription.user_id filter excludes it).
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=None)
        db = MagicMock()
        db.execute = AsyncMock(return_value=result)

        async def fake_ingest(**_kw) -> str:
            raise AssertionError("ingest must not run when transcription is missing")

        monkeypatch.setattr(
            "app.services.knowledge_adapter.ingest_scribe_transcript", fake_ingest
        )

        with pytest.raises(HTTPException) as exc_info:
            await ingest_transcription_to_kb(
                txn_id="txn-other",
                body=IngestToKBRequest(kb_slug="team-notes"),
                caller=CallerIdentity(user_id="user-aaaa", org_id="org-xxxx"),
                db=db,
            )

        assert exc_info.value.status_code == 404
