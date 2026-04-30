"""
Zitadel management API client.
All calls use the portal-api service account PAT — never exposed to the browser.
"""

import asyncio
import logging
import time

import httpx

from app.core.config import settings
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

    # ── User management ───────────────────────────────────────────────────────

    async def create_human_user(
        self,
        org_id: str,
        email: str,
        first_name: str,
        last_name: str,
        password: str,
        preferred_language: str = "nl",
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
                    "isEmailVerified": False,
                },
                "password": password,
                "passwordChangeRequired": False,
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
        """Create a human user and send initialization email (password-less invite).

        ``userName`` is lowercased before submission — see
        ``create_human_user`` for rationale. The display ``email`` field
        keeps its original case so the invite mail addresses the user
        the way the inviting admin typed it.
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
                    "isEmailVerified": False,
                },
                "sendCodes": True,
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def verify_user_email(self, org_id: str, user_id: str, code: str) -> None:
        """Verify a user's email address using the code from the verification email."""
        resp = await self._http.post(
            f"/management/v1/users/{user_id}/email/_verify",
            headers={"x-zitadel-orgid": org_id},
            json={"verificationCode": code},
        )
        resp.raise_for_status()

    async def remove_user(self, org_id: str, zitadel_user_id: str) -> None:
        """Deactivate a user in the org (does not delete the Zitadel account)."""
        resp = await self._http.delete(
            f"/management/v1/users/{zitadel_user_id}",
            headers={"x-zitadel-orgid": org_id},
        )
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

    async def set_password_with_code(self, user_id: str, code: str, new_password: str) -> None:
        """Set a new password using a verification code from a password-reset email."""
        resp = await self._http.post(
            f"/v2/users/{user_id}/password",
            json={
                "newPassword": {"password": new_password, "changeRequired": False},
                "verificationCode": code,
            },
        )
        resp.raise_for_status()

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

    async def resend_init_mail(self, org_id: str, user_id: str) -> None:
        """Resend the invite email to a user who hasn't completed setup.

        Uses the Zitadel v2 invite_code API. The Management v1 resend_init_mail
        endpoint returns NOT_FOUND once the original init code expires (72h TTL).
        """
        resp = await self._http.post(
            f"/v2/users/{user_id}/invite_code",
            json={"sendCode": {}},
        )
        resp.raise_for_status()

    async def send_password_reset(self, user_id: str) -> None:
        """Trigger Zitadel to send a password reset email to the user."""
        resp = await self._http.post(f"/v2/users/{user_id}/password_reset")
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
            f"/v2/users/{user_id}/totp/_verify",
            json={"code": code},
        )
        resp.raise_for_status()

    # ── Provisioning ──────────────────────────────────────────────────────────

    async def create_librechat_oidc_app(self, slug: str, redirect_uri: str) -> dict:
        """Create a per-tenant LibreChat OIDC app in the Klai Platform project."""
        resp = await self._http.post(
            f"/management/v1/projects/{settings.zitadel_project_id}/apps/oidc",
            json={
                "name": f"librechat-{slug}",
                "redirectUris": [redirect_uri],
                "responseTypes": ["OIDC_RESPONSE_TYPE_CODE"],
                "grantTypes": [
                    "OIDC_GRANT_TYPE_AUTHORIZATION_CODE",
                    "OIDC_GRANT_TYPE_REFRESH_TOKEN",
                ],
                "appType": "OIDC_APP_TYPE_WEB",
                "authMethodType": "OIDC_AUTH_METHOD_TYPE_POST",
                "postLogoutRedirectUris": [
                    f"https://chat-{slug}.{settings.domain}",
                    f"https://chat-{slug}.{settings.domain}/login",
                ],
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

    async def create_session_with_idp_intent(self, idp_intent_id: str, idp_intent_token: str) -> dict:
        """Create a Zitadel session from a completed IDP intent. Returns { sessionId, sessionToken }.

        Retrieves the linked userId from the intent first — required since newer versions of the
        Zitadel sessions API require an explicit user check alongside the IDP intent check.
        Raises ValueError if no userId is linked (e.g. first-time social signup — caller must
        create the Zitadel user first via create_zitadel_user_from_idp).
        """
        intent = await self.retrieve_idp_intent(idp_intent_id, idp_intent_token)
        user_id: str | None = intent.get("userId")
        if not user_id:
            logger.error(
                "IDP intent %s returned no userId — cannot create session",
                idp_intent_id,
            )
            raise ValueError(f"No user linked to IDP intent {idp_intent_id}")
        return await self.create_session_for_user_idp(user_id, idp_intent_id, idp_intent_token)

    async def get_session(self, session_id: str, session_token: str) -> dict:
        """Fetch full session details including factors.user.id and IDP profile data.

        Used after create_session_with_idp_intent to retrieve the Zitadel user ID
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
