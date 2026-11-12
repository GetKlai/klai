"""Keeping the one Zitadel identity of a person in step with their portal memberships."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import httpx
import structlog

from app.core.config import settings
from app.services.user_memberships import get_user_global_membership_state, get_user_membership_summary
from app.services.zitadel import zitadel

_slog = structlog.get_logger()

_INVITE_READY_STATES = {"USER_STATE_ACTIVE"}
_BROKEN_IMPORT_STATE = "USER_STATE_INITIAL"
_INACTIVE_STATE = "USER_STATE_INACTIVE"


class ZitadelIdentityRecoveryError(RuntimeError):
    """Raised when an existing Zitadel identity cannot be made invite-ready."""


@dataclass(frozen=True)
class InviteIdentityRecoveryResult:
    user_id: str
    created_new_user: bool = False
    reactivated_existing_user: bool = False


def email_hash_for_log(email: str) -> str:
    return hashlib.sha256(email.lower().encode("utf-8")).hexdigest()


def _extract_zitadel_user_state(user_response: dict) -> str:
    user = user_response.get("user")
    payload = user if isinstance(user, dict) else user_response
    state = payload.get("state") if isinstance(payload, dict) else None
    if not isinstance(state, str) or not state:
        raise ZitadelIdentityRecoveryError("Zitadel user response did not include a user state")
    return state


async def _identity_state(zitadel_user_id: str) -> str | None:
    """The identity's Zitadel state, or None when Zitadel no longer has the user."""
    try:
        return _extract_zitadel_user_state(await zitadel.get_user_by_id(zitadel_user_id))
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 404:
            raise
        _slog.warning("zitadel_identity_missing", zitadel_user_id=zitadel_user_id)
        return None


async def sync_identity_login(zitadel_user_id: str) -> None:
    """Let the identity sign in exactly when one of its committed memberships is active.

    Call after the ``portal_users`` change is committed. One identity serves
    every organisation the person belongs to, so suspending, offboarding or
    removing one membership deactivates it only when no other membership is
    active, and reactivating a membership reactivates it. Zitadel refuses
    ``_deactivate`` on an inactive user and ``_reactivate`` on one that is not
    inactive with a failed precondition (internal/command/user.go:
    Errors.User.AlreadyInactive, Errors.User.NotInactive), so the call follows
    the identity's current state. An initial identity cannot be deactivated
    (Errors.User.CantDeactivateInitial) and cannot sign in either (see
    ``ZitadelClient.invite_user``), so it is left as it is, as is an identity
    Zitadel no longer has.
    """
    has_active_membership = (await get_user_global_membership_state(zitadel_user_id)).active_count > 0
    state = await _identity_state(zitadel_user_id)
    if state is None:
        return
    if not has_active_membership and state not in (_INACTIVE_STATE, _BROKEN_IMPORT_STATE):
        await zitadel.lock_user(zitadel_user_id=zitadel_user_id, org_id=settings.zitadel_portal_org_id)
    elif has_active_membership and state == _INACTIVE_STATE:
        await zitadel.unlock_user(zitadel_user_id=zitadel_user_id, org_id=settings.zitadel_portal_org_id)


async def reactivate_identity_if_inactive(zitadel_user_id: str) -> bool:
    """Reactivate the identity for a membership that is being restored; True when it did.

    Used before the restored membership is committed, so it cannot count
    memberships the way ``sync_identity_login`` does. The identity stays
    active while another membership is active, and Zitadel refuses
    ``_reactivate`` on it, so only an inactive identity is reactivated. A
    caller that rolls back undoes exactly this reactivation and nothing another
    organisation still relies on.
    """
    if await _identity_state(zitadel_user_id) != _INACTIVE_STATE:
        return False
    await zitadel.unlock_user(zitadel_user_id=zitadel_user_id, org_id=settings.zitadel_portal_org_id)
    return True


async def recover_existing_zitadel_identity_for_invite(
    *,
    zitadel_user_id: str,
    email: str,
    first_name: str,
    last_name: str,
    preferred_language: str,
    org_id: int,
    zitadel_client=None,
) -> InviteIdentityRecoveryResult:
    """Return an invite-ready Zitadel identity for a globally existing email.

    This is used only after Zitadel rejected a fresh user create with 409 and
    we confirmed there is no membership in the current tenant. Active users are
    safe to reuse. Suspended/deactivated users can be unlocked when they still
    have other tenant memberships.

    A dangling ``USER_STATE_INITIAL`` identity is the old v1-import failure
    mode documented in ``ZitadelClient.invite_user``. It is not repaired by
    sending another invite code, so when it has no portal memberships we hard
    delete it and create a fresh v2 user. If it still has memberships, fail
    loudly instead of silently linking a tenant to a known-broken login state.
    """

    client = zitadel if zitadel_client is None else zitadel_client

    try:
        state = _extract_zitadel_user_state(await client.get_user_by_id(zitadel_user_id))
    except Exception as exc:
        raise ZitadelIdentityRecoveryError("Could not inspect existing Zitadel user state") from exc

    if state in _INVITE_READY_STATES:
        return InviteIdentityRecoveryResult(user_id=zitadel_user_id)

    membership_summary = await get_user_membership_summary(zitadel_user_id)

    if membership_summary.total_count == 0:
        try:
            await client.remove_user(
                org_id=settings.zitadel_portal_org_id,
                zitadel_user_id=zitadel_user_id,
            )
            user_data = await client.invite_user(
                org_id=settings.zitadel_portal_org_id,
                email=email,
                first_name=first_name,
                last_name=last_name,
                preferred_language=preferred_language,
            )
        except Exception as exc:
            raise ZitadelIdentityRecoveryError("Could not replace dangling Zitadel user") from exc

        new_user_id = user_data["userId"]
        _slog.info(
            "invite_dangling_zitadel_user_recreated",
            old_zitadel_user_id=zitadel_user_id,
            new_zitadel_user_id=new_user_id,
            email_hash=email_hash_for_log(email),
            org_id=org_id,
            previous_state=state,
        )
        return InviteIdentityRecoveryResult(user_id=new_user_id, created_new_user=True)

    if state == _BROKEN_IMPORT_STATE:
        _slog.error(
            "invite_existing_zitadel_user_initial_state_blocked",
            zitadel_user_id=zitadel_user_id,
            email_hash=email_hash_for_log(email),
            org_id=org_id,
            memberships=membership_summary.total_count,
        )
        raise ZitadelIdentityRecoveryError(
            "Existing Zitadel user is stuck in USER_STATE_INITIAL and has portal memberships"
        )

    try:
        await client.unlock_user(
            zitadel_user_id=zitadel_user_id,
            org_id=settings.zitadel_portal_org_id,
        )
    except Exception as exc:
        raise ZitadelIdentityRecoveryError("Could not reactivate existing Zitadel user") from exc

    _slog.info(
        "invite_existing_zitadel_user_unlocked",
        zitadel_user_id=zitadel_user_id,
        email_hash=email_hash_for_log(email),
        org_id=org_id,
        previous_state=state,
    )
    return InviteIdentityRecoveryResult(user_id=zitadel_user_id, reactivated_existing_user=True)
