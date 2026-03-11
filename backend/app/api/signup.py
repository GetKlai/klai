"""
POST /api/signup

Creates:
  1. A Zitadel org  (company name → slug)
  2. A human user in that org
  3. Assigns org:owner role to the user (so /api/me returns isAdmin=true)
  4. A portal_orgs + portal_users row in PostgreSQL

Returns 201 on success. The user still needs to verify their email before logging in.
"""
import re
import unicodedata

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.models.portal import PortalOrg, PortalUser
from app.services.provisioning import provision_tenant
from app.services.zitadel import zitadel

router = APIRouter(prefix="/api", tags=["auth"])


class SignupRequest(BaseModel):
    first_name: str
    last_name: str
    email: EmailStr
    password: str
    company_name: str

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Wachtwoord moet minimaal 8 tekens bevatten")
        return v

    @field_validator("company_name", "first_name", "last_name")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Veld mag niet leeg zijn")
        return v.strip()


class SignupResponse(BaseModel):
    org_id: str
    user_id: str
    message: str


def _slugify(name: str) -> str:
    """Convert company name to a Zitadel-safe org name."""
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode()
    name = re.sub(r"[^a-zA-Z0-9\s-]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:60] if name else "org"


def _to_slug(name: str, suffix: str = "") -> str:
    """Convert company name to a unique URL slug (lowercase, dashes)."""
    base = _slugify(name).lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")
    if not base:
        base = "org"
    if suffix:
        base = f"{base}-{suffix[:8]}"
    return base[:64]


@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
async def signup(body: SignupRequest, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)) -> SignupResponse:
    # 1. Create Zitadel org
    try:
        org_data = await zitadel.create_org(_slugify(body.company_name))
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 409:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Deze bedrijfsnaam is al in gebruik. Probeer een andere naam.",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Aanmaken mislukt, probeer het later opnieuw",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Aanmaken mislukt, probeer het later opnieuw",
        ) from exc

    zitadel_org_id: str = org_data["id"]

    # 2. Create human user in the portal org (all users live here for OIDC compatibility)
    try:
        user_data = await zitadel.create_human_user(
            org_id=settings.zitadel_portal_org_id,
            email=body.email,
            first_name=body.first_name,
            last_name=body.last_name,
            password=body.password,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 409:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Dit e-mailadres is al geregistreerd. Probeer in te loggen.",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Aanmaken mislukt, probeer het later opnieuw",
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Aanmaken mislukt, probeer het later opnieuw",
        ) from exc

    zitadel_user_id: str = user_data["userId"]

    # 3. Assign org:owner role in the portal org's project
    try:
        await zitadel.grant_user_role(
            org_id=settings.zitadel_portal_org_id,
            user_id=zitadel_user_id,
            role="org:owner",
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Aanmaken mislukt, probeer het later opnieuw",
        ) from exc

    # 4. Persist to PostgreSQL
    try:
        org_row = PortalOrg(
            zitadel_org_id=zitadel_org_id,
            name=body.company_name,
            slug=_to_slug(body.company_name, zitadel_org_id),
        )
        db.add(org_row)
        await db.flush()  # get org_row.id without committing yet

        user_row = PortalUser(
            zitadel_user_id=zitadel_user_id,
            org_id=org_row.id,
            role="admin",  # org creator is always admin
        )
        db.add(user_row)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Aanmaken mislukt, probeer het later opnieuw",
        ) from exc

    background_tasks.add_task(provision_tenant, org_row.id)

    return SignupResponse(
        org_id=zitadel_org_id,
        user_id=zitadel_user_id,
        message="Account aangemaakt. Controleer je e-mail om je account te bevestigen.",
    )
