"""Gmail connector adapter.

Syncs Gmail messages as knowledge documents via the Gmail API v1.
Uses service account authentication with domain-wide delegation
to impersonate a user and fetch their email. Each email becomes
a single document with subject, sender, and body text.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from app.adapters._google_auth import get_google_access_token
from app.adapters.base import BaseAdapter, DocumentRef
from app.core.config import Settings

logger = structlog.get_logger(__name__)

_GMAIL_API = "https://gmail.googleapis.com/gmail/v1"

_GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def _extract_body_text(payload: dict[str, Any]) -> str:
    """Extract plain text body from a Gmail message payload.

    Prefers text/plain parts. Falls back to text/html with tag stripping.
    Handles both simple and multipart messages recursively.
    """
    mime_type = payload.get("mimeType", "")

    # Simple single-part message.
    if mime_type == "text/plain":
        body_data = payload.get("body", {}).get("data", "")
        if body_data:
            return base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")
        return ""

    # Multipart: recurse through parts.
    parts = payload.get("parts", [])
    plain_text = ""
    html_text = ""

    for part in parts:
        part_mime = part.get("mimeType", "")
        if part_mime == "text/plain":
            body_data = part.get("body", {}).get("data", "")
            if body_data:
                plain_text = base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")
        elif part_mime == "text/html":
            body_data = part.get("body", {}).get("data", "")
            if body_data:
                html_text = base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace")
        elif part_mime.startswith("multipart/"):
            # Nested multipart.
            nested = _extract_body_text(part)
            if nested:
                plain_text = nested

    if plain_text:
        return plain_text

    # Fallback: strip HTML tags.
    if html_text:
        import re
        text = re.sub(r"<[^>]+>", " ", html_text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    return ""


def _get_header(headers: list[dict[str, str]], name: str) -> str:
    """Extract a header value by name from Gmail message headers."""
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


class GmailAdapter(BaseAdapter):
    """Gmail connector adapter.

    Authenticates via a service account with domain-wide delegation
    to impersonate the configured user email. Lists messages matching
    an optional Gmail search query and fetches full message content.

    Config fields (from connector.config):
        service_account_json (required): JSON string of the service account key.
        user_email (required): Email address to impersonate.
        query (optional): Gmail search query (e.g. "label:important").
            Empty = all messages.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def aclose(self) -> None:
        """No persistent resources to close."""

    @staticmethod
    def _extract_config(connector: Any) -> dict[str, Any]:
        """Extract and validate Gmail config."""
        config: dict[str, Any] = connector.config
        sa_json = config.get("service_account_json", "")
        user_email = config.get("user_email", "")
        if not sa_json:
            raise ValueError("Gmail config missing 'service_account_json'")
        if not user_email:
            raise ValueError("Gmail config missing 'user_email'")
        return {
            "service_account_json": sa_json,
            "user_email": user_email,
            "query": config.get("query", ""),
        }

    async def _list_message_ids(
        self,
        client: httpx.AsyncClient,
        user_email: str,
        query: str,
    ) -> list[str]:
        """List all message IDs matching the query with pagination."""
        message_ids: list[str] = []
        page_token: str | None = None

        while True:
            params: dict[str, Any] = {"maxResults": 100}
            if query:
                params["q"] = query
            if page_token:
                params["pageToken"] = page_token

            resp = await client.get(
                f"{_GMAIL_API}/users/{user_email}/messages",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()

            for msg in data.get("messages", []):
                message_ids.append(msg["id"])

            page_token = data.get("nextPageToken")
            if not page_token:
                break

        return message_ids

    async def list_documents(
        self,
        connector: Any,
        cursor_context: dict[str, Any] | None = None,
    ) -> list[DocumentRef]:
        """List Gmail messages as documents.

        Each email message becomes a single document. Returns all messages
        matching the configured query.
        """
        cfg = self._extract_config(connector)
        sa_json: str = cfg["service_account_json"]
        user_email: str = cfg["user_email"]
        query: str = cfg["query"]

        access_token = await get_google_access_token(
            sa_json, scopes=_GMAIL_SCOPES, subject=user_email,
        )

        async with httpx.AsyncClient(
            timeout=30.0,
            headers={"Authorization": f"Bearer {access_token}"},
        ) as client:
            message_ids = await self._list_message_ids(client, user_email, query)

            refs: list[DocumentRef] = []
            for msg_id in message_ids:
                # Fetch minimal metadata for listing.
                resp = await client.get(
                    f"{_GMAIL_API}/users/{user_email}/messages/{msg_id}",
                    params={"format": "metadata", "metadataHeaders": ["Subject", "From", "Date"]},
                )
                resp.raise_for_status()
                msg_data = resp.json()

                headers = msg_data.get("payload", {}).get("headers", [])
                subject = _get_header(headers, "Subject") or "(no subject)"
                internal_date = msg_data.get("internalDate", "")

                try:
                    last_edited = datetime.fromtimestamp(
                        int(internal_date) / 1000, tz=UTC,
                    ).isoformat()
                except (ValueError, TypeError, OSError):
                    last_edited = ""

                refs.append(
                    DocumentRef(
                        path=subject,
                        ref=msg_id,
                        size=msg_data.get("sizeEstimate", 0),
                        content_type="email",
                        source_ref=msg_id,
                        last_edited=last_edited,
                    )
                )

        logger.info("Listed Gmail messages", count=len(refs))
        return refs

    async def fetch_document(self, ref: DocumentRef, connector: Any) -> bytes:
        """Fetch full email content from Gmail.

        Extracts subject, from, date, and body text. Prefers text/plain
        body parts over text/html.
        """
        cfg = self._extract_config(connector)
        sa_json: str = cfg["service_account_json"]
        user_email: str = cfg["user_email"]

        access_token = await get_google_access_token(
            sa_json, scopes=_GMAIL_SCOPES, subject=user_email,
        )

        async with httpx.AsyncClient(
            timeout=30.0,
            headers={"Authorization": f"Bearer {access_token}"},
        ) as client:
            resp = await client.get(
                f"{_GMAIL_API}/users/{user_email}/messages/{ref.ref}",
                params={"format": "full"},
            )
            resp.raise_for_status()
            msg_data = resp.json()

        payload = msg_data.get("payload", {})
        headers = payload.get("headers", [])

        subject = _get_header(headers, "Subject") or "(no subject)"
        from_addr = _get_header(headers, "From")
        date_str = _get_header(headers, "Date")
        body = _extract_body_text(payload)

        content = f"Subject: {subject}\nFrom: {from_addr}\nDate: {date_str}\n\n{body}"
        return content.encode("utf-8")

    async def get_cursor_state(self, connector: Any) -> dict[str, Any]:
        """Return cursor with current time for incremental sync."""
        return {"last_synced_at": datetime.now(UTC).isoformat()}
