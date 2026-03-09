"""
Zitadel management API client.
All calls use the portal-api service account PAT — never exposed to the browser.
"""
import httpx
from app.core.config import settings


class ZitadelClient:
    def __init__(self) -> None:
        self._http = httpx.AsyncClient(
            base_url=settings.zitadel_base_url,
            headers={
                "Authorization": f"Bearer {settings.zitadel_pat}",
                "Content-Type": "application/json",
            },
            timeout=15.0,
        )

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

    async def invite_user(self, org_id: str, email: str, first_name: str, last_name: str) -> dict:
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

    async def remove_user(self, org_id: str, zitadel_user_id: str) -> None:
        """Deactivate a user in the org (does not delete the Zitadel account)."""
        resp = await self._http.delete(
            f"/management/v1/users/{zitadel_user_id}",
            headers={"x-zitadel-orgid": org_id},
        )
        resp.raise_for_status()

    # ── Token introspection ───────────────────────────────────────────────────

    async def get_userinfo(self, access_token: str) -> dict:
        """Get user info from an OIDC access token (for /api/me)."""
        resp = await self._http.get(
            "/oidc/v1/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        resp.raise_for_status()
        return resp.json()

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

    async def finalize_auth_request(
        self, auth_request_id: str, session_id: str, session_token: str
    ) -> str:
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

    # ── Provisioning ──────────────────────────────────────────────────────────

    async def create_librechat_oidc_app(
        self, slug: str, redirect_uri: str
    ) -> dict:
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
                "postLogoutRedirectUris": [f"https://chat.{slug}.{settings.domain}"],
            },
        )
        resp.raise_for_status()
        return resp.json()  # contains appId, clientId, clientSecret

    async def add_portal_redirect_uri(self, slug: str) -> None:
        """Add {slug}.getklai.com/callback to the portal OIDC app's allowed redirect URIs."""
        if not settings.zitadel_portal_app_id:
            return  # not configured yet, skip
        # GET current config
        get_resp = await self._http.get(
            f"/management/v1/projects/{settings.zitadel_project_id}/apps/{settings.zitadel_portal_app_id}/oidc"
        )
        get_resp.raise_for_status()
        current = get_resp.json().get("oidcConfig", {})
        existing_uris: list[str] = current.get("redirectUris", [])
        new_uri = f"https://{slug}.{settings.domain}/callback"
        if new_uri in existing_uris:
            return
        updated_uris = existing_uris + [new_uri]
        # PUT updated config (preserve all other fields)
        put_resp = await self._http.put(
            f"/management/v1/projects/{settings.zitadel_project_id}/apps/{settings.zitadel_portal_app_id}/oidc",
            json={**current, "redirectUris": updated_uris},
        )
        put_resp.raise_for_status()


# Singleton — reused across requests
zitadel = ZitadelClient()
