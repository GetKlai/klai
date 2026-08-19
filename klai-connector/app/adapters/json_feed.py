"""Generic adapter for importing a public JSON endpoint as one document."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx
from klai_image_storage import PinnedResolverTransport

from app.adapters.base import BaseAdapter, DocumentRef
from app.clients.knowledge_ingest import MAX_INGEST_CONTENT_CHARS
from app.services.url_guard import validate_json_feed_url_strict

_MAX_FEED_SIZE = 2 * 1024 * 1024
_REQUEST_TIMEOUT = 30.0
_TOTAL_FETCH_TIMEOUT = 60.0
_MIN_KNOWLEDGE_CHARS = 50


def _public_source_url(url: str) -> str:
    """Return a citation-safe origin without path or credential material."""
    parts = urlsplit(url)
    public_netloc = parts.netloc.rsplit("@", 1)[-1]
    return urlunsplit((parts.scheme, public_netloc, "", "", ""))


class JsonFeedAdapter(BaseAdapter):
    """Fetch one HTTPS JSON URL and expose it as one knowledge document."""

    @staticmethod
    def _extract_url(connector: Any) -> str:
        config: dict[str, Any] = connector.config or {}
        url = config.get("url")
        if not isinstance(url, str) or not url.strip():
            raise ValueError(
                "JSON feed connector config missing required field 'url'. "
                "Provide an HTTPS JSON endpoint in connector.config.url."
            )
        return url.strip()

    async def list_documents(
        self,
        connector: Any,
        cursor_context: dict[str, Any] | None = None,
    ) -> list[DocumentRef]:
        """Validate the configured URL and return the feed as one stable ref."""
        url = self._extract_url(connector)
        connector_id = str(connector.id)
        await validate_json_feed_url_strict(url, connector_id=connector_id)
        return [
            DocumentRef(
                path=f"json-feed/{connector_id}.json",
                ref=connector_id,
                size=0,
                content_type="kb_article",
                source_ref=f"json-feed:{connector_id}",
                source_url=_public_source_url(url),
            )
        ]

    async def fetch_document(self, ref: DocumentRef, connector: Any) -> bytes:
        """Download the feed through the shared SSRF guard and pinned transport."""
        url = self._extract_url(connector)
        connector_id = str(connector.id)
        validated = await validate_json_feed_url_strict(url, connector_id=connector_id)
        transport = PinnedResolverTransport({validated.hostname: validated.preferred_ip})

        try:
            async with asyncio.timeout(_TOTAL_FETCH_TIMEOUT):
                async with (
                    httpx.AsyncClient(
                        transport=transport,
                        timeout=_REQUEST_TIMEOUT,
                        follow_redirects=False,
                    ) as client,
                    client.stream("GET", url, headers={"Accept": "application/json"}) as response,
                ):
                    if not 200 <= response.status_code < 300:
                        raise ValueError(f"JSON feed returned HTTP {response.status_code}")

                    content_length = response.headers.get("content-length")
                    if content_length:
                        try:
                            declared_size = int(content_length)
                        except ValueError:
                            declared_size = 0
                        if declared_size > _MAX_FEED_SIZE:
                            raise ValueError(
                                f"JSON feed is too large: {declared_size} bytes (max {_MAX_FEED_SIZE} bytes)"
                            )

                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > _MAX_FEED_SIZE:
                            raise ValueError(f"JSON feed is too large: more than {_MAX_FEED_SIZE} bytes")

                    try:
                        parsed = json.loads(content)
                    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                        raise ValueError("JSON feed returned invalid JSON") from exc

                    normalized = json.dumps(parsed, ensure_ascii=False, indent=2)
                    normalized_length = len(normalized)
                    if normalized_length < _MIN_KNOWLEDGE_CHARS:
                        raise ValueError(
                            f"JSON feed contains too little knowledge: {normalized_length} characters "
                            f"(minimum {_MIN_KNOWLEDGE_CHARS})"
                        )
                    if normalized_length > MAX_INGEST_CONTENT_CHARS:
                        raise ValueError(
                            f"JSON feed produces {normalized_length} characters of text; "
                            f"the ingest limit is {MAX_INGEST_CONTENT_CHARS} characters"
                        )
                    return normalized.encode("utf-8")
        except TimeoutError as exc:
            raise RuntimeError(
                f"JSON feed request exceeded the {_TOTAL_FETCH_TIMEOUT:g}-second total deadline"
            ) from exc
        except ValueError:
            raise
        except httpx.HTTPError as exc:
            # httpx exception strings include the full request URL. Do not
            # leak query parameters into sync_run.error_details or logs.
            raise RuntimeError(f"JSON feed request failed ({type(exc).__name__})") from exc

    async def get_cursor_state(self, connector: Any) -> dict[str, Any]:
        """Omit last_synced_at so reconciliation fetches every manual sync."""
        return {}
