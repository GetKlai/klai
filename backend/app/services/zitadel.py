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

    async def grant_project_role(self, org_id: str, user_id: str, role: str) -> None:
        """Grant a project role to a user (e.g. 'org:owner')."""
        resp = await self._http.post(
            f"/management/v1/projects/{settings.zitadel_project_id}/grants",
            headers={"x-zitadel-orgid": org_id},
            json={
                "grantedOrgId": org_id,
                "roleKeys": [role],
            },
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


# Singleton — reused across requests
zitadel = ZitadelClient()
