"""YouTube source extractor (SPEC-KB-SOURCES-001 Module 3).

Extracts the video ID from a YouTube URL, fetches the transcript via
``youtube-transcript-api`` (sync library — wrapped in
``asyncio.to_thread``), and resolves the video title via the public
oembed endpoint. Failure in the oembed call is best-effort — transcript
is the primary payload and must drive success / failure, per SPEC R3.4.

Video-ID extraction is ported from klai-focus (no verbatim re-use — same
regex shape, broader host coverage).
"""

from __future__ import annotations

import asyncio
import re

import httpx
import structlog
from youtube_transcript_api import (
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeTranscriptApi,
)

from app.services.source_extractors.exceptions import (
    InvalidUrlError,
    UnsupportedSourceError,
)

logger = structlog.get_logger()

# Matches the four URL shapes we care about and captures the 11-char ID.
# Supports http/https, optional www./m., youtube.com (watch/shorts/embed/v),
# and youtu.be. Anything trailing (query, fragment) is ignored by the
# anchored match — only the ID portion is captured.
_YOUTUBE_URL_RE = re.compile(
    r"""
    ^
    (?:https?://)?
    (?:www\.|m\.)?
    (?:
        youtube\.com/
        (?:
            watch\?(?:[^\s&]+&)*v=        # ?v=ID or ?foo=bar&v=ID
          | v/                             # /v/ID
          | embed/                         # /embed/ID
          | shorts/                        # /shorts/ID
        )
      | youtu\.be/                         # youtu.be/ID
    )
    ([A-Za-z0-9_-]{11})                    # the 11-char video ID
    """,
    re.VERBOSE,
)

_OEMBED_URL = "https://www.youtube.com/oembed"
_OEMBED_TIMEOUT = 5.0


def _extract_video_id(url: str) -> str:
    """Parse ``url`` and return the 11-character YouTube video ID.

    Raises InvalidUrlError on non-string input, empty string, or any
    URL shape not matching the four supported hostnames.
    """
    if not isinstance(url, str) or not url.strip():
        raise InvalidUrlError("URL is empty")
    match = _YOUTUBE_URL_RE.match(url.strip())
    if not match:
        raise InvalidUrlError("Not a recognised YouTube URL")
    return match.group(1)


def _fetch_transcript_sync(video_id: str) -> list[str]:
    """Blocking transcript fetch — runs in a worker thread.

    Returns the list of snippet texts; caller joins them. Language
    preference is English then Dutch, then youtube-transcript-api's
    default fallback (any available).
    """
    api = YouTubeTranscriptApi()
    fetched = api.fetch(video_id, languages=["en", "nl"])
    return [getattr(snippet, "text", "") for snippet in fetched]


async def _fetch_transcript(video_id: str) -> str:
    """Return the concatenated transcript text for ``video_id``.

    Joins segments with a single space; timestamps are deliberately
    discarded (R3.3 — not stored in retrieved text).
    """
    try:
        snippets = await asyncio.to_thread(_fetch_transcript_sync, video_id)
    except (
        TranscriptsDisabled,
        NoTranscriptFound,
        VideoUnavailable,
        RequestBlocked,
    ) as exc:
        raise UnsupportedSourceError(
            f"No transcript available for {video_id}: {exc.__class__.__name__}"
        ) from exc

    joined = " ".join(text.strip() for text in snippets if text and text.strip())
    if not joined:
        raise UnsupportedSourceError(f"Transcript for {video_id} is empty")
    return joined


async def _fetch_oembed_title(video_url: str, video_id: str) -> str:
    """Best-effort fetch of the video title from YouTube's oembed endpoint.

    Fallback is ``"YouTube video {video_id}"`` on ANY failure — network
    error, non-2xx, non-JSON body, or missing 'title' field. Must not
    raise: the transcript is the primary payload.
    """
    fallback = f"YouTube video {video_id}"
    try:
        async with httpx.AsyncClient(timeout=_OEMBED_TIMEOUT) as client:
            resp = await client.get(
                _OEMBED_URL, params={"url": video_url, "format": "json"}
            )
            if resp.status_code != 200:
                logger.info(
                    "youtube_oembed_non_200", video_id=video_id, status=resp.status_code
                )
                return fallback
            try:
                data = resp.json()
            except ValueError:
                logger.info("youtube_oembed_non_json", video_id=video_id)
                return fallback
    except httpx.RequestError as exc:
        logger.info("youtube_oembed_request_failed", video_id=video_id, error=str(exc))
        return fallback

    title = data.get("title") if isinstance(data, dict) else None
    if not title or not isinstance(title, str) or not title.strip():
        return fallback
    return title.strip()


async def extract_youtube(url: str) -> tuple[str, str, str]:
    """Return (title, transcript_text, source_ref) for a YouTube URL.

    The ``source_ref`` is ``f"youtube:{video_id}"`` so re-submitting the
    same video through any URL shape (youtu.be, watch, shorts, embed)
    dedups against the existing row.

    Raises:
        InvalidUrlError: URL does not parse as a YouTube video.
        UnsupportedSourceError: no transcript available in any language.
    """
    video_id = _extract_video_id(url)
    transcript = await _fetch_transcript(video_id)
    title = await _fetch_oembed_title(url, video_id)
    return title, transcript, f"youtube:{video_id}"
