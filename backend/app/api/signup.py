"""
POST /api/signup

Creates:
  1. A Zitadel org  (company name → slug)
  2. A human user in that org
  3. A portal_orgs + portal_users row in PostgreSQL

Returns 201 on success. The user still needs to verify their email before logging in.
"""
import re
import unicodedata

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr, field_validator

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


@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
async def signup(body: SignupRequest) -> SignupResponse:
    # 1. Create Zitadel org
    try:
        org_data = await zitadel.create_org(_slugify(body.company_name))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Kon organisatie niet aanmaken: {exc}",
        ) from exc

    org_id: str = org_data["id"]

    # 2. Create human user inside that org
    try:
        user_data = await zitadel.create_human_user(
            org_id=org_id,
            email=body.email,
            first_name=body.first_name,
            last_name=body.last_name,
            password=body.password,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Kon gebruiker niet aanmaken: {exc}",
        ) from exc

    user_id: str = user_data["userId"]

    # TODO: persist org + user to PostgreSQL (portal_orgs / portal_users tables)

    return SignupResponse(
        org_id=org_id,
        user_id=user_id,
        message="Account aangemaakt. Controleer je e-mail om je account te bevestigen.",
    )
