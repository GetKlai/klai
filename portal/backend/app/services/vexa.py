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

    async def get_recording(self, vexa_meeting_id: int) -> tuple[bytes, int]:
        """Download the raw audio recording from Vexa.

        Returns (audio_bytes, recording_id) so the caller can delete the recording
        from Vexa storage after a successful transcription.
        """
        # List recordings for this meeting — picks the most recent completed one.
        resp = await self._http.get("/recordings", params={"meeting_id": vexa_meeting_id})
        resp.raise_for_status()
        recordings = resp.json().get("recordings", [])

        recording = None
        media_file = None
        for rec in recordings:
            files = rec.get("media_files") or []
            if files:
                recording = rec
                media_file = files[0]
                break

        if recording is None or media_file is None:
            raise ValueError(f"No media files found for vexa meeting {vexa_meeting_id}")

        recording_id = recording["id"]
        media_file_id = media_file["id"]

        raw_resp = await self._http.get(
            f"/recordings/{recording_id}/media/{media_file_id}/raw",
            timeout=120.0,
        )
        raw_resp.raise_for_status()
        return raw_resp.content, recording_id

    async def delete_recording(self, recording_id: int) -> None:
        """Delete a recording and its media files from Vexa storage."""
        resp = await self._http.delete(f"/recordings/{recording_id}")
        resp.raise_for_status()


vexa = VexaClient()
