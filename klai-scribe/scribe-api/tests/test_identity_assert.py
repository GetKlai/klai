"""SPEC-SEC-IDENTITY-ASSERT-001 REQ-3 tests for klai-scribe ingest endpoint.

Closes the S1 finding in spec.md: ``POST /v1/transcriptions/{id}/ingest``
no longer accepts ``org_id`` in the request body. The tenant is derived
from the authenticated JWT's ``resourceowner`` claim, so a caller cannot
push their transcript into another org's knowledge base by editing the
request body.

Acceptance coverage:

- AC-2: cross-org ingest attempt is rejected (no body.org_id to attempt
  the cross-org with — schema-level closure).
- REQ-3.4: JWT without ``resourceowner`` → 403 ``no_active_org_membership``.
- REQ-3.5 fast path: JWT with ``resourceowner`` → ``ingest_scribe_transcript``
  receives that org_id directly, no portal-api round trip.
- AC-7 partial: legitimate flow still works after migration.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

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


_STUB_JWT = "stub.jwt.value"  # noqa: S105 — placeholder for monkey-patched decode


def _credentials(value: str = _STUB_JWT) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=value)


# ---------------------------------------------------------------------------
# get_authenticated_caller — the dependency that derives (user_id, org_id)
# from the JWT.
# ---------------------------------------------------------------------------


class TestAuthenticatedCaller:
    """REQ-3.5: trust the JWT ``resourceowner`` claim directly."""

    async def test_returns_user_id_and_org_id_from_jwt(self, patch_jwt) -> None:
        from app.core.auth import CallerIdentity, get_authenticated_caller

        patch_jwt(
            {
                "sub": "user-aaaa",
                "urn:zitadel:iam:user:resourceowner:id": "org-xxxx",
            }
        )

        caller = await get_authenticated_caller(credentials=_credentials())

        assert isinstance(caller, CallerIdentity)
        assert caller.user_id == "user-aaaa"
        assert caller.org_id == "org-xxxx"

    async def test_returns_403_when_resourceowner_claim_missing(self, patch_jwt) -> None:
        # REQ-3.4: a user without an active org membership in Zitadel has
        # no resourceowner in their JWT. Endpoint MUST refuse — silently
        # downgrading to a default org would re-introduce the S1 chain.
        from app.core.auth import get_authenticated_caller

        patch_jwt({"sub": "user-aaaa"})  # no resourceowner

        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_caller(credentials=_credentials())

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "no_active_org_membership"

    async def test_returns_403_when_resourceowner_is_empty_string(self, patch_jwt) -> None:
        # Defensive: an explicit empty string is treated identically to
        # absent — no silent fallback.
        from app.core.auth import get_authenticated_caller

        patch_jwt(
            {
                "sub": "user-aaaa",
                "urn:zitadel:iam:user:resourceowner:id": "",
            }
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_caller(credentials=_credentials())

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail == "no_active_org_membership"

    async def test_returns_403_when_resourceowner_is_not_a_string(self, patch_jwt) -> None:
        # Defensive against malformed JWT payload — type narrows before
        # any handler sees the value.
        from app.core.auth import get_authenticated_caller

        patch_jwt(
            {
                "sub": "user-aaaa",
                "urn:zitadel:iam:user:resourceowner:id": ["org-x", "org-y"],
            }
        )

        with pytest.raises(HTTPException) as exc_info:
            await get_authenticated_caller(credentials=_credentials())

        assert exc_info.value.status_code == 403

    async def test_returns_401_when_sub_claim_malformed(self, patch_jwt) -> None:
        # The HY-34 sub-charset whitelist (existing behaviour) MUST still
        # fire on the new dependency — defense-in-depth shared between
        # both call paths.
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
