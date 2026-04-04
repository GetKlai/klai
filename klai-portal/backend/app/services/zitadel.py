"""
Zitadel management API client.
All calls use the portal-api service account PAT — never exposed to the browser.
"""

import logging
import time

import httpx

from app.core.config import settings

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
                response.text[:200],
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
        """Create a human user inside a specific org."""
        resp = await self._http.post(
            "/management/v1/users/human/_import",
            headers={"x-zitadel-orgid": org_id},
            json={
                "userName": email,
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
        """Create a human user and send initialization email (password-less invite)."""
        resp = await self._http.post(
            "/management/v1/users/human/_import",
            headers={"x-zitadel-orgid": org_id},
            json={
                "userName": email,
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

    async def get_userinfo(self, access_token: str) -> dict:
        """Get user info from an OIDC access token (for /api/me).

        Results are cached for _USERINFO_TTL seconds per token to reduce
        Zitadel API load on multi-endpoint requests within a session.

        In auth dev mode, returns mock userinfo without calling Zitadel.
        """
        if settings.is_auth_dev_mode:
            return {"sub": settings.auth_dev_user_id}

        now = time.monotonic()
        cached = self._userinfo_cache.get(access_token)
        if cached and (now - cached[0]) < self._USERINFO_TTL:
            return cached[1]

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
        self._userinfo_cache[access_token] = (now, data)
        return data

    # ── Custom Login UI (Session API) ─────────────────────────────────────────

    async def create_session_with_password(self, email: str, password: str) -> dict:
        """Create a Zitadel session validated by email + password.

        Returns the full response dict containing ``sessionId`` and ``sessionToken``.
        Raises ``httpx.HTTPStatusError`` on invalid credentials (4xx).
        """
        resp = await self._http.post(
            "/v2/sessions",
            json={
                "checks": {
                    "user": {"loginName": email},
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
        """Return the Zitadel userId for the given email, or None if not found."""
        resp = await self._http.post(
            "/v2/users",
            json={"queries": [{"loginNameQuery": {"loginName": email, "method": "TEXT_QUERY_METHOD_EQUALS"}}]},
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
        """Return (userId, orgId) for the given email, or None if not found."""
        resp = await self._http.post(
            "/v2/users",
            json={"queries": [{"loginNameQuery": {"loginName": email, "method": "TEXT_QUERY_METHOD_EQUALS"}}]},
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

    async def add_portal_redirect_uri(self, slug: str) -> None:
        """Add {slug}.getklai.com/callback and /logged-out to the portal OIDC app's allowed URIs."""
        if not settings.zitadel_portal_app_id:
            return  # not configured yet, skip
        # GET current config (full app endpoint includes oidcConfig)
        get_resp = await self._http.get(
            f"/management/v1/projects/{settings.zitadel_project_id}/apps/{settings.zitadel_portal_app_id}"
        )
        get_resp.raise_for_status()
        current = get_resp.json().get("app", {}).get("oidcConfig", {})
        existing_redirect: list[str] = current.get("redirectUris", [])
        existing_logout: list[str] = current.get("postLogoutRedirectUris", [])
        new_callback = f"https://{slug}.{settings.domain}/callback"
        new_logged_out = f"https://{slug}.{settings.domain}/logged-out"
        if new_callback in existing_redirect and new_logged_out in existing_logout:
            return
        updated_redirect = existing_redirect + ([new_callback] if new_callback not in existing_redirect else [])
        updated_logout = existing_logout + ([new_logged_out] if new_logged_out not in existing_logout else [])
        # PUT updated config — correct path is oidc_config, not oidc
        put_resp = await self._http.put(
            f"/management/v1/projects/{settings.zitadel_project_id}/apps/{settings.zitadel_portal_app_id}/oidc_config",
            json={**current, "redirectUris": updated_redirect, "postLogoutRedirectUris": updated_logout},
        )
        put_resp.raise_for_status()


# Singleton — reused across requests
zitadel = ZitadelClient()
