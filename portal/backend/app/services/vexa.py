"""
Vexa bot-manager API client.
Portal-api calls this to start/stop meeting bots.
"""
import hashlib
import re
from typing import NamedTuple

import httpx

from app.core.config import settings


class MeetingRef(NamedTuple):
    platform: str
    native_meeting_id: str


_PLATFORM_PATTERNS = [
    ("google_meet", re.compile(r"https?://meet\.google\.com/([a-z0-9-]+)", re.IGNORECASE)),
    ("zoom", re.compile(r"https?://(?:[\w-]+\.)?zoom\.us/j/(\d+)", re.IGNORECASE)),
    ("teams", re.compile(r"https?://teams\.microsoft\.com/", re.IGNORECASE)),
]


def parse_meeting_url(url: str) -> MeetingRef | None:
    """Parse a meeting URL into (platform, native_meeting_id).
    Returns None if the URL does not match any supported platform.
    """
    for platform, pattern in _PLATFORM_PATTERNS:
        m = pattern.search(url)
        if m:
            if platform == "teams":
                # Teams URLs are complex; use a hash of the full URL as the ID
                native_id = hashlib.sha256(url.encode()).hexdigest()[:32]
            else:
                native_id = m.group(1)
            return MeetingRef(platform=platform, native_meeting_id=native_id)
    return None


class VexaClient:
    def __init__(self) -> None:
        self._http = httpx.AsyncClient(
            base_url=settings.vexa_bot_manager_url,
            headers={"X-API-Key": settings.vexa_api_key},
            timeout=30.0,
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def start_bot(self, platform: str, native_meeting_id: str) -> dict:
        """Start a bot for the given meeting. Returns the bot response dict."""
        resp = await self._http.post("/bots", json={"platform": platform, "native_meeting_id": native_meeting_id})
        resp.raise_for_status()
        return resp.json()

    async def stop_bot(self, platform: str, native_meeting_id: str) -> None:
        """Stop an active bot."""
        resp = await self._http.delete(f"/bots/{platform}/{native_meeting_id}")
        resp.raise_for_status()

    async def get_bot_status(self, platform: str, native_meeting_id: str) -> dict:
        """Get the current status of a bot."""
        resp = await self._http.get(f"/bots/{platform}/{native_meeting_id}")
        resp.raise_for_status()
        return resp.json()

    async def get_recording(self, platform: str, native_meeting_id: str) -> bytes:
        """Download the raw audio recording from Vexa."""
        resp = await self._http.get(f"/recordings/{platform}/{native_meeting_id}/raw")
        resp.raise_for_status()
        return resp.content


vexa = VexaClient()
