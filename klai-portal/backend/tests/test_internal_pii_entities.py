"""SPEC-PRIVACY-MISTRAL-PII-001 REQ-7 — GET /internal/v1/orgs/{org_id}/pii-entities.

Pinned behaviour:
- No bearer / wrong bearer → 401 from ``_require_internal_token``, before any DB work.
- Correct bearer → the handler proceeds (the token gate is not accidentally strict).
- Org with no opt-in → ``enabled_entities == []`` (REQ-7 "per-org, default off").
- Org with an opt-in → exactly that set, sorted.
- A stored value outside REQ-7's return set (``PERSON`` / ``SECRET`` / unknown)
  never reaches the wire.
- Tenant isolation (NFR): org A's id returns A's policy and only A's.

Layout follows ``tests/test_internal_user_permissions_endpoint.py`` — the handler
is called directly with a mocked session, so no live database is needed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, status

INTERNAL_SECRET = "internal-secret-under-test"


def _make_request(*, token: str | None = INTERNAL_SECRET) -> MagicMock:
    """FastAPI Request mock accepted by the real ``_require_internal_token``."""
    headers: dict[str, str] = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"

    request = MagicMock()
    request.headers = MagicMock()
    request.headers.get = lambda key, default="": next(
        (v for k, v in headers.items() if k.lower() == key.lower()),
        default,
    )
    request.client = MagicMock()
    request.client.host = "172.18.0.5"
    request.method = "GET"
    request.url = MagicMock()
    request.url.path = "/internal/v1/orgs/1/pii-entities"
    request.scope = {"route": MagicMock(path="/internal/v1/orgs/{org_id}/pii-entities")}
    request.state = MagicMock()
    return request


class _FakeOrgDb:
    """Session stub that answers from a per-org table, keyed on the bound id.

    Reads the compiled WHERE parameter rather than ignoring the statement, so a
    handler that dropped the ``org_id`` filter would return the wrong row here
    instead of quietly passing.
    """

    def __init__(self, rows: dict[str, list[str]]):
        # Keyed on the ZITADEL org id — the id the caller actually has. See
        # test_zitadel_org_id_is_the_lookup_key for why this is not the
        # portal integer PK.
        self._rows = rows
        self.queried_org_ids: list[str] = []
        self._pk_for = {org: index + 1 for index, org in enumerate(sorted(rows))}

    async def execute(self, statement):
        params = statement.compile().params
        org_id = next(iter(params.values()))
        self.queried_org_ids.append(org_id)

        stored = self._rows.get(org_id)
        result = MagicMock()
        # The handler selects (PortalOrg.id, PortalOrg.pii_masked_entities):
        # the PK for set_tenant, the array for the response.
        result.first.return_value = None if stored is None else (self._pk_for[org_id], stored)
        return result


@pytest.fixture
def patched_internal(monkeypatch):
    """Bypass rate limit + audit; keep the real token check and read path."""
    from app.api import internal

    monkeypatch.setattr(internal.settings, "internal_secret", INTERNAL_SECRET)
    monkeypatch.setattr(internal, "_check_rate_limit_internal", AsyncMock())
    monkeypatch.setattr(internal, "_audit_internal_call", AsyncMock())
    monkeypatch.setattr(internal, "set_tenant", AsyncMock())
    return internal


class TestAuth:
    @pytest.mark.asyncio
    async def test_missing_bearer_returns_401_before_db(self, patched_internal):
        db = _FakeOrgDb({"372801852200189969": []})

        with pytest.raises(HTTPException) as exc:
            await patched_internal.get_org_pii_entities(
                org_id="372801852200189969", request=_make_request(token=None), db=db
            )

        assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert db.queried_org_ids == []

    @pytest.mark.asyncio
    async def test_wrong_bearer_returns_401_before_db(self, patched_internal):
        db = _FakeOrgDb({"372801852200189969": []})

        with pytest.raises(HTTPException) as exc:
            await patched_internal.get_org_pii_entities(
                org_id="372801852200189969", request=_make_request(token="wrong"), db=db
            )

        assert exc.value.status_code == status.HTTP_401_UNAUTHORIZED
        assert db.queried_org_ids == []

    @pytest.mark.asyncio
    async def test_unconfigured_secret_returns_503(self, patched_internal, monkeypatch):
        """Fail closed rather than accepting an empty bearer."""
        monkeypatch.setattr(patched_internal.settings, "internal_secret", "")
        db = _FakeOrgDb({"372801852200189969": []})

        with pytest.raises(HTTPException) as exc:
            await patched_internal.get_org_pii_entities(
                org_id="372801852200189969", request=_make_request(token=""), db=db
            )

        assert exc.value.status_code == status.HTTP_503_SERVICE_UNAVAILABLE
        assert db.queried_org_ids == []

    @pytest.mark.asyncio
    async def test_correct_bearer_is_accepted(self, patched_internal):
        db = _FakeOrgDb({"372801852200189969": ["IBAN_CODE"]})

        response = await patched_internal.get_org_pii_entities(
            org_id="372801852200189969", request=_make_request(), db=db
        )

        assert response.enabled_entities == ["IBAN_CODE"]


class TestPolicyResponse:
    @pytest.mark.asyncio
    async def test_org_without_opt_in_returns_empty_set(self, patched_internal):
        """REQ-7 default-off, including for orgs that predate the column."""
        db = _FakeOrgDb({"372801852200189907": []})

        response = await patched_internal.get_org_pii_entities(
            org_id="372801852200189907", request=_make_request(), db=db
        )

        assert response.org_id == "372801852200189907"
        assert response.enabled_entities == []

    @pytest.mark.asyncio
    async def test_populated_policy_is_returned_sorted(self, patched_internal):
        db = _FakeOrgDb({"372801852200189907": ["NL_KVK", "IBAN_CODE", "EMAIL_ADDRESS"]})

        response = await patched_internal.get_org_pii_entities(
            org_id="372801852200189907", request=_make_request(), db=db
        )

        assert response.enabled_entities == ["EMAIL_ADDRESS", "IBAN_CODE", "NL_KVK"]

    @pytest.mark.asyncio
    async def test_response_key_matches_the_litellm_client_contract(self, patched_internal):
        """``klai_pii_org_policy.py:141-143`` reads ``enabled_entities`` and needs a list."""
        db = _FakeOrgDb({"372801852200189907": ["IBAN_CODE"]})

        response = await patched_internal.get_org_pii_entities(
            org_id="372801852200189907", request=_make_request(), db=db
        )
        payload = response.model_dump()

        assert isinstance(payload.get("enabled_entities"), list)
        assert all(isinstance(entity, str) for entity in payload["enabled_entities"])

    @pytest.mark.asyncio
    @pytest.mark.parametrize("stored", ["PERSON", "SECRET", "NL_BSN", "US_SSN"])
    async def test_non_return_set_values_are_filtered_out(self, patched_internal, stored):
        db = _FakeOrgDb({"372801852200189907": [stored, "IBAN_CODE"]})

        response = await patched_internal.get_org_pii_entities(
            org_id="372801852200189907", request=_make_request(), db=db
        )

        assert response.enabled_entities == ["IBAN_CODE"]

    @pytest.mark.asyncio
    async def test_filtered_value_is_logged_not_swallowed(self, patched_internal, monkeypatch):
        """Fail loudly: a stored value outside the return set must be visible."""
        logger = MagicMock()
        monkeypatch.setattr(patched_internal, "structlog_logger", logger)
        db = _FakeOrgDb({"372801852200189907": ["SECRET", "IBAN_CODE"]})

        await patched_internal.get_org_pii_entities(org_id="372801852200189907", request=_make_request(), db=db)

        logger.warning.assert_called_once()
        assert logger.warning.call_args.args[0] == "pii_org_policy_stored_value_rejected"
        assert logger.warning.call_args.kwargs["dropped"] == ["SECRET"]

    @pytest.mark.asyncio
    async def test_clean_policy_logs_nothing(self, patched_internal, monkeypatch):
        logger = MagicMock()
        monkeypatch.setattr(patched_internal, "structlog_logger", logger)
        db = _FakeOrgDb({"372801852200189907": ["IBAN_CODE"]})

        await patched_internal.get_org_pii_entities(org_id="372801852200189907", request=_make_request(), db=db)

        logger.warning.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_org_returns_404(self, patched_internal):
        db = _FakeOrgDb({})

        with pytest.raises(HTTPException) as exc:
            await patched_internal.get_org_pii_entities(org_id="3728018522001899999", request=_make_request(), db=db)

        assert exc.value.status_code == status.HTTP_404_NOT_FOUND


class TestTenantIsolation:
    @pytest.mark.asyncio
    async def test_org_a_cannot_read_org_b_policy(self, patched_internal):
        db = _FakeOrgDb(
            {
                "372801852200189969": ["IBAN_CODE"],
                "372801852200189970": ["NL_POSTCODE", "PHONE_NUMBER"],
            }
        )

        response_a = await patched_internal.get_org_pii_entities(
            org_id="372801852200189969", request=_make_request(), db=db
        )
        response_b = await patched_internal.get_org_pii_entities(
            org_id="372801852200189970", request=_make_request(), db=db
        )

        assert response_a.enabled_entities == ["IBAN_CODE"]
        assert response_b.enabled_entities == ["NL_POSTCODE", "PHONE_NUMBER"]
        assert "NL_POSTCODE" not in response_a.enabled_entities
        assert "IBAN_CODE" not in response_b.enabled_entities
        assert db.queried_org_ids == ["372801852200189969", "372801852200189970"]

    @pytest.mark.asyncio
    async def test_tenant_context_is_bound_to_the_path_org(self, patched_internal):
        db = _FakeOrgDb({"372801852200189970": []})

        await patched_internal.get_org_pii_entities(org_id="372801852200189970", request=_make_request(), db=db)

        # set_tenant takes the portal integer PK — that is what RLS binds to —
        # while the path parameter and the audit record carry the Zitadel id
        # the caller supplied. The two id spaces are deliberately different
        # and this test pins both, because conflating them is exactly the bug
        # that made this endpoint return 500 in production.
        assert patched_internal.set_tenant.await_args.args[1] == 1
        assert patched_internal._audit_internal_call.await_args.kwargs["org_id"] == "372801852200189970"


class TestOrgIdSpace:
    """The path parameter is the ZITADEL org id, not the portal integer PK.

    Typing it as ``int`` was a live production defect: the LiteLLM enforcement
    stack carries the Zitadel id in team-key metadata (every PII event logs it,
    e.g. ``pii_observed org_id=372801852200189969``), and against the deployed
    endpoint a real id returned **500** while the portal PK ``1`` returned 200.
    The client treats any non-2xx as the empty policy, so the entire REQ-7
    return set could never activate and the failure was indistinguishable from
    "this org opted into nothing".

    No unit test caught it because every test supplied the PK the handler
    happened to want. These pin the id space itself.
    """

    @pytest.mark.asyncio
    async def test_zitadel_org_id_is_the_lookup_key(self, patched_internal):
        zitadel_id = "372801852200189969"
        db = _FakeOrgDb({zitadel_id: ["IBAN_CODE"]})

        response = await patched_internal.get_org_pii_entities(org_id=zitadel_id, request=_make_request(), db=db)

        assert db.queried_org_ids == [zitadel_id]
        assert response.enabled_entities == ["IBAN_CODE"]

    @pytest.mark.asyncio
    async def test_long_zitadel_id_does_not_raise(self, patched_internal):
        """A 18-digit Zitadel id overflows a PG int4 — the shape that 500'd."""
        zitadel_id = "372801852200189969"
        db = _FakeOrgDb({zitadel_id: []})

        response = await patched_internal.get_org_pii_entities(org_id=zitadel_id, request=_make_request(), db=db)

        assert response.org_id == zitadel_id

    @pytest.mark.asyncio
    async def test_unknown_org_is_404_not_a_crash(self, patched_internal):
        db = _FakeOrgDb({"372801852200189969": []})

        with pytest.raises(HTTPException) as exc:
            await patched_internal.get_org_pii_entities(org_id="999999999999999999", request=_make_request(), db=db)

        assert exc.value.status_code == 404
