"""Inviting an existing, fully-initialized account must add membership, not 500.

Reproduction for the 2026-08-12 report (request f2b514c8): a voys.be user
self-signed-up first; the admin invite then hit Zitadel 409 "User already
exists", the identity recovery correctly reused the ACTIVE account, but
``send_invite_code`` failed with 400 "User is already initialized
(COMMAND-EF34g)" — Zitadel refuses invite codes for accounts that already
completed setup. The exception leg turned that into a raw 500 in the UI and
rolled back the membership.

Contract (industry pattern — Slack/Notion/GitHub org invites): an invite for
an existing initialized account creates the membership and sends a
"you've been added to workspace X" notification instead of an activation
invite. Any OTHER invite-mail failure keeps the existing rollback+cleanup leg.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


def _initialized_400() -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://auth.example.com/v2/users/1/invite_code")
    resp = httpx.Response(
        400,
        request=req,
        text='{"code":9, "message":"User is already initialized (COMMAND-EF34g)"}',
    )
    return httpx.HTTPStatusError("400", request=req, response=resp)


def _other_400() -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://auth.example.com/v2/users/1/invite_code")
    resp = httpx.Response(400, request=req, text='{"code":3, "message":"Errors.User.Code.Invalid"}')
    return httpx.HTTPStatusError("400", request=req, response=resp)


def _org(org_id: int = 8, slug: str = "voys") -> MagicMock:
    org = MagicMock()
    org.id = org_id
    org.slug = slug
    return org


def _body(email: str = "thijs.joos@voys.be") -> MagicMock:
    body = MagicMock()
    body.email = email
    body.first_name = "Thijs"
    body.last_name = "Joos"
    return body


async def _run_persist(zit: MagicMock, notify: AsyncMock, cleanup: AsyncMock, db: AsyncMock):
    from app.api.admin.users import _persist_invited_user_and_send_code

    with (
        patch("app.api.admin.users.zitadel", zit),
        patch("app.api.admin.users.notify_user_join_approved", notify),
        patch(
            "app.services.default_knowledge_bases.create_default_personal_kb",
            AsyncMock(),
        ),
    ):
        await _persist_invited_user_and_send_code(
            db=db,
            org=_org(),
            body=_body(),
            zitadel_user_id="385955253213724721",
            user_row=MagicMock(),
            reactivated_membership=None,
            reactivated_existing_zitadel_user=False,
            invite_url_template="https://my.example.com/password/set?userID={{.UserID}}&code={{.Code}}",
            cleanup_zitadel_user=cleanup,
        )


class TestInviteInitializedAccount:
    @pytest.mark.asyncio
    async def test_already_initialized_adds_membership_and_notifies(self) -> None:
        zit = MagicMock()
        zit.send_invite_code = AsyncMock(side_effect=_initialized_400())
        notify = AsyncMock()
        cleanup = AsyncMock()
        db = AsyncMock()
        db.add = MagicMock()

        await _run_persist(zit, notify, cleanup, db)

        db.commit.assert_awaited_once()
        db.rollback.assert_not_awaited()
        cleanup.assert_not_awaited()
        notify.assert_awaited_once()
        kwargs = notify.await_args.kwargs
        assert kwargs["email"] == "thijs.joos@voys.be"
        assert kwargs["workspace_url"].startswith("https://")

    @pytest.mark.asyncio
    async def test_other_invite_mail_failure_keeps_rollback_leg(self) -> None:
        from fastapi import HTTPException

        zit = MagicMock()
        zit.send_invite_code = AsyncMock(side_effect=_other_400())
        notify = AsyncMock()
        cleanup = AsyncMock()
        db = AsyncMock()
        db.add = MagicMock()

        with pytest.raises(HTTPException):
            await _run_persist(zit, notify, cleanup, db)

        db.rollback.assert_awaited_once()
        cleanup.assert_awaited_once()
        notify.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_notify_failure_does_not_block_membership(self) -> None:
        """The membership grant is the real mutation; the mail is best-effort."""
        zit = MagicMock()
        zit.send_invite_code = AsyncMock(side_effect=_initialized_400())
        notify = AsyncMock(side_effect=RuntimeError("mailer down"))
        cleanup = AsyncMock()
        db = AsyncMock()
        db.add = MagicMock()

        await _run_persist(zit, notify, cleanup, db)

        db.commit.assert_awaited_once()
        cleanup.assert_not_awaited()
