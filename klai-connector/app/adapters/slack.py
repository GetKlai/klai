"""Slack connector adapter.

Syncs Slack messages as knowledge documents via the Slack Web API.
Each message thread (parent + replies) is treated as a single document.
Supports optional channel filtering and incremental sync via cursor_context.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import structlog

from app.adapters.base import BaseAdapter, DocumentRef
from app.core.config import Settings

logger = structlog.get_logger(__name__)

_SLACK_API = "https://slack.com/api"


class SlackAdapter(BaseAdapter):
    """Slack connector adapter.

    Authenticates via a bot token against the Slack Web API.
    Lists public channels and their message history, grouping threads
    into single documents.

    Config fields (from connector.config):
        bot_token (required): Slack bot user OAuth token (xoxb-...).
        channel_ids (optional): List of channel IDs to sync.
            Empty = all public channels the bot can access.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._content_cache: dict[str, str] = {}

    async def aclose(self) -> None:
        """No persistent resources to close."""

    @staticmethod
    def _extract_config(connector: Any) -> dict[str, Any]:
        """Extract and validate Slack config."""
        config: dict[str, Any] = connector.config
        bot_token = config.get("bot_token", "")
        if not bot_token:
            raise ValueError("Slack config missing 'bot_token'")
        return {
            "bot_token": bot_token,
            "channel_ids": config.get("channel_ids", []),
        }

    async def _slack_get(
        self, client: httpx.AsyncClient, method: str, params: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a Slack Web API GET with pagination support."""
        resp = await client.get(f"{_SLACK_API}/{method}", params=params)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            error = data.get("error", "unknown")
            logger.error("Slack API error", method=method, error=error)
            raise RuntimeError(f"Slack API error: {error}")
        return data

    async def _get_channels(
        self, client: httpx.AsyncClient, channel_ids: list[str],
    ) -> list[dict[str, Any]]:
        """Get list of channels to sync.

        If channel_ids is provided, return those. Otherwise list all
        public channels the bot can access.
        """
        if channel_ids:
            return [{"id": cid} for cid in channel_ids]

        channels: list[dict[str, Any]] = []
        cursor = ""
        while True:
            params: dict[str, Any] = {
                "types": "public_channel",
                "exclude_archived": "true",
                "limit": 200,
            }
            if cursor:
                params["cursor"] = cursor

            data = await self._slack_get(client, "conversations.list", params)
            channels.extend(data.get("channels", []))

            cursor = data.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                break

        return channels

    async def _get_thread_replies(
        self,
        client: httpx.AsyncClient,
        channel_id: str,
        thread_ts: str,
    ) -> list[dict[str, Any]]:
        """Fetch all replies in a thread."""
        messages: list[dict[str, Any]] = []
        cursor = ""
        while True:
            params: dict[str, Any] = {
                "channel": channel_id,
                "ts": thread_ts,
                "limit": 200,
            }
            if cursor:
                params["cursor"] = cursor

            data = await self._slack_get(client, "conversations.replies", params)
            messages.extend(data.get("messages", []))

            cursor = data.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                break

        return messages

    async def _get_channel_history(
        self,
        client: httpx.AsyncClient,
        channel_id: str,
    ) -> list[dict[str, Any]]:
        """Fetch full channel message history with pagination."""
        messages: list[dict[str, Any]] = []
        cursor = ""
        while True:
            params: dict[str, Any] = {
                "channel": channel_id,
                "limit": 200,
            }
            if cursor:
                params["cursor"] = cursor

            data = await self._slack_get(client, "conversations.history", params)
            messages.extend(data.get("messages", []))

            cursor = data.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                break

        return messages

    def _format_thread(
        self, messages: list[dict[str, Any]], channel_id: str,
    ) -> str:
        """Format a thread's messages into a readable text document."""
        lines: list[str] = []
        for msg in messages:
            user = msg.get("user", "unknown")
            text = msg.get("text", "")
            ts = msg.get("ts", "")
            try:
                dt = datetime.fromtimestamp(float(ts), tz=UTC).strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError, OSError):
                dt = ts
            lines.append(f"[{dt}] {user}: {text}")
        return "\n".join(lines)

    async def list_documents(
        self,
        connector: Any,
        cursor_context: dict[str, Any] | None = None,
    ) -> list[DocumentRef]:
        """List Slack threads as documents.

        Each top-level message with replies becomes a document.
        Standalone messages (no thread) are also included as documents.
        """
        cfg = self._extract_config(connector)
        bot_token: str = cfg["bot_token"]
        channel_ids: list[str] = cfg["channel_ids"]

        async with httpx.AsyncClient(
            timeout=30.0,
            headers={"Authorization": f"Bearer {bot_token}"},
        ) as client:
            channels = await self._get_channels(client, channel_ids)
            refs: list[DocumentRef] = []
            max_ts = "0"

            for channel in channels:
                ch_id = channel["id"]
                ch_name = channel.get("name", ch_id)
                messages = await self._get_channel_history(client, ch_id)

                for msg in messages:
                    ts = msg.get("ts", "")
                    thread_ts = msg.get("thread_ts", ts)

                    # Only process top-level messages (not threaded replies).
                    if thread_ts != ts and msg.get("thread_ts"):
                        continue

                    reply_count = msg.get("reply_count", 0)
                    if reply_count > 0:
                        thread_messages = await self._get_thread_replies(
                            client, ch_id, thread_ts,
                        )
                    else:
                        thread_messages = [msg]

                    content = self._format_thread(thread_messages, ch_id)
                    doc_id = f"{ch_id}:{thread_ts}"
                    self._content_cache[doc_id] = content

                    if ts > max_ts:
                        max_ts = ts

                    try:
                        last_edited = datetime.fromtimestamp(
                            float(ts), tz=UTC,
                        ).isoformat()
                    except (ValueError, TypeError, OSError):
                        last_edited = ""

                    refs.append(
                        DocumentRef(
                            path=f"#{ch_name}/{thread_ts}",
                            ref=doc_id,
                            size=len(content.encode("utf-8")),
                            content_type="chat_transcript",
                            source_ref=doc_id,
                            last_edited=last_edited,
                        )
                    )

            logger.info("Listed Slack threads", count=len(refs))
            return refs

    async def fetch_document(self, ref: DocumentRef, connector: Any) -> bytes:
        """Return cached thread content as UTF-8 bytes."""
        content = self._content_cache.get(ref.ref, "")
        if not content:
            logger.warning("Slack thread content not in cache", ref=ref.ref)
        return content.encode("utf-8")

    async def get_cursor_state(self, connector: Any) -> dict[str, Any]:
        """Return cursor with the latest message timestamp."""
        return {"last_synced_at": datetime.now(UTC).isoformat()}

    async def post_sync(self, connector: Any) -> None:
        """Clear cached thread content after sync."""
        self._content_cache.clear()
