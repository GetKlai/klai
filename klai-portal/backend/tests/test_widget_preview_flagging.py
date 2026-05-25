"""REQ-15 (Finding B-11, SPEC-SEC-CROSS-TENANT-FOLLOWUP-001):
admin preview-session conversations SHALL be flagged and excluded from stats.

AC15.1 — preview JWT carries is_preview=true claim
AC15.2 — record_widget_turn persists widget_conversations.is_preview = true
         when called with is_preview=True
AC15.3 — widget_activity_stats query filters out is_preview rows (covered
         here by SQL-source inspection; full SQL execution against a real
         DB is covered by the higher-level integration suite at deploy time)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest

from app.services.widget_auth import (
    decode_session_token,
    generate_session_token,
)

# ---------------------------------------------------------------------------
# AC15.1 — preview JWT carries is_preview claim
# ---------------------------------------------------------------------------


class TestPreviewJWTClaim:
    SECRET = "test-master-secret"
    TENANT = "acme"

    def test_preview_jwt_payload_carries_is_preview_true(self) -> None:
        token = generate_session_token(
            wgt_id="wgt_x",
            org_id=42,
            kb_ids=[1, 2],
            secret=self.SECRET,
            tenant_slug=self.TENANT,
            is_preview=True,
        )
        payload = decode_session_token(token, self.SECRET, self.TENANT)
        assert payload["is_preview"] is True

    def test_public_jwt_payload_has_is_preview_false(self) -> None:
        token = generate_session_token(
            wgt_id="wgt_x",
            org_id=42,
            kb_ids=[1, 2],
            secret=self.SECRET,
            tenant_slug=self.TENANT,
            # is_preview defaults to False
        )
        payload = decode_session_token(token, self.SECRET, self.TENANT)
        assert payload.get("is_preview", False) is False

    def test_each_minted_token_has_unique_jti(self) -> None:
        """Audit session keys must not collapse two mints in the same exp second."""
        from app.services.widget_audit import session_key_from_token

        token_a = generate_session_token(
            wgt_id="wgt_x",
            org_id=42,
            kb_ids=[1, 2],
            secret=self.SECRET,
            tenant_slug=self.TENANT,
            is_preview=True,
        )
        token_b = generate_session_token(
            wgt_id="wgt_x",
            org_id=42,
            kb_ids=[1, 2],
            secret=self.SECRET,
            tenant_slug=self.TENANT,
            is_preview=True,
        )
        payload_a = decode_session_token(token_a, self.SECRET, self.TENANT)
        payload_b = decode_session_token(token_b, self.SECRET, self.TENANT)

        assert payload_a["jti"]
        assert payload_b["jti"]
        assert payload_a["jti"] != payload_b["jti"]
        assert session_key_from_token(token_a) != session_key_from_token(token_b)

    def test_preview_token_decodes_with_correct_tenant_key(self) -> None:
        """HKDF-per-tenant binding still holds even with the new claim."""
        token = generate_session_token(
            wgt_id="wgt_x",
            org_id=42,
            kb_ids=[1, 2],
            secret=self.SECRET,
            tenant_slug=self.TENANT,
            is_preview=True,
        )
        with pytest.raises(jwt.InvalidTokenError):
            decode_session_token(token, self.SECRET, tenant_slug="different-tenant")


# ---------------------------------------------------------------------------
# AC15.2 — record_widget_turn writes is_preview into the conversation row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_widget_turn_persists_is_preview_true() -> None:
    from app.services.widget_audit import record_widget_turn

    captured: list[dict] = []

    async def _exec(sql, params=None):
        if params is not None:
            captured.append(dict(params))
        res = MagicMock()
        res.first.return_value = ("conv-uuid", 0)
        return res

    tenant_db = AsyncMock()
    tenant_db.execute = AsyncMock(side_effect=_exec)
    tenant_db.commit = AsyncMock()

    lookup_row = MagicMock()
    lookup_row.first.return_value = (42,)
    lookup_db = AsyncMock()
    lookup_db.execute = AsyncMock(return_value=lookup_row)

    with (
        patch("app.services.widget_audit.cross_org_session") as ctx_cross,
        patch("app.services.widget_audit.tenant_scoped_session") as ctx_tenant,
    ):
        ctx_cross.return_value.__aenter__ = AsyncMock(return_value=lookup_db)
        ctx_cross.return_value.__aexit__ = AsyncMock(return_value=False)
        ctx_tenant.return_value.__aenter__ = AsyncMock(return_value=tenant_db)
        ctx_tenant.return_value.__aexit__ = AsyncMock(return_value=False)

        await record_widget_turn(
            widget_id="00000000-0000-0000-0000-000000000001",
            session_key="ses",
            role="user",
            content="hi",
            is_preview=True,
        )

    conversation_params = [p for p in captured if "session_key" in p]
    assert conversation_params, "Expected the widget_conversations INSERT params"
    assert conversation_params[0]["is_preview"] is True


@pytest.mark.asyncio
async def test_record_widget_turn_defaults_is_preview_false() -> None:
    """Public chat path does NOT pass is_preview; it must default to False."""
    from app.services.widget_audit import record_widget_turn

    captured: list[dict] = []

    async def _exec(sql, params=None):
        if params is not None:
            captured.append(dict(params))
        res = MagicMock()
        res.first.return_value = ("conv-uuid", 0)
        return res

    tenant_db = AsyncMock()
    tenant_db.execute = AsyncMock(side_effect=_exec)
    tenant_db.commit = AsyncMock()

    lookup_row = MagicMock()
    lookup_row.first.return_value = (42,)
    lookup_db = AsyncMock()
    lookup_db.execute = AsyncMock(return_value=lookup_row)

    with (
        patch("app.services.widget_audit.cross_org_session") as ctx_cross,
        patch("app.services.widget_audit.tenant_scoped_session") as ctx_tenant,
    ):
        ctx_cross.return_value.__aenter__ = AsyncMock(return_value=lookup_db)
        ctx_cross.return_value.__aexit__ = AsyncMock(return_value=False)
        ctx_tenant.return_value.__aenter__ = AsyncMock(return_value=tenant_db)
        ctx_tenant.return_value.__aexit__ = AsyncMock(return_value=False)

        await record_widget_turn(
            widget_id="00000000-0000-0000-0000-000000000001",
            session_key="ses",
            role="user",
            content="hi",
        )

    conversation_params = [p for p in captured if "session_key" in p]
    assert conversation_params
    assert conversation_params[0]["is_preview"] is False


# ---------------------------------------------------------------------------
# AC15.3 — stats query filters out preview rows
# ---------------------------------------------------------------------------


def test_widget_activity_stats_sql_filters_preview_rows() -> None:
    """SQL-source inspection: every aggregate in widget_activity_stats
    contains ``is_preview = false`` so admin probing does not pollute totals.
    """
    import inspect

    from app.api.admin_widgets import widget_activity_stats

    source = inspect.getsource(widget_activity_stats)
    # All six SQL bodies in the handler must reference is_preview = false:
    # totals/top/hourly, each with cutoff and all-time branches.
    assert source.count("is_preview = false") >= 6, (
        "widget_activity_stats must filter every aggregate on is_preview = false; "
        f"found {source.count('is_preview = false')} occurrences. "
        "REQ-15 requires the stats query to exclude admin-preview conversations."
    )
