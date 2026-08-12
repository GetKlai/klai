"""
Zitadel management API client.
All calls use the portal-api service account PAT — never exposed to the browser.
"""

import asyncio
import logging
import time
from typing import Literal

import httpx

from app.core.config import settings
from app.core.provisioning_names import validate_slug_for_provisioning
from app.utils.response_sanitizer import sanitize_response_body  # SPEC-SEC-INTERNAL-001 REQ-4

logger = logging.getLogger(__name__)


class ZitadelClient:
    @staticmethod
    async def _log_response_errors(response: httpx.Response) -> None:
        """Log error responses from Zitadel API."""
        if response.is_error:
            await response.aread()
            logger.error(
                "Zitadel API %s %s failed: status=%d, body=%s",
                response.request.method,
                response.url.path,
                response.status_code,
                sanitize_response_body(response, max_len=200),
            )

    _USERINFO_TTL = 60  # seconds

    def __init__(self) -> None:
        self._http = httpx.AsyncClient(
            base_url=settings.zitadel_base_url,
            headers={
                "Authorization": f"Bearer {settings.zitadel_pat}",
                "Content-Type": "application/json",
            },
            timeout=15.0,
            event_hooks={"response": [self._log_response_errors]},
        )
        self._userinfo_cache: dict[str, tuple[float, dict]] = {}
        # Singleflight: coalesce concurrent userinfo requests for the same token
        self._userinfo_inflight: dict[str, asyncio.Future[dict]] = {}

    async def close(self) -> None:
        await self._http.aclose()

    # ── Org management ────────────────────────────────────────────────────────

    async def create_org(self, name: str) -> dict:
        """Create a new Zitadel organisation and return its details."""
        resp = await self._http.post("/management/v1/orgs", json={"name": name})
        resp.raise_for_status()
        return resp.json()

    async def get_password_complexity_policy(self) -> dict:
        """Return the IAM password complexity policy from Zitadel Admin API."""
        resp = await self._http.get("/admin/v1/policies/password/complexity")
        resp.raise_for_status()
        return resp.json()

    async def delete_org(self, org_id: str) -> None:
        """Delete a Zitadel organisation and cascade-delete all its users + grants.

        Idempotent: 404 means the org is already absent, which is fine for
        deprovisioning re-runs. All other non-2xx responses are propagated
        via raise_for_status().

        Requires IAM_OWNER role on the PAT (settings.zitadel_pat). Per A4 in
        SPEC-INFRA-TENANT-DELETE-001: Zitadel cascades users and grants when
        the org is deleted — no per-user step is needed.

        # @MX:NOTE: SPEC-INFRA-TENANT-DELETE-001 Phase 5 — called by step 15
        #   (_delete_zitadel_org) in deprovisioning_orchestrator.
        """
        # Zitadel Management API `RemoveOrg` lives at `/management/v1/orgs/me`;
        # the `x-zitadel-orgid` header selects which org "me" resolves to.
        # `/management/v1/orgs` (without `/me`) is the create endpoint — POST
        # only — so DELETE on it returns 405 Method Not Allowed.
        # SPEC-INFRA-TENANT-DELETE-003 Bug E.
        resp = await self._http.delete(
            "/management/v1/orgs/me",
            headers={"x-zitadel-orgid": org_id},
        )
        # Idempotent: 404 (gone) and 403 (Zitadel sometimes returns this when
        # the calling identity no longer has any grant on a deleted org) both
        # mean "already absent — fine".
        if resp.status_code in (403, 404):
            # File uses stdlib logging (not structlog) — kwargs would be
            # treated as `extra` not structured fields. Use %-style instead.
            logger.info("zitadel_org_already_absent org_id=%s status=%d", org_id, resp.status_code)
            return
        resp.raise_for_status()

    # ── User management ───────────────────────────────────────────────────────

    async def create_human_user_v2_with_verify(
        self,
        org_id: str,
        email: str,
        first_name: str,
        last_name: str,
        password: str,
        *,
        url_template: str,
        preferred_language: str = "nl",
    ) -> dict:
        """Create a human user and send the Klai verify-email link.

        The v2 AddHumanUser endpoint accepts
        ``email.verification.sendCode.urlTemplate`` in the same request as user
        creation. That keeps self-service signup on the Klai-branded
        ``/verify`` page instead of Zitadel's hosted initialization UI.
        """
        resp = await self._http.post(
            "/v2/users/human",
            json={
                "username": email.lower(),
                "organization": {"orgId": org_id},
                "profile": {
                    "givenName": first_name,
                    "familyName": last_name,
                    "displayName": f"{first_name} {last_name}",
                    "preferredLanguage": preferred_language,
                },
                "email": {
                    "email": email,
                    "verification": {"sendCode": {"urlTemplate": url_template}},
                },
                "password": {
                    "password": password,
                    "changeRequired": False,
                },
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def create_human_user(
        self,
        org_id: str,
        email: str,
        first_name: str,
        last_name: str,
        password: str,
        preferred_language: str = "nl",
        send_codes: bool = True,
        is_email_verified: bool = False,
    ) -> dict:
        """Create a human user inside a specific org.

        ``userName`` is lowercased before submission to Zitadel. Email
        addresses are case-insensitive per RFC 5321 §2.4, but Zitadel
        stores the userName / loginName byte-for-byte and matches against
        it case-sensitively in some downstream calls (notably
        ``/v2/sessions`` user check). Storing only the lowercase form
        eliminates a class of "user signed up as Steven@... but typed
        steven@... at login" issues at the source. The display ``email``
        field keeps its original case for outgoing mail headers.

        ``send_codes`` controls whether Zitadel auto-fires the InitCode
        notification on import. Default ``True`` preserves Zitadel's
        initialization flow; klai-mailer renders that event with Klai branding.
        """
        resp = await self._http.post(
            "/management/v1/users/human/_import",
            headers={"x-zitadel-orgid": org_id},
            json={
                "userName": email.lower(),
                "profile": {
                    "firstName": first_name,
                    "lastName": last_name,
                    "displayName": f"{first_name} {last_name}",
                    "preferredLanguage": preferred_language,
                },
                "email": {
                    "email": email,
                    "isEmailVerified": is_email_verified,
                },
                "password": password,
                "passwordChangeRequired": False,
                "sendCodes": send_codes,
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def get_user_by_id(self, user_id: str) -> dict:
        resp = await self._http.get(f"/management/v1/users/{user_id}")
        resp.raise_for_status()
        return resp.json()

    # ── Role assignment ───────────────────────────────────────────────────────

    async def grant_user_role(self, org_id: str, user_id: str, role: str) -> None:
        """Assign a project role to a specific user (user grant)."""
        resp = await self._http.post(
            f"/management/v1/users/{user_id}/grants",
            headers={"x-zitadel-orgid": org_id},
            json={
                "projectId": settings.zitadel_project_id,
                "roleKeys": [role],
            },
        )
        resp.raise_for_status()

    async def list_user_grants(self, org_id: str, user_id: str) -> list[dict]:
        """List all project grants for a user in a specific org."""
        resp = await self._http.post(
            "/management/v1/users/grants/_search",
            headers={"x-zitadel-orgid": org_id},
            json={
                "queries": [
                    {"userIdQuery": {"userId": user_id}},
                    {"projectIdQuery": {"projectId": settings.zitadel_project_id}},
                ]
            },
        )
        resp.raise_for_status()
        return resp.json().get("result", [])

    async def remove_user_role(self, org_id: str, user_id: str, role: str) -> None:
        """Remove a project role grant from a user.

        Looks up the grant ID for the project and role, then deletes it.
        No-op if no matching grant is found (idempotent).

        # @MX:NOTE: [AUTO] Called on admin→non-admin demotion to revoke org:owner JWT claim.
        # @MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-5
        """
        grants = await self.list_user_grants(org_id=org_id, user_id=user_id)
        for grant in grants:
            if _grant_has_project_role(grant, role):
                grant_id = grant["id"]
                resp = await self._http.delete(
                    f"/management/v1/users/{user_id}/grants/{grant_id}",
                    headers={"x-zitadel-orgid": org_id},
                )
                resp.raise_for_status()
                return
        # No matching grant found — already removed or never granted. Treat as success.

    async def list_org_users(self, org_id: str) -> list[dict]:
        """List all human users in a Zitadel org."""
        resp = await self._http.post(
            "/management/v1/users/_search",
            headers={"x-zitadel-orgid": org_id},
            json={"queries": [{"typeQuery": {"type": "TYPE_HUMAN"}}]},
        )
        resp.raise_for_status()
        return resp.json().get("result", [])

    async def invite_user(
        self,
        org_id: str,
        email: str,
        first_name: str,
        last_name: str,
        preferred_language: str = "nl",
    ) -> dict:
        """Create a human user WITHOUT sending the activation email.

        Uses the v2 ``/v2/users/human`` (AddHumanUser) endpoint. The legacy
        v1 ``/management/v1/users/human/_import`` path is broken on Zitadel
        6.x: it leaves every user permanently in ``USER_STATE_INITIAL``
        (``isEmailVerified`` is ignored and ``invite_code/verify`` never
        clears INITIAL). An INITIAL user's project grant is not effective, so
        OIDC login fails with ``Errors.User.ProjectRequired`` — i.e. every
        invited user was unable to log in. Verified end-to-end on 6.1.6:
        v1 import → INITIAL forever; v2 AddHumanUser → ``USER_STATE_ACTIVE``
        immediately, and the downstream invite_code + set-password flow then
        works unchanged.

        No password is set here — the user picks one via the
        :meth:`send_invite_code` link to ``my.getklai.com/password/set``.
        The email is created pre-verified (``isVerified``) and NO verification
        code is generated, so the only onboarding mail is the invite issued by
        :meth:`send_invite_code`. (``returnCode`` was tried first but still
        fires ``user.human.email.code.added``; the Klai mailer sends on that
        event, producing a duplicate "Confirm your email" mail whose code
        collided with the invite code and broke onboarding — 2026-05-22.)

        ``username`` is lowercased; the display ``email`` keeps its case so
        the invite mail addresses the user the way the admin typed it.
        """
        resp = await self._http.post(
            "/v2/users/human",
            json={
                "username": email.lower(),
                "organization": {"orgId": org_id},
                "profile": {
                    "givenName": first_name,
                    "familyName": last_name,
                    "displayName": f"{first_name} {last_name}",
                    "preferredLanguage": preferred_language,
                },
                "email": {
                    "email": email,
                    # isVerified => create the user with the email already
                    # verified and generate NO email-verification code.
                    #
                    # Why NOT returnCode: returnCode only suppresses Zitadel's
                    # OWN SMTP, but the `user.human.email.code.added` HTTP-
                    # notification event STILL fires, and the Klai mailer sends
                    # a "Confirm your email" mail on it (same gotcha as v1's
                    # `sendCodes:false`). That second mail + its code collided
                    # with the invite-code flow, so every invited owner got
                    # "Code is invalid" on verify/set-password (2026-05-22
                    # onboarding incident). The mailer cannot drop that event
                    # because self-signup uses the same `email.code.added`.
                    #
                    # invite_user is admin-invite-only (always paired with
                    # send_invite_code). The invite link goes solely to the
                    # email owner, who proves ownership by accepting it and
                    # setting a password — so pre-verifying the address here is
                    # safe and leaves exactly ONE code (the invite code).
                    "isVerified": True,
                },
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def send_invite_code(
        self,
        user_id: str,
        *,
        url_template: str,
        application_name: str = "Klai",
    ) -> None:
        """Issue (or re-issue) a Zitadel invite code and mail it to the user.

        SPEC-PORTAL-AUTH-EMAIL-LINKS-001 REQ-2 / REQ-3. Used both by the
        new-user invite flow (right after ``invite_user``) and the
        resend-invite flow (replaces the legacy ``resend_init_mail``).

        Body shape (per zitadel/user/v2/user.proto::SendInviteCode):

        .. code-block:: json

            {
              "sendCode": {
                "urlTemplate": "https://my.getklai.com/password/set?userID=...",
                "applicationName": "Klai"
              }
            }

        REQ-10 — ``url_template`` MUST be set explicitly on every call.
        Zitadel caches the previous url_template per user; relying on the
        cache means a stale Zitadel-default URL silently wins on the next
        resend if the previous call did not pass urlTemplate.
        """
        resp = await self._http.post(
            f"/v2/users/{user_id}/invite_code",
            json={
                "sendCode": {
                    "urlTemplate": url_template,
                    "applicationName": application_name,
                },
            },
        )
        resp.raise_for_status()

    async def verify_user_email(self, org_id: str, user_id: str, code: str) -> None:
        """Verify a user's email address using a v2 email verification code.

        ``org_id`` is kept for the public method contract and legacy callers,
        but v2 ``VerifyEmail`` is user-scoped and does not need an org header.
        """
        resp = await self._http.post(
            f"/v2/users/{user_id}/email/verify",
            json={"verificationCode": code},
        )
        resp.raise_for_status()

    async def remove_user(self, org_id: str, zitadel_user_id: str) -> None:
        """Permanently delete a Zitadel user account (Management RemoveUser).

        Issues ``DELETE /management/v1/users/{id}`` — this REMOVES the account,
        it is NOT a soft deactivate. For login-disable-without-delete use
        :meth:`deactivate_user`. Idempotent: 403/404 are logged and treated as
        already-absent (returns without raising), so callers that need to
        confirm actual removal must verify via :meth:`get_user_by_id`.
        """
        resp = await self._http.delete(
            f"/management/v1/users/{zitadel_user_id}",
            headers={"x-zitadel-orgid": org_id},
        )
        if resp.status_code in (403, 404):
            logger.info(
                "zitadel_user_already_absent org_id=%s user_id=%s status=%d",
                org_id,
                zitadel_user_id,
                resp.status_code,
            )
            return
        resp.raise_for_status()

    # @MX:WARN external API call - deactivation is irreversible
    async def deactivate_user(self, user_id: str, org_id: str) -> None:
        """Deactivate a user in Zitadel (login disabled, not deleted)."""
        resp = await self._http.post(
            f"/management/v1/users/{user_id}/_deactivate",
            headers={"x-zitadel-orgid": org_id},
            json={},
        )
        resp.raise_for_status()

    # REQ-12 (Finding A-6, SPEC-SEC-CROSS-TENANT-FOLLOWUP-001): lock / unlock
    # helpers wrap Zitadel's _deactivate / _reactivate endpoints. portal-admin
    # calls these whenever portal_users.status transitions to/from "suspended"
    # so the JWT-direct services (retrieval-api, connector) cannot keep
    # accepting tokens for a paused account.
    # @MX:SPEC SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-12
    async def lock_user(self, zitadel_user_id: str, org_id: str) -> None:
        """Lock a Zitadel user (cannot login). Reversible via ``unlock_user``."""
        resp = await self._http.post(
            f"/management/v1/users/{zitadel_user_id}/_deactivate",
            headers={"x-zitadel-orgid": org_id},
            json={},
        )
        resp.raise_for_status()

    async def unlock_user(self, zitadel_user_id: str, org_id: str) -> None:
        """Unlock a Zitadel user (login re-enabled). Inverse of ``lock_user``."""
        resp = await self._http.post(
            f"/management/v1/users/{zitadel_user_id}/_reactivate",
            headers={"x-zitadel-orgid": org_id},
            json={},
        )
        resp.raise_for_status()

    # ── Token introspection ───────────────────────────────────────────────────

    # @MX:ANCHOR fan_in=8 — called by /api/me, _get_caller_org, get_current_user_id, and others
    async def get_userinfo(self, access_token: str) -> dict:
        """Get user info from an OIDC access token (for /api/me).

        Results are cached for _USERINFO_TTL seconds per token to reduce
        Zitadel API load on multi-endpoint requests within a session.

        Concurrent requests for the same token are coalesced (singleflight)
        so that N parallel /api/me calls produce at most 1 Zitadel request.

        In auth dev mode, returns mock userinfo without calling Zitadel.
        """
        if settings.is_auth_dev_mode:
            return {
                "sub": settings.auth_dev_user_id,
                "urn:zitadel:iam:org:project:roles": {"org:owner": {}},
            }

        now = time.monotonic()
        cached = self._userinfo_cache.get(access_token)
        if cached and (now - cached[0]) < self._USERINFO_TTL:
            return cached[1]

        # Singleflight: if another coroutine is already fetching for this
        # token, await its result instead of making a duplicate request.
        inflight = self._userinfo_inflight.get(access_token)
        if inflight is not None:
            return await inflight

        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict] = loop.create_future()
        self._userinfo_inflight[access_token] = future

        try:
            resp = await self._http.get(
                "/oidc/v1/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            data = resp.json()

            # Evict expired entries to prevent unbounded growth
            if len(self._userinfo_cache) > 500:
                cutoff = now - self._USERINFO_TTL
                self._userinfo_cache = {k: v for k, v in self._userinfo_cache.items() if v[0] > cutoff}
            self._userinfo_cache[access_token] = (time.monotonic(), data)

            future.set_result(data)
            return data
        except BaseException as exc:
            future.set_exception(exc)
            raise
        finally:
            self._userinfo_inflight.pop(access_token, None)

    # ── Custom Login UI (Session API) ─────────────────────────────────────────

    async def create_session_with_password(self, user_id: str, password: str) -> dict:
        """Create a Zitadel session for the given Zitadel ``user_id`` with the
        supplied password.

        ``user_id`` MUST be the canonical Zitadel userId resolved from the
        user-supplied email via ``find_user_by_email`` (which is itself
        case-insensitive per RFC 5321 §2.4). Passing the raw user-typed
        email here is wrong: Zitadel's ``/v2/sessions`` user check matches
        ``loginName`` case-sensitively against the stored value, so a user
        whose Zitadel ``loginName`` is ``Steven@getklai.com`` cannot log in
        by typing ``steven@getklai.com`` — Zitadel returns HTTP 400 and the
        portal returns 401 "Email address or password is incorrect". The
        IGNORE_CASE fix on ``find_user_by_email`` (commit 7e92e089) closed
        the lookup half of this gap; this signature closes the session-
        creation half.

        Returns the full response dict containing ``sessionId`` and
        ``sessionToken``. Raises ``httpx.HTTPStatusError`` on invalid
        credentials (4xx) or unknown ``user_id`` (also 4xx).
        """
        resp = await self._http.post(
            "/v2/sessions",
            json={
                "checks": {
                    "user": {"userId": user_id},
                    "password": {"password": password},
                }
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def finalize_auth_request(self, auth_request_id: str, session_id: str, session_token: str) -> str:
        """Connect an authenticated session to an OIDC auth request.

        Returns the ``callbackUrl`` the browser should be redirected to.
        Requires the service account to have the ``IAM_LOGIN_CLIENT`` role.
        """
        resp = await self._http.post(
            f"/v2/oidc/auth_requests/{auth_request_id}",
            json={"session": {"sessionId": session_id, "sessionToken": session_token}},
        )
        resp.raise_for_status()
        return resp.json()["callbackUrl"]

    async def set_password_with_code(
        self,
        user_id: str,
        code: str,
        new_password: str,
        *,
        invite_retry_url_template: str | None = None,
    ) -> Literal["invite", "reset"]:
        """Set a new password using a one-time code from email.

        Returns ``"invite"`` if the code was consumed via the invite flow
        (``invite_code/verify`` + ``PATCH /users`` with the first password)
        and ``"reset"``
        if it was consumed via the legacy single-call reset flow
        (``password`` with verificationCode). Callers should record this
        in their structured logs so operators can split metrics by path.

        Zitadel has TWO distinct code flows that both land on Klai's
        ``/password/set`` page:

        - **Password reset** (from ``/password/forgot``) — one API call:
          ``POST /v2/users/{id}/password`` with ``{newPassword, verificationCode}``.
          Zitadel consumes the password_reset code and sets the password
          atomically.

        - **Invite** (from admin-invite mail) — two API calls:
          1. ``POST /v2/users/{id}/invite_code/verify`` with
             ``{verificationCode: code}`` — verifies the invite code AND
             marks the user's email as verified.
          2. ``PATCH /v2/users/{id}`` with ``human.password`` — sets the
             first password on the now-verified user.

          The invite_code consumer does NOT accept reset-style codes, and
          the password endpoint does NOT accept invite codes. Without this
          split the invite-flow returns 400 ``invalid_code``.

        We try the invite-flow first because invite-codes are the more
        common case (every newly invited user goes through it). If the
        verify step returns a 4xx we fall back to the reset-flow.

        If the invite verify step succeeds but the later password PATCH fails,
        the invite code has already been consumed by Zitadel. When
        ``invite_retry_url_template`` is provided, we immediately issue a fresh
        invite code before re-raising the original PATCH error so the user is
        not left with only an exhausted link.

        Raises ``httpx.HTTPStatusError`` (caller maps to 400/502) when both
        flows fail, Zitadel returns a 5xx during verify, or the invite password
        PATCH fails.
        """
        # ---- Path 1: invite flow ------------------------------------------------
        verify_resp = await self._http.post(
            f"/v2/users/{user_id}/invite_code/verify",
            json={"verificationCode": code},
        )
        if verify_resp.is_success:
            # Invite verified; set the user's first password with the v2
            # update-user API. The deprecated /password endpoint expects
            # currentPassword or a password-reset verificationCode, and can
            # reject this step after the one-time invite code is already
            # consumed.
            password_resp = await self._http.patch(
                f"/v2/users/{user_id}",
                json={
                    "human": {
                        "password": {
                            "password": {"password": new_password, "changeRequired": False},
                        }
                    }
                },
            )
            try:
                password_resp.raise_for_status()
            except httpx.HTTPStatusError:
                if invite_retry_url_template is not None:
                    try:
                        await self.send_invite_code(user_id, url_template=invite_retry_url_template)
                    except Exception:
                        logger.exception(
                            "invite_code_reissue_after_password_failure_failed zitadel_status=%d",
                            password_resp.status_code,
                        )
                    else:
                        logger.warning(
                            "invite_code_reissued_after_password_failure zitadel_status=%d",
                            password_resp.status_code,
                        )
                raise
            return "invite"

        # invite_code/verify returned 4xx/5xx. Two reasons it might:
        #   - 4xx: the code is not an invite code (likely a password-reset code).
        #          Fall back to the reset-flow below.
        #   - 5xx: Zitadel itself is unhealthy. Propagate as HTTPStatusError so
        #          the caller emits zitadel_5xx and returns 502.
        if verify_resp.status_code >= 500:
            verify_resp.raise_for_status()

        # ---- Path 2: reset flow (1 call) ---------------------------------------
        reset_resp = await self._http.post(
            f"/v2/users/{user_id}/password",
            json={
                "newPassword": {"password": new_password, "changeRequired": False},
                "verificationCode": code,
            },
        )
        reset_resp.raise_for_status()
        return "reset"

    async def find_user_id_by_email(self, email: str) -> str | None:
        """Return the Zitadel userId for the given email, or None if not found.

        Email matching is case-insensitive: Zitadel stores loginName with the
        original case the user signed up with, but email addresses are
        case-insensitive per RFC 5321 §2.4. Without IGNORE_CASE, a user typing
        "steven@..." is silently not found if their loginName is "Steven@...",
        which breaks the password-reset flow (returns 204 without sending mail).
        """
        resp = await self._http.post(
            "/v2/users",
            json={
                "queries": [{"loginNameQuery": {"loginName": email, "method": "TEXT_QUERY_METHOD_EQUALS_IGNORE_CASE"}}]
            },
        )
        resp.raise_for_status()
        result = resp.json().get("result", [])
        if not result:
            return None
        return result[0]["userId"]

    async def update_user_profile(
        self,
        org_id: str,
        user_id: str,
        first_name: str,
        last_name: str,
        preferred_language: str,
    ) -> None:
        """Update name and preferredLanguage on a Zitadel user profile."""
        get_resp = await self._http.get(
            f"/management/v1/users/{user_id}",
            headers={"x-zitadel-orgid": org_id},
        )
        get_resp.raise_for_status()
        profile = get_resp.json().get("user", {}).get("human", {}).get("profile", {})
        put_resp = await self._http.put(
            f"/management/v1/users/{user_id}/profile",
            headers={"x-zitadel-orgid": org_id},
            json={
                "firstName": first_name,
                "lastName": last_name,
                "displayName": f"{first_name} {last_name}",
                "preferredLanguage": preferred_language,
                "gender": profile.get("gender", "GENDER_UNSPECIFIED"),
            },
        )
        # Zitadel returns 400 with code 9 when nothing changed — treat as success
        if put_resp.status_code == 400 and put_resp.json().get("code") == 9:
            return
        put_resp.raise_for_status()

    async def update_user_language(self, org_id: str, user_id: str, language: str) -> None:
        """Update the preferredLanguage on a Zitadel user profile."""
        get_resp = await self._http.get(
            f"/management/v1/users/{user_id}",
            headers={"x-zitadel-orgid": org_id},
        )
        get_resp.raise_for_status()
        profile = get_resp.json().get("user", {}).get("human", {}).get("profile", {})
        put_resp = await self._http.put(
            f"/management/v1/users/{user_id}/profile",
            headers={"x-zitadel-orgid": org_id},
            json={
                "firstName": profile.get("firstName", ""),
                "lastName": profile.get("lastName", ""),
                "displayName": profile.get("displayName", ""),
                "preferredLanguage": language,
                "gender": profile.get("gender", "GENDER_UNSPECIFIED"),
            },
        )
        put_resp.raise_for_status()

    # resend_init_mail was deleted in SPEC-PORTAL-AUTH-EMAIL-LINKS-001 REQ-3.
    # Use ``send_invite_code(user_id, url_template=...)`` instead — same API
    # call, but with an explicit Klai urlTemplate instead of {} (which made
    # Zitadel default to its own hosted UI).

    async def send_password_reset(self, user_id: str, *, url_template: str) -> None:
        """Trigger Zitadel to send a password reset email to the user.

        SPEC-PORTAL-AUTH-EMAIL-LINKS-001 REQ-1: ``url_template`` is keyword-only
        and required. Zitadel substitutes ``{{.UserID}}``, ``{{.Code}}`` and
        ``{{.OrgID}}`` server-side before mailing the link. Callers MUST build
        the template via :func:`app.services.auth_links.build_url_template` so
        every Klai mail-link points at the same frontend route.

        Body shape (per zitadel/user/v2/password.proto::SendPasswordResetLink):

        .. code-block:: json

            {
              "sendLink": {
                "notificationType": "NOTIFICATION_TYPE_Email",
                "urlTemplate": "https://my.getklai.com/password/set?userID=..."
              }
            }
        """
        resp = await self._http.post(
            f"/v2/users/{user_id}/password_reset",
            json={
                "sendLink": {
                    "notificationType": "NOTIFICATION_TYPE_Email",
                    "urlTemplate": url_template,
                },
            },
        )
        resp.raise_for_status()

    # ── MFA / TOTP ────────────────────────────────────────────────────────────

    async def find_user_by_email(self, email: str) -> tuple[str, str] | None:
        """Return (userId, orgId) for the given email, or None if not found.

        Case-insensitive — see find_user_id_by_email for rationale. Used by
        login + MFA flows where the user types their email manually.
        """
        resp = await self._http.post(
            "/v2/users",
            json={
                "queries": [{"loginNameQuery": {"loginName": email, "method": "TEXT_QUERY_METHOD_EQUALS_IGNORE_CASE"}}]
            },
        )
        resp.raise_for_status()
        result = resp.json().get("result", [])
        if not result:
            return None
        user = result[0]
        return user["userId"], user["details"]["resourceOwner"]

    async def has_totp(self, user_id: str, org_id: str | None = None) -> bool:
        """Return True if the user has a verified TOTP factor registered."""
        resp = await self._http.get(f"/v2/users/{user_id}/authentication_methods")
        resp.raise_for_status()
        methods = resp.json().get("authMethodTypes", [])
        return "AUTHENTICATION_METHOD_TYPE_TOTP" in methods

    async def has_any_mfa(self, user_id: str) -> bool:
        """Return True if the user has any second factor registered (TOTP, passkey, email OTP)."""
        if settings.is_auth_dev_mode:
            return False
        resp = await self._http.get(f"/v2/users/{user_id}/authentication_methods")
        resp.raise_for_status()
        methods = resp.json().get("authMethodTypes", [])
        mfa_types = {
            "AUTHENTICATION_METHOD_TYPE_TOTP",
            "AUTHENTICATION_METHOD_TYPE_U2F",
            "AUTHENTICATION_METHOD_TYPE_OTP_EMAIL",
            "AUTHENTICATION_METHOD_TYPE_OTP_SMS",
        }
        return bool(mfa_types & set(methods))

    async def start_passkey_registration(self, user_id: str, domain: str) -> dict:
        """Start WebAuthn passkey registration for a user.

        Returns { passkeyId, publicKeyCredentialCreationOptions } from Zitadel.
        The options must be forwarded to the browser to call navigator.credentials.create().
        Verify endpoint: POST /v2/users/{userId}/passkeys/{passkeyId}
        """
        resp = await self._http.post(
            f"/v2/users/{user_id}/passkeys",
            json={"domain": domain},
        )
        resp.raise_for_status()
        return resp.json()

    async def verify_passkey_registration(
        self, user_id: str, passkey_id: str, public_key_credential: dict, passkey_name: str = "My passkey"
    ) -> None:
        """Complete passkey registration by submitting the browser's PublicKeyCredential."""
        resp = await self._http.post(
            f"/v2/users/{user_id}/passkeys/{passkey_id}",
            json={"publicKeyCredential": public_key_credential, "passkeyName": passkey_name},
        )
        resp.raise_for_status()

    async def register_email_otp(self, user_id: str) -> None:
        """Register email OTP for a user. Zitadel sends a verification code to the user's email."""
        resp = await self._http.post(f"/v2/users/{user_id}/otp_email")
        resp.raise_for_status()

    async def remove_email_otp(self, user_id: str) -> None:
        """Remove email OTP from a user. Used before re-registration to resend the code."""
        resp = await self._http.delete(f"/v2/users/{user_id}/otp_email")
        resp.raise_for_status()

    async def verify_email_otp(self, user_id: str, code: str) -> None:
        """Verify and activate the email OTP registration using the code from the email."""
        resp = await self._http.post(
            f"/v2/users/{user_id}/otp_email/_verify",
            json={"code": code},
        )
        resp.raise_for_status()

    async def update_session_with_totp(self, session_id: str, session_token: str, code: str) -> dict:
        """Add a TOTP check to an existing session. Returns updated session dict."""
        resp = await self._http.patch(
            f"/v2/sessions/{session_id}",
            json={
                "sessionToken": session_token,
                "checks": {"totp": {"code": code}},
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def register_user_totp(self, user_id: str) -> dict:
        """Start TOTP registration for a user. Returns {uri, totpSecret}."""
        resp = await self._http.post(f"/v2/users/{user_id}/totp")
        resp.raise_for_status()
        return resp.json()

    async def verify_user_totp(self, user_id: str, code: str) -> None:
        """Verify and activate a TOTP registration."""
        resp = await self._http.post(
            f"/v2/users/{user_id}/totp/verify",
            json={"code": code},
        )
        resp.raise_for_status()

    # ── Provisioning ──────────────────────────────────────────────────────────

    async def create_librechat_oidc_app(self, slug: str, redirect_uri: str) -> dict:
        """Create a per-tenant LibreChat OIDC app in the Klai Platform project."""
        names = validate_slug_for_provisioning(slug, domain=settings.domain)
        resp = await self._http.post(
            f"/management/v1/projects/{settings.zitadel_project_id}/apps/oidc",
            json={
                "name": names.zitadel_oidc_app_name,
                "redirectUris": [redirect_uri],
                "responseTypes": ["OIDC_RESPONSE_TYPE_CODE"],
                "grantTypes": [
                    "OIDC_GRANT_TYPE_AUTHORIZATION_CODE",
                    "OIDC_GRANT_TYPE_REFRESH_TOKEN",
                ],
                "appType": "OIDC_APP_TYPE_WEB",
                "authMethodType": "OIDC_AUTH_METHOD_TYPE_POST",
                "postLogoutRedirectUris": list(names.zitadel_post_logout_redirect_uris),
            },
        )
        resp.raise_for_status()
        return resp.json()  # contains appId, clientId, clientSecret

    async def delete_librechat_oidc_app(self, app_id: str) -> None:
        """Delete a per-tenant LibreChat OIDC app from the Klai Platform project."""
        resp = await self._http.delete(
            f"/management/v1/projects/{settings.zitadel_project_id}/apps/{app_id}",
        )
        resp.raise_for_status()

    # ── IDP (social login) ────────────────────────────────────────────────────

    async def create_idp_intent(self, idp_id: str, success_url: str, failure_url: str) -> dict:
        """Start an IDP intent flow. Returns { authUrl } to redirect the user to."""
        resp = await self._http.post(
            "/v2/idp_intents",
            json={"idpId": idp_id, "urls": {"successUrl": success_url, "failureUrl": failure_url}},
        )
        resp.raise_for_status()
        return resp.json()

    async def retrieve_idp_intent(self, idp_intent_id: str, idp_intent_token: str) -> dict:
        """Retrieve a completed IDP intent. Returns dict with userId and idpInformation.

        userId may be absent if the IDP did not link to an existing Zitadel user.
        """
        resp = await self._http.post(
            f"/v2/idp_intents/{idp_intent_id}",
            json={"idpIntentToken": idp_intent_token},
        )
        resp.raise_for_status()
        return resp.json()

    async def link_idp_to_user(self, user_id: str, intent_data: dict) -> None:
        """Link the IdP identity from a completed intent to an existing Zitadel user.

        POST /v2/users/{userId}/links — used when ``create_zitadel_user_from_idp``
        hits 409 "User already exists": the account was created earlier (portal
        invite, password signup) without this IdP link, so social login must
        attach the identity instead of failing. After linking, a session can be
        created from the same intent via ``create_session_for_user_idp``.
        """
        idp_info = intent_data.get("idpInformation", {})
        resp = await self._http.post(
            f"/v2/users/{user_id}/links",
            json={
                "idpLink": {
                    "idpId": idp_info.get("idpId", ""),
                    "userId": idp_info.get("userId", ""),
                    "userName": idp_info.get("userName", ""),
                }
            },
        )
        resp.raise_for_status()

    async def create_zitadel_user_from_idp(self, intent_data: dict, org_id: str) -> str:
        """Create a Zitadel human user from IDP intent data. Returns the new Zitadel userId.

        Used during social signup when no existing Zitadel user is linked to the IDP intent.
        The email is marked verified (trusted from the IDP) and the IDP is linked immediately.

        Zitadel v2 IDP intent structure (POST /v2/idp_intents/{id}):
          idpInformation.idpId          — Zitadel IDP config ID
          idpInformation.userId         — IDP-side user ID (e.g. Google sub)
          idpInformation.userName       — IDP-side username / email
          idpInformation.rawInformation.User — raw OIDC user info dict
        """
        idp_info = intent_data.get("idpInformation", {})
        raw_user = idp_info.get("rawInformation", {}).get("User", {})

        idp_id: str = idp_info.get("idpId", "")
        idp_user_id: str = idp_info.get("userId", "")
        idp_user_name: str = idp_info.get("userName", "")

        # email: raw OIDC profile has it directly; fall back to IDP userName
        email: str = raw_user.get("email", "") or idp_user_name
        given_name: str = raw_user.get("given_name", "")
        family_name: str = raw_user.get("family_name", "")
        if not given_name and raw_user.get("name"):
            parts = raw_user["name"].split(" ", 1)
            given_name = parts[0]
            family_name = parts[1] if len(parts) > 1 else ""
        display_name: str = raw_user.get("name", f"{given_name} {family_name}".strip()) or email.split("@")[0]

        if not email:
            logger.error(
                "create_zitadel_user_from_idp: no email in intent — idp_info_keys=%s raw_user_keys=%s",
                list(idp_info.keys()),
                list(raw_user.keys()),
            )
            raise ValueError("Cannot create Zitadel user: no email in IDP intent data")

        resp = await self._http.post(
            "/v2/users/human",
            headers={"x-zitadel-orgid": org_id},
            json={
                # username is lowercased to keep all auto-provisioned IDP
                # users on the same case-insensitive footing as humans
                # created via ``create_human_user`` and ``invite_user``.
                # See ``create_human_user`` docstring for rationale.
                "username": email.lower(),
                "profile": {
                    "givenName": given_name or email.split("@")[0],
                    "familyName": family_name,
                    "displayName": display_name,
                },
                "email": {
                    "email": email,
                    "isVerified": True,
                },
                "idpLinks": [
                    {
                        "idpId": idp_id,
                        "userId": idp_user_id,
                        "userName": idp_user_name or email,
                    }
                ],
            },
        )
        resp.raise_for_status()
        return resp.json()["userId"]

    async def create_session_for_user_idp(self, user_id: str, idp_intent_id: str, idp_intent_token: str) -> dict:
        """Create a Zitadel session for a known user_id using a completed IDP intent."""
        resp = await self._http.post(
            "/v2/sessions",
            json={
                "checks": {
                    "user": {"userId": user_id},
                    "idpIntent": {"idpIntentId": idp_intent_id, "idpIntentToken": idp_intent_token},
                }
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def get_session(self, session_id: str, session_token: str) -> dict:
        """Fetch full session details including factors.user.id and IDP profile data.

        Used after create_session_for_user_idp to retrieve the Zitadel user ID
        and profile (firstName, lastName, email) from the IDP.
        """
        resp = await self._http.get(
            f"/v2/sessions/{session_id}",
            headers={"Authorization": f"Bearer {session_token}"},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_session_details(self, session_id: str, session_token: str) -> dict:
        """Fetch session details to extract user_id and email after IDP login.

        Returns {"zitadel_user_id": ..., "email": ...}.
        Used in idp_callback to identify the SSO user for auto-provisioning.
        """
        resp = await self._http.get(
            f"/v2/sessions/{session_id}",
            headers={"x-zitadel-session-token": session_token},
        )
        resp.raise_for_status()
        data = resp.json()
        user_info = data.get("session", {}).get("factors", {}).get("user", {})
        return {
            "zitadel_user_id": user_info.get("id", ""),
            "email": user_info.get("loginName", ""),
        }


# Singleton — reused across requests
zitadel = ZitadelClient()

# ---------------------------------------------------------------------------
# Shared role-sync helper (REQ-5)
# ---------------------------------------------------------------------------

# Mirror of _ZITADEL_ROLE_BY_PORTAL_ROLE in platform_manage.py and users.py.
# Only admin maps to a Zitadel grant; all other portal roles have no grant.
_ZITADEL_ROLE_FOR_PORTAL_ADMIN = "org:owner"
_PORTAL_ADMIN_ROLE = "admin"


def _grant_has_project_role(grant: dict, role: str) -> bool:
    return grant.get("projectId") == settings.zitadel_project_id and role in (grant.get("roleKeys") or [])


async def _sync_zitadel_role_grant(
    zitadel_user_id: str,
    old_role: str,
    new_role: str,
) -> None:
    """Reconcile the global Zitadel org:owner grant for one portal identity.

    Called AFTER the DB commit — does NOT rollback on failure.

    The identity can be a member of multiple tenants. The grant must exist if
    any non-offboarded tenant membership is admin, and must be removed only
    when no such admin membership remains.

    Callers must catch exceptions and emit a desync audit event without
    propagating (DB commit must not be undone).

    # @MX:NOTE: [AUTO] Shared between platform_manage.platform_update_role and
    # users.update_user_role. Single source of truth for the admin↔Zitadel
    # grant relationship.
    # @MX:SPEC: SPEC-SEC-CROSS-TENANT-FOLLOWUP-001 REQ-5
    """
    from app.core.config import settings as _settings  # local import to avoid circular
    from app.services.user_memberships import get_user_global_membership_state

    portal_org_id = _settings.zitadel_portal_org_id
    membership_state = await get_user_global_membership_state(zitadel_user_id)
    desired = membership_state.admin_count > 0

    grants = await zitadel.list_user_grants(org_id=portal_org_id, user_id=zitadel_user_id)
    has_grant = any(_grant_has_project_role(grant, _ZITADEL_ROLE_FOR_PORTAL_ADMIN) for grant in grants)

    if desired and not has_grant:
        await zitadel.grant_user_role(
            org_id=portal_org_id,
            user_id=zitadel_user_id,
            role=_ZITADEL_ROLE_FOR_PORTAL_ADMIN,
        )
    elif not desired and has_grant:
        await zitadel.remove_user_role(
            org_id=portal_org_id,
            user_id=zitadel_user_id,
            role=_ZITADEL_ROLE_FOR_PORTAL_ADMIN,
        )
