"""SPEC-PRIVACY-PII-POLICY-ADMIN-001 PR1 — tenant PII policy write path.

Covers the two tenant-self-service endpoints added to ``app/api/orgs.py``:

- ``PATCH /api/orgs/me/pii-entities`` — full-set replacement of the
  opted-in PII entity types, gated at ``ProfileRole.ADMIN``, validated via
  ``pii_entity_policy.validate_entity_selection`` (REQ-1).
- ``PATCH /api/orgs/me/pii-allow-list`` — full-set replacement of the
  tenant's allow-list exclusions, validated via
  ``pii_allow_list.validate_allow_list`` (D1/REQ-9).

Layout follows ``tests/test_orgs_telemetry_level.py`` (endpoint called
directly with a synthetic ``UserPermissions`` + mocked ``AsyncSession``)
and ``tests/test_platform_unlocks_phase5.py`` (full-replace + audit
pattern). AC-1 through AC-4 of the SPEC map directly onto the test classes
below.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException, status

from app.models.portal import PortalOrg
from tests.conftest import make_perms
from tests.role_matrix_helpers import assert_role_blocked_at_gate


def _make_db_async() -> AsyncMock:
    """Minimal AsyncSession mock. db.add() kept synchronous (SQLAlchemy contract)."""
    db = AsyncMock()
    db.add = MagicMock()
    return db


def _make_org(
    *, org_id: int = 42, pii_masked_entities: list[str] | None = None, pii_allow_list: list[dict] | None = None
) -> MagicMock:
    org = MagicMock(spec=PortalOrg)
    org.id = org_id
    org.pii_masked_entities = pii_masked_entities or []
    org.pii_allow_list = pii_allow_list or []
    return org


# ---------------------------------------------------------------------------
# PATCH /api/orgs/me/pii-entities
# ---------------------------------------------------------------------------


class TestSetPiiEntities:
    @pytest.mark.asyncio
    async def test_admin_can_set_valid_entity_set(self) -> None:
        """AC-1: valid set persists, is sorted+deduped, and is committed."""
        from app.api.orgs import PiiEntitiesUpdate, set_my_org_pii_entities

        org = _make_org(org_id=42)
        db = _make_db_async()
        db.get = AsyncMock(return_value=org)
        perms = make_perms(role="admin", user_id="zit-user-1", org_id=42)

        with patch("app.api.orgs.log_event", new_callable=AsyncMock) as mock_audit:
            out = await set_my_org_pii_entities(
                PiiEntitiesUpdate(entities=["IBAN_CODE", "EMAIL_ADDRESS", "IBAN_CODE"]),
                perms=perms,
                db=db,
            )

        assert out.entities == ["EMAIL_ADDRESS", "IBAN_CODE"]
        assert org.pii_masked_entities == ["EMAIL_ADDRESS", "IBAN_CODE"]
        db.commit.assert_awaited_once()
        mock_audit.assert_awaited_once()
        assert mock_audit.call_args.kwargs["action"] == "pii_masked_entities_changed"
        assert mock_audit.call_args.kwargs["org_id"] == 42

    @pytest.mark.asyncio
    async def test_validate_entity_selection_is_called(self) -> None:
        """Pin the REQ-1 contract itself: the endpoint MUST go through
        ``validate_entity_selection``, not reimplement the check."""
        from app.api.orgs import PiiEntitiesUpdate, set_my_org_pii_entities

        org = _make_org(org_id=42)
        db = _make_db_async()
        db.get = AsyncMock(return_value=org)
        perms = make_perms(role="admin", user_id="zit-user-1", org_id=42)

        with (
            patch("app.api.orgs.log_event", new_callable=AsyncMock),
            patch(
                "app.api.orgs.validate_entity_selection",
                MagicMock(return_value=frozenset({"IBAN_CODE"})),
            ) as mock_validate,
        ):
            await set_my_org_pii_entities(
                PiiEntitiesUpdate(entities=["IBAN_CODE"]),
                perms=perms,
                db=db,
            )

        mock_validate.assert_called_once_with(["IBAN_CODE"])

    @pytest.mark.asyncio
    @pytest.mark.parametrize("forbidden", ["PERSON", "SECRET", "NL_BSN"])
    async def test_forbidden_entities_rejected(self, forbidden: str) -> None:
        """Hard constraint: PERSON/SECRET/NL_BSN are rejected by the endpoint."""
        from app.api.orgs import PiiEntitiesUpdate, set_my_org_pii_entities

        db = _make_db_async()
        db.get = AsyncMock()
        perms = make_perms(role="admin", user_id="zit-user-1", org_id=42)

        with pytest.raises(HTTPException) as exc:
            await set_my_org_pii_entities(
                PiiEntitiesUpdate(entities=[forbidden]),
                perms=perms,
                db=db,
            )

        assert exc.value.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        db.commit.assert_not_awaited()
        db.get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_entity_type_rejected(self) -> None:
        from app.api.orgs import PiiEntitiesUpdate, set_my_org_pii_entities

        db = _make_db_async()
        db.get = AsyncMock()
        perms = make_perms(role="admin", user_id="zit-user-1", org_id=42)

        with pytest.raises(HTTPException) as exc:
            await set_my_org_pii_entities(
                PiiEntitiesUpdate(entities=["US_SSN"]),
                perms=perms,
                db=db,
            )

        assert exc.value.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_admin_user_gets_403_and_no_db_write(self) -> None:
        """AC-4: non-admin tenant user -> 403, no DB write. The 403 fires at
        the ``get_caller_at_least(ADMIN)`` gate before the endpoint body
        (and therefore any DB call) runs."""
        from app.api.orgs import set_my_org_pii_entities

        await assert_role_blocked_at_gate(
            endpoint=set_my_org_pii_entities,
            module_path="app.api.orgs",
            role="company",
        )

    @pytest.mark.asyncio
    async def test_tenant_isolation_writes_do_not_cross(self) -> None:
        """AC-13: two orgs, concurrent writes, neither affects the other.

        org_id comes from ``perms`` (resolved from the caller's JWT), never
        from the request body, so each call is structurally scoped to its
        own org's mocked row.
        """
        from app.api.orgs import PiiEntitiesUpdate, set_my_org_pii_entities

        org_a = _make_org(org_id=1)
        org_b = _make_org(org_id=2)
        db_a = _make_db_async()
        db_a.get = AsyncMock(return_value=org_a)
        db_b = _make_db_async()
        db_b.get = AsyncMock(return_value=org_b)

        perms_a = make_perms(role="admin", user_id="user-a", org_id=1)
        perms_b = make_perms(role="admin", user_id="user-b", org_id=2)

        with patch("app.api.orgs.log_event", new_callable=AsyncMock):
            await set_my_org_pii_entities(PiiEntitiesUpdate(entities=["IBAN_CODE"]), perms=perms_a, db=db_a)
            await set_my_org_pii_entities(PiiEntitiesUpdate(entities=["NL_POSTCODE"]), perms=perms_b, db=db_b)

        assert org_a.pii_masked_entities == ["IBAN_CODE"]
        assert org_b.pii_masked_entities == ["NL_POSTCODE"]
        db_a.get.assert_awaited_once_with(PortalOrg, 1)
        db_b.get.assert_awaited_once_with(PortalOrg, 2)


# ---------------------------------------------------------------------------
# PATCH /api/orgs/me/pii-allow-list
# ---------------------------------------------------------------------------


class TestSetPiiAllowList:
    @pytest.mark.asyncio
    async def test_admin_can_set_valid_allow_list(self) -> None:
        from app.api.orgs import PiiAllowListEntryIn, PiiAllowListUpdate, set_my_org_pii_allow_list

        org = _make_org(org_id=42)
        db = _make_db_async()
        db.get = AsyncMock(return_value=org)
        perms = make_perms(role="admin", user_id="zit-user-1", org_id=42)

        with patch("app.api.orgs.log_event", new_callable=AsyncMock) as mock_audit:
            out = await set_my_org_pii_allow_list(
                PiiAllowListUpdate(
                    entries=[PiiAllowListEntryIn(value="Best Solutions", match="exact", note="our company name")]
                ),
                perms=perms,
                db=db,
            )

        assert out.entries[0].value == "Best Solutions"
        assert org.pii_allow_list == [{"value": "Best Solutions", "match": "exact", "note": "our company name"}]
        db.commit.assert_awaited_once()
        mock_audit.assert_awaited_once()
        assert mock_audit.call_args.kwargs["action"] == "pii_allow_list_changed"

    @pytest.mark.asyncio
    async def test_non_compiling_regex_rejected(self) -> None:
        from app.api.orgs import PiiAllowListEntryIn, PiiAllowListUpdate, set_my_org_pii_allow_list

        db = _make_db_async()
        db.get = AsyncMock()
        perms = make_perms(role="admin", user_id="zit-user-1", org_id=42)

        with pytest.raises(HTTPException) as exc:
            await set_my_org_pii_allow_list(
                PiiAllowListUpdate(entries=[PiiAllowListEntryIn(value="(unclosed", match="regex")]),
                perms=perms,
                db=db,
            )

        assert exc.value.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        db.commit.assert_not_awaited()
        db.get.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_catastrophic_regex_rejected(self) -> None:
        """REQ-9: nested-quantifier allow-list regex is rejected before storage."""
        from app.api.orgs import PiiAllowListEntryIn, PiiAllowListUpdate, set_my_org_pii_allow_list

        db = _make_db_async()
        db.get = AsyncMock()
        perms = make_perms(role="admin", user_id="zit-user-1", org_id=42)

        with pytest.raises(HTTPException) as exc:
            await set_my_org_pii_allow_list(
                PiiAllowListUpdate(entries=[PiiAllowListEntryIn(value="(a+)+", match="regex")]),
                perms=perms,
                db=db,
            )

        assert exc.value.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_over_long_value_rejected(self) -> None:
        from app.api.orgs import PiiAllowListEntryIn, PiiAllowListUpdate, set_my_org_pii_allow_list

        db = _make_db_async()
        db.get = AsyncMock()
        perms = make_perms(role="admin", user_id="zit-user-1", org_id=42)

        with pytest.raises(HTTPException) as exc:
            await set_my_org_pii_allow_list(
                PiiAllowListUpdate(entries=[PiiAllowListEntryIn(value="a" * 500, match="exact")]),
                perms=perms,
                db=db,
            )

        assert exc.value.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_too_many_entries_rejected(self) -> None:
        from app.api.orgs import PiiAllowListEntryIn, PiiAllowListUpdate, set_my_org_pii_allow_list

        db = _make_db_async()
        db.get = AsyncMock()
        perms = make_perms(role="admin", user_id="zit-user-1", org_id=42)
        entries = [PiiAllowListEntryIn(value=f"v{i}", match="exact") for i in range(51)]

        with pytest.raises(HTTPException) as exc:
            await set_my_org_pii_allow_list(PiiAllowListUpdate(entries=entries), perms=perms, db=db)

        assert exc.value.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        db.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_non_admin_user_gets_403_and_no_db_write(self) -> None:
        from app.api.orgs import set_my_org_pii_allow_list

        await assert_role_blocked_at_gate(
            endpoint=set_my_org_pii_allow_list,
            module_path="app.api.orgs",
            role="kb_manager",
        )

    @pytest.mark.asyncio
    async def test_tenant_isolation_writes_do_not_cross(self) -> None:
        from app.api.orgs import PiiAllowListEntryIn, PiiAllowListUpdate, set_my_org_pii_allow_list

        org_a = _make_org(org_id=1)
        org_b = _make_org(org_id=2)
        db_a = _make_db_async()
        db_a.get = AsyncMock(return_value=org_a)
        db_b = _make_db_async()
        db_b.get = AsyncMock(return_value=org_b)

        perms_a = make_perms(role="admin", user_id="user-a", org_id=1)
        perms_b = make_perms(role="admin", user_id="user-b", org_id=2)

        with patch("app.api.orgs.log_event", new_callable=AsyncMock):
            await set_my_org_pii_allow_list(
                PiiAllowListUpdate(entries=[PiiAllowListEntryIn(value="Best", match="exact")]),
                perms=perms_a,
                db=db_a,
            )
            await set_my_org_pii_allow_list(
                PiiAllowListUpdate(entries=[PiiAllowListEntryIn(value="Ede", match="exact")]),
                perms=perms_b,
                db=db_b,
            )

        assert org_a.pii_allow_list == [{"value": "Best", "match": "exact", "note": None}]
        assert org_b.pii_allow_list == [{"value": "Ede", "match": "exact", "note": None}]
