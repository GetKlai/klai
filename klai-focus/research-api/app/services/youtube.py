"""
YouTube transcript extraction using youtube-transcript-api.

Direct connection is attempted first. If YouTube blocks the server IP, the request
is retried via a residential proxy when YOUTUBE_PROXY_URL is configured.
"""
import logging
import re

from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    YouTubeTranscriptApi,
)

from app.core.config import settings

logger = logging.getLogger(__name__)

_YT_PATTERNS = [
    r"(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]{11})",
]


def extract_video_id(url: str) -> str | None:
    for pattern in _YT_PATTERNS:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def _fetch(video_id: str, proxies: dict | None) -> str:
    """Fetch transcript for video_id, optionally via proxy."""
    kwargs: dict = {}
    if proxies:
        kwargs["proxies"] = proxies

    transcript_list = YouTubeTranscriptApi.list_transcripts(video_id, **kwargs)
    try:
        transcript = transcript_list.find_transcript(["nl", "en"])
    except NoTranscriptFound:
        transcript = transcript_list.find_generated_transcript(["nl", "en"])

    entries = transcript.fetch()
    return " ".join(entry["text"] for entry in entries)


def get_transcript(url: str) -> str:
    """
    Fetch YouTube transcript as plain text.

    First attempts a direct connection. If YouTube blocks the server IP (rate limit,
    datacenter block), retries via the configured residential proxy when available.
    Raises ValueError with a user-facing Dutch message if transcript unavailable.
    """
    video_id = extract_video_id(url)
    if not video_id:
        raise ValueError("Geen geldige YouTube-URL herkend")

    try:
        return _fetch(video_id, proxies=None)
    except TranscriptsDisabled:
        raise ValueError("Transcripts zijn uitgeschakeld voor deze video")
    except NoTranscriptFound:
        raise ValueError("Geen transcript beschikbaar voor deze video")
    except Exception as exc:
        if settings.youtube_proxy_url:
            logger.warning(
                "YouTube direct fetch blocked for %s (%s), retrying via proxy",
                video_id,
                type(exc).__name__,
            )
            try:
                return _fetch(video_id, proxies={"https": settings.youtube_proxy_url})
            except TranscriptsDisabled:
                raise ValueError("Transcripts zijn uitgeschakeld voor deze video")
            except NoTranscriptFound:
                raise ValueError("Geen transcript beschikbaar voor deze video")
            except Exception as proxy_exc:
                logger.error("YouTube proxy fetch also failed for %s: %s", video_id, proxy_exc)
                raise ValueError(f"Transcript ophalen mislukt (ook via proxy): {proxy_exc}") from proxy_exc

        logger.warning("YouTube fetch failed for %s: %s", video_id, exc)
        raise ValueError(f"Transcript ophalen mislukt: {exc}") from exc
