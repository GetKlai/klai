"""Shared Google service account authentication helper.

Generates OAuth2 access tokens from a service account JSON key using
a self-signed JWT, exchanged at Google's token endpoint.

Used by: GoogleDriveAdapter, GmailAdapter, GoogleSheetsAdapter.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import jwt
import structlog

logger = structlog.get_logger(__name__)

# Google OAuth2 token endpoint.
_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Token lifetime: 1 hour (Google maximum).
_TOKEN_LIFETIME = 3600


async def get_google_access_token(
    service_account_json: str,
    scopes: list[str] | None = None,
    subject: str | None = None,
) -> str:
    """Generate an OAuth2 access token from a service account JSON key.

    Creates a self-signed JWT using the service account's private key,
    then exchanges it at Google's token endpoint for an access token.

    Args:
        service_account_json: JSON string of the service account key file.
        scopes: OAuth2 scopes to request. Defaults to Drive read-only.
        subject: Email address to impersonate (domain-wide delegation).
            Required for Gmail API and other user-scoped APIs.

    Returns:
        Bearer access token string.

    Raises:
        ValueError: If the service account JSON is invalid or missing fields.
        httpx.HTTPStatusError: If the token exchange fails.
    """
    try:
        sa_info: dict[str, Any] = json.loads(service_account_json)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("Invalid service account JSON") from exc

    private_key = sa_info.get("private_key")
    client_email = sa_info.get("client_email")
    token_uri = sa_info.get("token_uri", _TOKEN_URL)

    if not private_key or not client_email:
        raise ValueError(
            "Service account JSON missing required fields: "
            "'private_key' and 'client_email'"
        )

    if scopes is None:
        scopes = ["https://www.googleapis.com/auth/drive.readonly"]

    now = int(time.time())
    payload: dict[str, Any] = {
        "iss": client_email,
        "scope": " ".join(scopes),
        "aud": token_uri,
        "iat": now,
        "exp": now + _TOKEN_LIFETIME,
    }
    if subject:
        payload["sub"] = subject

    signed_jwt = jwt.encode(payload, private_key, algorithm="RS256")

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            token_uri,
            data={
                "grant_type": "urn:ietf:params:oauth:v2.0:jwt-bearer",
                "assertion": signed_jwt,
            },
        )
        resp.raise_for_status()
        token_data = resp.json()

    access_token = token_data.get("access_token")
    if not access_token:
        raise ValueError("Google token response missing 'access_token'")

    return access_token
