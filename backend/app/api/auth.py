"""
POST /api/auth/login

Custom Login UI endpoint — called by my.getklai.com/login after the user submits
email + password. Authenticates via Zitadel's Session API and finalizes the OIDC
auth request, returning the callbackUrl the browser should navigate to.

The authRequestId is issued by Zitadel when it redirects to the custom login UI:
  https://my.getklai.com/login?authRequestId=<id>

The service account (zitadel_pat) must have the ``IAM_LOGIN_CLIENT`` role in Zitadel
for the finalize step to succeed.
"""
import logging

import httpx
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr

from app.services.zitadel import zitadel

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    auth_request_id: str


class LoginResponse(BaseModel):
    callback_url: str


class PasswordSetRequest(BaseModel):
    user_id: str
    code: str
    new_password: str


@router.post("/auth/password/set", status_code=status.HTTP_204_NO_CONTENT)
async def password_set(body: PasswordSetRequest) -> None:
    """Complete a password reset using the code from the reset email."""
    try:
        await zitadel.set_password_with_code(body.user_id, body.code, body.new_password)
    except httpx.HTTPStatusError as exc:
        log.error("set_password_with_code failed %s: %s", exc.response.status_code, exc.response.text)
        if exc.response.status_code in (400, 404, 410):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Link is verlopen of ongeldig, vraag een nieuwe reset-link aan",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Wachtwoord instellen mislukt, probeer het later opnieuw",
        ) from exc


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest) -> LoginResponse:
    # 1. Create a Zitadel session by checking email + password
    try:
        session = await zitadel.create_session_with_password(body.email, body.password)
    except httpx.HTTPStatusError as exc:
        log.error("create_session failed %s: %s", exc.response.status_code, exc.response.text)
        if exc.response.status_code in (400, 401, 404, 412):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="E-mailadres of wachtwoord is onjuist",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Inloggen mislukt, probeer het later opnieuw",
        ) from exc

    # 2. Finalize the OIDC auth request with the authenticated session
    try:
        callback_url = await zitadel.finalize_auth_request(
            auth_request_id=body.auth_request_id,
            session_id=session["sessionId"],
            session_token=session["sessionToken"],
        )
    except httpx.HTTPStatusError as exc:
        log.error("finalize_auth_request failed %s: %s", exc.response.status_code, exc.response.text)
        if exc.response.status_code == 404:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Inlogverzoek is verlopen, probeer opnieuw",
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Inloggen mislukt, probeer het later opnieuw",
        ) from exc

    return LoginResponse(callback_url=callback_url)
