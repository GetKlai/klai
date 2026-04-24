"""YouTube source extractor (SPEC-KB-SOURCES-001 Module 3).

Extracts the video ID from a YouTube URL, fetches the transcript via
``youtube-transcript-api`` (sync library — wrapped in
``asyncio.to_thread``), and resolves the video title via the public
oembed endpoint. Failure in the oembed call is best-effort — transcript
is the primary payload and must drive success / failure, per SPEC R3.4.

Error mapping (1.3):
- ``NoTranscriptFound`` / ``TranscriptsDisabled`` / ``VideoUnavailable``
  → ``UnsupportedSourceError`` → 422 ("no transcript available"). This is
  a user-facing truth: the video genuinely cannot be ingested.
- ``RequestBlocked`` / ``IpBlocked`` / ``YouTubeRequestFailed`` /
  ``CouldNotRetrieveTranscript`` → ``SourceFetchError`` → 502 ("could not
  reach YouTube"). These are infrastructure signals — typically YouTube
  rate-limiting the datacenter IP. Telling the user "no transcript" here
  is a lie; "try again" / "temporarily unavailable" is the truth.

Optional proxy fallback: when ``settings.youtube_proxy_url`` is set,
infrastructure-level errors trigger one retry via
``GenericProxyConfig(http_url, https_url)``. Ported from
``klai-focus/research-api/app/services/youtube.py`` (SPEC D5 follow-up).

Video-ID extraction is ported from klai-focus (no verbatim re-use — same
regex shape, broader host coverage).
"""

from __future__ import annotations

import asyncio
import re

import httpx
import structlog
from youtube_transcript_api import (
    CouldNotRetrieveTranscript,
    IpBlocked,
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeRequestFailed,
    YouTubeTranscriptApi,
)
from youtube_transcript_api.proxies import GenericProxyConfig

from app.core.config import settings
from app.services.source_extractors.exceptions import (
    InvalidUrlError,
    SourceFetchError,
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

# Language preference order: English then Dutch, with youtube-transcript-api's
# own fallback behaviour handling everything else (find_transcript returns
# whatever language is available after those two).
_TRANSCRIPT_LANGUAGES: tuple[str, ...] = ("en", "nl")

# These exceptions mean the video HAS no accessible transcript — truthful
# "no transcript" banner to the user.
_NO_TRANSCRIPT_EXCEPTIONS = (
    TranscriptsDisabled,
    NoTranscriptFound,
    VideoUnavailable,
)

# These exceptions mean YouTube refused OUR request — infrastructure issue,
# unrelated to whether the video has a transcript. User gets a retry banner.
_UPSTREAM_BLOCKED_EXCEPTIONS = (
    RequestBlocked,
    IpBlocked,
    YouTubeRequestFailed,
    CouldNotRetrieveTranscript,
)


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


def _make_api(proxy_url: str | None) -> YouTubeTranscriptApi:
    """Instantiate ``YouTubeTranscriptApi`` with an optional residential proxy."""
    if proxy_url:
        proxy = GenericProxyConfig(http_url=proxy_url, https_url=proxy_url)
        return YouTubeTranscriptApi(proxy_config=proxy)
    return YouTubeTranscriptApi()


def _fetch_transcript_sync(video_id: str, proxy_url: str | None) -> list[str]:
    """Blocking transcript fetch — runs in a worker thread.

    Returns a list of snippet text strings. ``api.fetch`` internally calls
    ``list(video_id).find_transcript(languages).fetch()``, and
    ``find_transcript`` already falls back from manual to auto-generated
    captions, so we only need one call here.
    """
    api = _make_api(proxy_url)
    fetched = api.fetch(video_id, languages=list(_TRANSCRIPT_LANGUAGES))
    return [getattr(snippet, "text", "") for snippet in fetched]


async def _fetch_transcript(video_id: str) -> str:
    """Return the concatenated transcript text for ``video_id``.

    Joins segments with a single space; timestamps are deliberately
    discarded (R3.3 — not stored in retrieved text).

    Tries a direct connection first. If YouTube blocks the request AND
    ``settings.youtube_proxy_url`` is configured, retries once via the
    proxy. No proxy → the upstream block propagates as SourceFetchError.

    Raises:
        UnsupportedSourceError: the video genuinely has no transcript.
        SourceFetchError: YouTube refused the request (IP block, rate
            limit, transient failure). The user sees a retry banner, not
            a misleading "no transcript" message.
    """
    proxy_url: str | None = None
    try:
        snippets = await asyncio.to_thread(_fetch_transcript_sync, video_id, proxy_url)
    except _NO_TRANSCRIPT_EXCEPTIONS as exc:
        logger.info(
            "youtube_transcript_unavailable",
            video_id=video_id,
            error_class=exc.__class__.__name__,
        )
        raise UnsupportedSourceError(f"No transcript available for {video_id}: {exc.__class__.__name__}") from exc
    except _UPSTREAM_BLOCKED_EXCEPTIONS as exc:
        proxy_url = settings.youtube_proxy_url or None
        if not proxy_url:
            logger.warning(
                "youtube_upstream_blocked_no_proxy",
                video_id=video_id,
                error_class=exc.__class__.__name__,
            )
            raise SourceFetchError(f"YouTube refused the request for {video_id}: {exc.__class__.__name__}") from exc

        logger.warning(
            "youtube_upstream_blocked_retry_via_proxy",
            video_id=video_id,
            error_class=exc.__class__.__name__,
        )
        try:
            snippets = await asyncio.to_thread(_fetch_transcript_sync, video_id, proxy_url)
        except _NO_TRANSCRIPT_EXCEPTIONS as proxy_exc:
            # Proxy got through but the video really has no transcript.
            logger.info(
                "youtube_transcript_unavailable_via_proxy",
                video_id=video_id,
                error_class=proxy_exc.__class__.__name__,
            )
            raise UnsupportedSourceError(
                f"No transcript available for {video_id}: {proxy_exc.__class__.__name__}"
            ) from proxy_exc
        except _UPSTREAM_BLOCKED_EXCEPTIONS as proxy_exc:
            logger.exception(
                "youtube_upstream_blocked_via_proxy",
                video_id=video_id,
                error_class=proxy_exc.__class__.__name__,
            )
            raise SourceFetchError(f"YouTube refused the request even via proxy for {video_id}") from proxy_exc

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
            resp = await client.get(_OEMBED_URL, params={"url": video_url, "format": "json"})
            if resp.status_code != 200:
                logger.info("youtube_oembed_non_200", video_id=video_id, status=resp.status_code)
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
        UnsupportedSourceError: the video genuinely has no transcript.
        SourceFetchError: YouTube refused the request (IP block, rate
            limit, transient failure) — retry-friendly banner, not the
            "no transcript" banner.
    """
    video_id = _extract_video_id(url)
    transcript = await _fetch_transcript(video_id)
    title = await _fetch_oembed_title(url, video_id)
    return title, transcript, f"youtube:{video_id}"
