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
        resp = await self._http.post(
            "/bots",
            json={
                "platform": platform,
                "native_meeting_id": native_meeting_id,
                "recording_enabled": True,
                "bot_name": "Klai",
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def stop_bot(self, platform: str, native_meeting_id: str) -> None:
        """Stop an active bot."""
        resp = await self._http.delete(f"/bots/{platform}/{native_meeting_id}")
        resp.raise_for_status()

    async def get_meeting_by_native_id(self, platform: str, native_meeting_id: str) -> dict | None:
        """Find a Vexa meeting by platform + native_meeting_id.

        Queries GET /meetings and returns the most recent matching entry, or None.
        """
        resp = await self._http.get("/meetings")
        resp.raise_for_status()
        meetings = resp.json().get("meetings", [])
        # Return the most recent matching meeting (highest id)
        matches = [
            m for m in meetings if m.get("platform") == platform and m.get("native_meeting_id") == native_meeting_id
        ]
        return max(matches, key=lambda m: m["id"]) if matches else None

    async def get_bot_status(self, platform: str, native_meeting_id: str) -> dict:
        """Get the current status of a bot via the /meetings endpoint."""
        meeting = await self.get_meeting_by_native_id(platform, native_meeting_id)
        if meeting is None:
            raise httpx.HTTPStatusError(
                "Meeting not found",
                request=httpx.Request("GET", "/meetings"),
                response=httpx.Response(404),
            )
        return meeting

    async def get_recording(self, vexa_meeting_id: int) -> tuple[bytes, str]:
        """Download the raw audio recording from Vexa.

        Looks up recordings for the given vexa internal meeting ID,
        then downloads the first completed audio file.

        Returns (audio_bytes, format) where format is e.g. 'wav' or 'webm'.
        """
        resp = await self._http.get("/recordings", params={"meeting_id": vexa_meeting_id})
        resp.raise_for_status()
        recordings = resp.json().get("recordings", [])
        if not recordings:
            raise ValueError(f"No recordings found for vexa meeting {vexa_meeting_id}")

        # Pick the first completed recording with audio media files
        for rec in recordings:
            if rec.get("status") != "completed":
                continue
            for mf in rec.get("media_files", []):
                if mf.get("type") == "audio":
                    rec_id = rec["id"]
                    mf_id = mf["id"]
                    fmt = mf.get("format", "wav")
                    raw_resp = await self._http.get(
                        f"/recordings/{rec_id}/media/{mf_id}/raw",
                        timeout=120.0,
                    )
                    raw_resp.raise_for_status()
                    return raw_resp.content, fmt

        raise ValueError(f"No completed audio recording found for vexa meeting {vexa_meeting_id}")

    async def get_transcript_segments(self, platform: str, native_meeting_id: str) -> list[dict]:
        """Fetch transcript segments with speaker labels from the Vexa API-gateway (port 8123).

        Returns list of segment dicts: {start, end, text, speaker, language, absolute_start_time}
        Raises httpx.HTTPStatusError on HTTP error, httpx.RequestError on network error.
        """
        async with httpx.AsyncClient(
            base_url=settings.vexa_api_gateway_url,
            headers={"X-API-Key": settings.vexa_api_key},
            timeout=30.0,
        ) as client:
            resp = await client.get(f"/transcripts/{platform}/{native_meeting_id}")
            resp.raise_for_status()
            return resp.json().get("segments", [])


vexa = VexaClient()
