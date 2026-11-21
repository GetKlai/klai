"""Real-PostgreSQL proof that offboarding hands partner API keys over.

An integration on a partner API key must keep working when the admin who
created the key leaves, so offboarding and deleting a user move the key to a
recipient instead of deleting it. Two things only a real database shows:
the handed-over key still authenticates through ``get_partner_key`` (same
hash, same access rows), and the creator rule in that lookup
(``owner_user_id == created_by``) does not suddenly open the recipient's
personal knowledge base through a leftover access row.

Skipped unless ``RLS_TEST_DATABASE_URL`` is set; CI runs it in the postgres
lane (``pytest -m postgres``). Local run::

    docker run --rm -d --name handover-pg -e POSTGRES_PASSWORD=test \
        -p 55447:5432 postgres:16
    RLS_TEST_DATABASE_URL=postgresql+asyncpg://postgres:test@localhost:55447/postgres \
        uv run pytest tests/test_offboard_api_key_handover_postgres.py -m postgres -q
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import DefaultClause, MetaData, Table, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from starlette.requests import Request

from app.api.partner_dependencies import get_partner_key
from app.models.knowledge_bases import PortalKnowledgeBase
from app.models.partner_api_keys import PartnerAPIKey, PartnerApiKeyKbAccess
from app.models.portal import PortalOrg, PortalUser
from app.services.kb_offboarding import check_api_keys_recipient, transfer_user_api_keys

pytestmark = pytest.mark.postgres

_SCHEMA = "offboard_key_handover"
_TOKEN_A = "pk_live_" + "a" * 40
_TOKEN_B = "pk_live_" + "b" * 40


def _schema_metadata() -> MetaData:
    """The ORM tables, with the server defaults alembic owns made valid DDL.

    The models carry some defaults as strings that only alembic renders
    correctly (``'[]'::jsonb``, ``company::portal_user_role``,
    ``gen_random_uuid()::varchar`` on a uuid column); ``create_all`` would quote
    them as literals, so they are rewritten here as raw SQL.
    """
    md = MetaData()
    tables = [
        cast(Table, t).to_metadata(md)
        for t in (
            PortalOrg.__table__,
            PortalUser.__table__,
            PortalKnowledgeBase.__table__,
            PartnerAPIKey.__table__,
            PartnerApiKeyKbAccess.__table__,
        )
    ]
    for table in tables:
        for col in table.c:
            default = col.server_default
            if not isinstance(default, DefaultClause):
                continue
            if col.type.__class__.__name__ == "UUID" and not isinstance(default.arg, str):
                col.server_default = DefaultClause(text("gen_random_uuid()"))
            elif isinstance(default.arg, str) and "::" in default.arg:
                raw = default.arg if default.arg.startswith("'") else "'" + default.arg.replace("::", "'::", 1)
                col.server_default = DefaultClause(text(raw))
    return md


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    dsn = os.environ.get("RLS_TEST_DATABASE_URL", "")
    if not dsn:
        pytest.skip("RLS_TEST_DATABASE_URL not set — real-PostgreSQL handover tests skipped")
    engine = create_async_engine(dsn, connect_args={"server_settings": {"search_path": _SCHEMA}})
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE"))
        await conn.execute(text(f"CREATE SCHEMA {_SCHEMA}"))
        await conn.execute(text("CREATE TYPE telemetry_level_t AS ENUM ('off', 'shadow', 'full')"))
        await conn.execute(
            text("CREATE TYPE portal_user_role AS ENUM ('personal', 'company', 'kb_manager', 'group_manager', 'admin')")
        )
        await conn.run_sync(_schema_metadata().create_all)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            await _seed(session)
            yield session
    finally:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {_SCHEMA} CASCADE"))
        await engine.dispose()


async def _seed(db: AsyncSession) -> None:
    db.add_all(
        [
            PortalOrg(
                id=1,
                zitadel_org_id="z-org-1",
                name="Example One",
                slug="example-one",
                platform_unlocked_features=["partner_api"],
            ),
            PortalOrg(id=2, zitadel_org_id="z-org-2", name="Example Two", slug="example-two"),
        ]
    )
    await db.flush()
    db.add_all(
        [
            PortalUser(zitadel_user_id="uid-leaving", org_id=1, role="admin", status="active"),
            PortalUser(zitadel_user_id="uid-admin", org_id=1, role="admin", status="active"),
            PortalUser(zitadel_user_id="uid-other-admin", org_id=1, role="admin", status="active"),
            PortalUser(zitadel_user_id="uid-member", org_id=1, role="company", status="active"),
            PortalUser(zitadel_user_id="uid-suspended", org_id=1, role="admin", status="suspended"),
            PortalUser(zitadel_user_id="uid-foreign", org_id=2, role="admin", status="active"),
            PortalKnowledgeBase(id=10, org_id=1, name="Team", slug="team", created_by="uid-admin"),
            PortalKnowledgeBase(
                id=20,
                org_id=1,
                name="Leaving",
                slug="personal-leaving",
                created_by="uid-leaving",
                owner_type="user",
                owner_user_id="uid-leaving",
            ),
            PortalKnowledgeBase(
                id=30,
                org_id=1,
                name="Admin",
                slug="personal-admin",
                created_by="uid-admin",
                owner_type="user",
                owner_user_id="uid-admin",
            ),
            PortalKnowledgeBase(
                id=31,
                org_id=1,
                name="Other",
                slug="personal-other",
                created_by="uid-other-admin",
                owner_type="user",
                owner_user_id="uid-other-admin",
            ),
        ]
    )
    await db.flush()
    for key_id, token in (
        ("00000000-0000-0000-0000-00000000000a", _TOKEN_A),
        ("00000000-0000-0000-0000-00000000000b", _TOKEN_B),
    ):
        db.add(
            PartnerAPIKey(
                id=key_id,
                org_id=1,
                name=f"Integration {token[8]}",
                key_prefix=token[:12],
                key_hash=hashlib.sha256(token.encode()).hexdigest(),
                created_by="uid-leaving",
                rate_limit_rpm=77,
            )
        )
    await db.flush()
    # Rows on personal KBs other than the creator's own are legacy (the admin
    # API refuses them today); they are exactly what the creator rule would
    # turn live once created_by points at the owner of that personal KB.
    for key_id in ("00000000-0000-0000-0000-00000000000a", "00000000-0000-0000-0000-00000000000b"):
        for kb_id in (10, 20, 30, 31):
            db.add(PartnerApiKeyKbAccess(partner_api_key_id=key_id, kb_id=kb_id, access_level="read"))
    await db.commit()


async def _authenticate(db: AsyncSession, token: str):
    request = Request({"type": "http", "headers": [(b"authorization", f"Bearer {token}".encode())]})
    with (
        patch("app.api.partner_dependencies.get_redis_pool", AsyncMock(return_value=None)),
        patch("app.api.partner_dependencies._update_last_used", AsyncMock()),
    ):
        return await get_partner_key(request, db)


async def _creators(db: AsyncSession) -> list[str]:
    rows = await db.execute(select(PartnerAPIKey.created_by).order_by(PartnerAPIKey.id))
    return list(rows.scalars())


@pytest.mark.asyncio
async def test_handed_over_keys_keep_authenticating_under_the_acting_admin(db: AsyncSession) -> None:
    transferred = await transfer_user_api_keys("uid-leaving", "uid-admin", 1, db)
    await db.commit()

    assert transferred == 2
    assert await _creators(db) == ["uid-admin", "uid-admin"]
    for token in (_TOKEN_A, _TOKEN_B):
        ctx = await _authenticate(db, token)
        assert ctx.org_id == 1
        assert ctx.rate_limit_rpm == 77
        # The team KB stays; the recipient's own personal KB (30) is not
        # opened by the creator rule, and nobody's personal KB is kept.
        assert ctx.kb_access == {10: "read"}


@pytest.mark.asyncio
async def test_keys_go_to_the_member_the_admin_picked_without_reaching_their_personal_kb(db: AsyncSession) -> None:
    await check_api_keys_recipient("uid-other-admin", "uid-leaving", 1, db)
    await transfer_user_api_keys("uid-leaving", "uid-other-admin", 1, db)
    await db.commit()

    assert await _creators(db) == ["uid-other-admin", "uid-other-admin"]
    ctx = await _authenticate(db, _TOKEN_B)
    assert 31 not in ctx.kb_access
    assert ctx.kb_access == {10: "read"}


@pytest.mark.parametrize(
    ("recipient", "error_code"),
    [
        ("uid-leaving", "api_keys_recipient_is_leaving_user"),
        ("uid-suspended", "transfer_recipient_not_active"),
        ("uid-foreign", "transfer_recipient_not_member"),
        ("uid-member", "api_keys_recipient_not_admin"),
    ],
)
@pytest.mark.asyncio
async def test_an_ineligible_recipient_is_refused_and_the_keys_stay_put(
    db: AsyncSession, recipient: str, error_code: str
) -> None:
    with pytest.raises(HTTPException) as exc:
        await check_api_keys_recipient(recipient, "uid-leaving", 1, db)

    assert exc.value.status_code == 400
    detail = exc.value.detail
    assert isinstance(detail, dict)
    assert detail.get("error_code") == error_code
    assert await _creators(db) == ["uid-leaving", "uid-leaving"]
