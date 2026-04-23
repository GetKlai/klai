"""
Tests for SPEC-PORTAL-UNIFY-KB-001 Phase A: KBQuotaService (kb_quota.py).

Covers:
- assert_can_create_personal_kb raises 403 when user is at/above limit
- assert_can_create_personal_kb passes when user is below limit
- assert_can_create_personal_kb passes when limit is None (complete plan)
- assert_can_create_org_kb raises 403 when can_create_org_kbs is False
- assert_can_create_org_kb passes when can_create_org_kbs is True
- Error codes match SPEC: kb_quota_personal_kb_exceeded, kb_quota_org_kb_not_allowed
- K2: pg_advisory_xact_lock is issued before count query on PostgreSQL
- K2: pg_advisory_xact_lock is skipped on SQLite (test dialect)
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException


def _make_db_mock() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    return db


def _make_org(plan: str = "core") -> MagicMock:
    org = MagicMock()
    org.plan = plan
    org.id = 1
    return org


class TestAssertCanCreatePersonalKB:
    """assert_can_create_personal_kb enforces max_personal_kbs_per_user."""

    @pytest.mark.asyncio
    async def test_passes_when_user_has_no_kbs(self) -> None:
        from app.services.kb_quota import assert_can_create_personal_kb

        mock_db = _make_db_mock()
        # count query returns 0
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 0
        mock_db.execute.return_value = mock_result

        # Should not raise
        await assert_can_create_personal_kb(
            user_id="user-1",
            org=_make_org("core"),
            db=mock_db,
        )

    @pytest.mark.asyncio
    async def test_passes_when_user_has_less_than_limit(self) -> None:
        from app.services.kb_quota import assert_can_create_personal_kb

        mock_db = _make_db_mock()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 4  # 4 < 5 (core limit)
        mock_db.execute.return_value = mock_result

        await assert_can_create_personal_kb(
            user_id="user-1",
            org=_make_org("core"),
            db=mock_db,
        )

    @pytest.mark.asyncio
    async def test_raises_403_when_user_is_at_limit(self) -> None:
        from app.services.kb_quota import assert_can_create_personal_kb

        mock_db = _make_db_mock()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 5  # exactly at limit
        mock_db.execute.return_value = mock_result

        with pytest.raises(HTTPException) as exc_info:
            await assert_can_create_personal_kb(
                user_id="user-1",
                org=_make_org("core"),
                db=mock_db,
            )

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["error_code"] == "kb_quota_personal_kb_exceeded"

    @pytest.mark.asyncio
    async def test_raises_403_when_user_is_above_limit(self) -> None:
        from app.services.kb_quota import assert_can_create_personal_kb

        mock_db = _make_db_mock()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 7  # above limit (grandfathered)
        mock_db.execute.return_value = mock_result

        with pytest.raises(HTTPException) as exc_info:
            await assert_can_create_personal_kb(
                user_id="user-1",
                org=_make_org("core"),
                db=mock_db,
            )

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["error_code"] == "kb_quota_personal_kb_exceeded"

    @pytest.mark.asyncio
    async def test_error_detail_includes_plan_and_limit(self) -> None:
        from app.services.kb_quota import assert_can_create_personal_kb

        mock_db = _make_db_mock()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 5
        mock_db.execute.return_value = mock_result

        with pytest.raises(HTTPException) as exc_info:
            await assert_can_create_personal_kb(
                user_id="user-1",
                org=_make_org("core"),
                db=mock_db,
            )

        detail = exc_info.value.detail
        assert "plan" in detail
        assert "limit" in detail
        assert detail["plan"] == "core"
        assert detail["limit"] == 5

    @pytest.mark.asyncio
    async def test_passes_when_plan_is_complete_and_none_limit(self) -> None:
        """Complete plan has no limit (None = unlimited)."""
        from app.services.kb_quota import assert_can_create_personal_kb

        mock_db = _make_db_mock()
        # Even with 100 KBs, complete plan should pass
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 100
        mock_db.execute.return_value = mock_result

        await assert_can_create_personal_kb(
            user_id="user-complete",
            org=_make_org("complete"),
            db=mock_db,
        )

    @pytest.mark.asyncio
    async def test_db_execute_is_called_only_when_limit_exists(self) -> None:
        """When plan has None limit, skip the DB count query."""
        from app.services.kb_quota import assert_can_create_personal_kb

        mock_db = _make_db_mock()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 0
        mock_db.execute.return_value = mock_result

        await assert_can_create_personal_kb(
            user_id="user-complete",
            org=_make_org("complete"),
            db=mock_db,
        )
        # DB execute should NOT be called for unlimited plan
        mock_db.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_professional_plan_same_limits_as_core(self) -> None:
        from app.services.kb_quota import assert_can_create_personal_kb

        mock_db = _make_db_mock()
        mock_result = MagicMock()
        mock_result.scalar_one.return_value = 5  # at limit
        mock_db.execute.return_value = mock_result

        with pytest.raises(HTTPException) as exc_info:
            await assert_can_create_personal_kb(
                user_id="user-pro",
                org=_make_org("professional"),
                db=mock_db,
            )

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["limit"] == 5


class TestAssertCanCreateOrgKB:
    """assert_can_create_org_kb enforces can_create_org_kbs."""

    @pytest.mark.asyncio
    async def test_passes_for_complete_plan(self) -> None:
        from app.services.kb_quota import assert_can_create_org_kb

        mock_db = _make_db_mock()

        await assert_can_create_org_kb(org=_make_org("complete"), db=mock_db)

    @pytest.mark.asyncio
    async def test_raises_403_for_core_plan(self) -> None:
        from app.services.kb_quota import assert_can_create_org_kb

        mock_db = _make_db_mock()

        with pytest.raises(HTTPException) as exc_info:
            await assert_can_create_org_kb(org=_make_org("core"), db=mock_db)

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["error_code"] == "kb_quota_org_kb_not_allowed"

    @pytest.mark.asyncio
    async def test_raises_403_for_professional_plan(self) -> None:
        from app.services.kb_quota import assert_can_create_org_kb

        mock_db = _make_db_mock()

        with pytest.raises(HTTPException) as exc_info:
            await assert_can_create_org_kb(org=_make_org("professional"), db=mock_db)

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["error_code"] == "kb_quota_org_kb_not_allowed"

    @pytest.mark.asyncio
    async def test_error_detail_includes_plan(self) -> None:
        from app.services.kb_quota import assert_can_create_org_kb

        mock_db = _make_db_mock()

        with pytest.raises(HTTPException) as exc_info:
            await assert_can_create_org_kb(org=_make_org("core"), db=mock_db)

        detail = exc_info.value.detail
        assert "plan" in detail


def _make_kb(owner_type: str = "user", slug: str = "my-kb") -> MagicMock:
    kb = MagicMock()
    kb.owner_type = owner_type
    kb.slug = slug
    return kb


class TestAssertCanAddItemToKB:
    """assert_can_add_item_to_kb enforces max_items_per_kb for personal KBs.

    SPEC-PORTAL-UNIFY-KB-001 R-E2.
    """

    @pytest.mark.asyncio
    async def test_raises_403_when_personal_kb_at_limit_core_plan(self) -> None:
        """Core plan: 20 items in personal KB → 403 kb_quota_items_exceeded."""
        from unittest.mock import patch

        from app.services.kb_quota import assert_can_add_item_to_kb

        kb = _make_kb(owner_type="user")
        org = _make_org("core")

        with patch(
            "app.services.kb_quota.knowledge_ingest_client.get_source_count",
            new_callable=AsyncMock,
            return_value=20,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await assert_can_add_item_to_kb(kb=kb, org=org)

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["error_code"] == "kb_quota_items_exceeded"
        assert exc_info.value.detail["limit"] == 20
        assert exc_info.value.detail["current"] == 20

    @pytest.mark.asyncio
    async def test_passes_when_personal_kb_below_limit_core_plan(self) -> None:
        """Core plan: 19 items in personal KB → no error."""
        from unittest.mock import patch

        from app.services.kb_quota import assert_can_add_item_to_kb

        kb = _make_kb(owner_type="user")
        org = _make_org("core")

        with patch(
            "app.services.kb_quota.knowledge_ingest_client.get_source_count",
            new_callable=AsyncMock,
            return_value=19,
        ):
            # Should not raise
            await assert_can_add_item_to_kb(kb=kb, org=org)

    @pytest.mark.asyncio
    async def test_passes_for_complete_plan_unlimited(self) -> None:
        """Complete plan: max_items_per_kb is None → always allowed, no network call."""
        from unittest.mock import patch

        from app.services.kb_quota import assert_can_add_item_to_kb

        kb = _make_kb(owner_type="user")
        org = _make_org("complete")

        with patch(
            "app.services.kb_quota.knowledge_ingest_client.get_source_count",
            new_callable=AsyncMock,
        ) as mock_count:
            await assert_can_add_item_to_kb(kb=kb, org=org)
            mock_count.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_passes_for_org_kb_regardless_of_plan(self) -> None:
        """Org-scoped KB: quota not enforced (only complete users have org KBs)."""
        from unittest.mock import patch

        from app.services.kb_quota import assert_can_add_item_to_kb

        kb = _make_kb(owner_type="org")
        org = _make_org("core")

        with patch(
            "app.services.kb_quota.knowledge_ingest_client.get_source_count",
            new_callable=AsyncMock,
        ) as mock_count:
            await assert_can_add_item_to_kb(kb=kb, org=org)
            mock_count.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_fails_open_when_source_count_unavailable(self) -> None:
        """When knowledge-ingest is unreachable (None), fail open — allow the ingest."""
        from unittest.mock import patch

        from app.services.kb_quota import assert_can_add_item_to_kb

        kb = _make_kb(owner_type="user")
        org = _make_org("core")

        with patch(
            "app.services.kb_quota.knowledge_ingest_client.get_source_count",
            new_callable=AsyncMock,
            return_value=None,
        ):
            # Should NOT raise even for a core plan when count is unavailable
            await assert_can_add_item_to_kb(kb=kb, org=org)


class TestAdvisoryLockPersonalKB:
    """K2: assert_can_create_personal_kb serializes concurrent checks via
    pg_advisory_xact_lock on PostgreSQL, and skips the lock on SQLite.

    The advisory lock must be issued BEFORE the count query so that two
    concurrent requests cannot both see count < limit and both succeed.
    """

    def _make_pg_db_mock(self) -> AsyncMock:
        """Return an AsyncSession mock whose dialect reports 'postgresql'."""
        db = AsyncMock()
        db.add = MagicMock()

        # Simulate AsyncSession.connection() returning a connection whose
        # dialect.name is 'postgresql'.
        mock_conn = MagicMock()
        mock_conn.dialect = MagicMock()
        mock_conn.dialect.name = "postgresql"
        db.connection = AsyncMock(return_value=mock_conn)

        # get_bind is not available on AsyncSession — ensure it raises AttributeError
        # so the fallback path via db.connection() is exercised.
        del db.get_bind

        return db

    def _make_sqlite_db_mock(self) -> AsyncMock:
        """Return an AsyncSession mock whose dialect reports 'sqlite'."""
        db = AsyncMock()
        db.add = MagicMock()

        mock_conn = MagicMock()
        mock_conn.dialect = MagicMock()
        mock_conn.dialect.name = "sqlite"
        db.connection = AsyncMock(return_value=mock_conn)

        del db.get_bind

        return db

    @pytest.mark.asyncio
    async def test_postgresql_issues_advisory_lock_before_count(self) -> None:
        """K2: On PostgreSQL the function must call pg_advisory_xact_lock before
        the SELECT count(*) query.  Both calls go through db.execute; we verify
        that the lock call comes first.
        """
        from app.services.kb_quota import assert_can_create_personal_kb

        mock_db = self._make_pg_db_mock()

        # First execute call (lock): returns a trivial result.
        # Second execute call (count): returns result with scalar_one() == 0.
        lock_result = MagicMock()
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        mock_db.execute.side_effect = [lock_result, count_result]

        await assert_can_create_personal_kb(
            user_id="user-1",
            org=_make_org("core"),
            db=mock_db,
        )

        assert mock_db.execute.await_count == 2, "Expected exactly two db.execute calls: advisory lock + count query"
        # The first call must contain the advisory lock SQL.
        first_call_args = mock_db.execute.call_args_list[0]
        first_sql = str(first_call_args[0][0])  # positional arg 0 of first call
        assert "pg_advisory_xact_lock" in first_sql, (
            f"First db.execute call should be the advisory lock, got: {first_sql!r}"
        )

    @pytest.mark.asyncio
    async def test_postgresql_lock_keyed_on_org_id_and_user_hash(self) -> None:
        """K2: Lock parameters must include org_id and a deterministic hash of user_id."""
        from app.services.kb_quota import assert_can_create_personal_kb

        mock_db = self._make_pg_db_mock()

        lock_result = MagicMock()
        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        mock_db.execute.side_effect = [lock_result, count_result]

        await assert_can_create_personal_kb(
            user_id="user-abc",
            org=_make_org("core"),
            db=mock_db,
        )

        first_call_args = mock_db.execute.call_args_list[0][0]
        # The second positional argument to db.execute is the params dict.
        params = first_call_args[1]
        assert "org_id" in params, "Lock params must include org_id"
        assert "user_key" in params, "Lock params must include user_key (hash of user_id)"
        assert params["org_id"] == 1  # matches _make_org(plan).id = 1
        assert isinstance(params["user_key"], int), "user_key must be an integer (adler32 hash)"

    @pytest.mark.asyncio
    async def test_sqlite_skips_advisory_lock(self) -> None:
        """K2: On SQLite the advisory lock must NOT be issued.

        SQLite does not support pg_advisory_xact_lock; running it would fail.
        In tests (sqlite dialect) the function must issue exactly one db.execute
        call (the count query), with no advisory lock call.
        """
        from app.services.kb_quota import assert_can_create_personal_kb

        mock_db = self._make_sqlite_db_mock()

        count_result = MagicMock()
        count_result.scalar_one.return_value = 0
        mock_db.execute.return_value = count_result

        await assert_can_create_personal_kb(
            user_id="user-1",
            org=_make_org("core"),
            db=mock_db,
        )

        assert mock_db.execute.await_count == 1, "On SQLite only one db.execute call expected (count query, no lock)"
        # Verify the single call is NOT the advisory lock
        only_call_args = mock_db.execute.call_args_list[0][0]
        sql_text = str(only_call_args[0])
        assert "pg_advisory_xact_lock" not in sql_text, "Advisory lock must not be issued on SQLite"

    @pytest.mark.asyncio
    async def test_sqlite_still_enforces_quota(self) -> None:
        """K2: Skipping the lock on SQLite does not disable quota enforcement."""
        from app.services.kb_quota import assert_can_create_personal_kb

        mock_db = self._make_sqlite_db_mock()

        count_result = MagicMock()
        count_result.scalar_one.return_value = 5  # at limit
        mock_db.execute.return_value = count_result

        with pytest.raises(HTTPException) as exc_info:
            await assert_can_create_personal_kb(
                user_id="user-1",
                org=_make_org("core"),
                db=mock_db,
            )

        assert exc_info.value.status_code == 403
        assert exc_info.value.detail["error_code"] == "kb_quota_personal_kb_exceeded"

    @pytest.mark.asyncio
    async def test_unlimited_plan_skips_lock_and_count(self) -> None:
        """K2: Complete plan (None limit) must skip both the lock and the count query."""
        from app.services.kb_quota import assert_can_create_personal_kb

        mock_db = self._make_pg_db_mock()

        await assert_can_create_personal_kb(
            user_id="user-complete",
            org=_make_org("complete"),
            db=mock_db,
        )

        # No db.execute calls at all — unlimited plan short-circuits early.
        mock_db.execute.assert_not_awaited()
