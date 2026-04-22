"""
Vexa meeting-api client.
Portal-api calls this to start/stop meeting bots and manage recordings.
"""

import hashlib
import re
from typing import NamedTuple

import httpx
import structlog

from app.core.config import settings
from app.trace import get_trace_headers

logger = structlog.get_logger()


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
            base_url=settings.vexa_meeting_api_url,
            headers={"X-API-Key": settings.vexa_api_key},
            timeout=60.0,
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def start_bot(self, platform: str, native_meeting_id: str) -> dict:
        """Start a bot for the given meeting. Returns the bot response dict."""
        resp = await self._http.post(
            "/bots",
            headers={**get_trace_headers()},
            json={
                "platform": platform,
                "native_meeting_id": native_meeting_id,
                "recording_enabled": False,
                "bot_name": "Klai",
                "automatic_leave": {
                    "max_time_left_alone": 30000,  # 30s after everyone leaves
                    "no_one_joined_timeout": 120000,  # 2 min if no one joins
                    "max_wait_for_admission": 120000,  # 2 min in waiting room
                },
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def stop_bot(self, platform: str, native_meeting_id: str) -> None:
        """Stop an active bot."""
        resp = await self._http.delete(f"/bots/{platform}/{native_meeting_id}", headers={**get_trace_headers()})
        resp.raise_for_status()

    async def get_running_bots(self) -> list[dict]:
        """Return the list of currently running bot containers from Vexa.

        Uses GET /bots/status which returns {"running_bots": [...]}.
        Each entry has: platform, native_meeting_id, status, normalized_status, container_id.
        Returns an empty list if the call fails — treated as "unknown, assume still running".
        """
        resp = await self._http.get("/bots/status", headers={**get_trace_headers()})
        resp.raise_for_status()
        return resp.json().get("running_bots", [])

    async def get_recording(self, vexa_meeting_id: int) -> tuple[bytes, str]:
        """Download the raw audio recording from Vexa.

        Looks up recordings for the given vexa internal meeting ID,
        then downloads the first completed audio file.

        Returns (audio_bytes, format) where format is e.g. 'wav' or 'webm'.
        """
        resp = await self._http.get(
            "/recordings", params={"meeting_id": vexa_meeting_id}, headers={**get_trace_headers()}
        )
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
                        headers={**get_trace_headers()},
                        timeout=120.0,
                    )
                    raw_resp.raise_for_status()
                    return raw_resp.content, fmt

        raise ValueError(f"No completed audio recording found for vexa meeting {vexa_meeting_id}")

    async def get_transcript_segments(self, platform: str, native_meeting_id: str) -> list[dict]:
        """Fetch transcript segments with speaker labels.

        Returns list of segment dicts: {start, end, text, speaker, language, absolute_start_time}
        Raises httpx.HTTPStatusError on HTTP error, httpx.RequestError on network error.
        """
        resp = await self._http.get(f"/transcripts/{platform}/{native_meeting_id}", headers={**get_trace_headers()})
        resp.raise_for_status()
        return resp.json().get("segments", [])

    async def delete_recording(self, recording_id: int) -> bool:
        """Delete a recording by ID. Returns True on success, False on failure.

        404 (already gone) counts as success: nothing to delete and the caller
        can mark the recording as cleaned up so we do not re-queue it forever.
        Other errors return False and are logged with traceback. Never raises.
        """
        try:
            resp = await self._http.delete(f"/recordings/{recording_id}", headers={**get_trace_headers()})
            resp.raise_for_status()
            return True
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                logger.info(
                    "Recording already absent on upstream, marking as deleted",
                    recording_id=recording_id,
                    upstream_status=404,
                )
                return True
            logger.warning(
                "Failed to delete recording",
                recording_id=recording_id,
                upstream_status=exc.response.status_code,
                exc_info=True,
            )
            return False
        except httpx.RequestError:
            logger.warning(
                "Failed to delete recording (network error)",
                recording_id=recording_id,
                exc_info=True,
            )
            return False


vexa = VexaClient()
