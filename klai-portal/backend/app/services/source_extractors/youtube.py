"""YouTube source extractor (SPEC-KB-SOURCES-001 Module 3).

Extracts the video ID from a YouTube URL, resolves a transcript via
``yt-dlp``, and resolves the video title via the public oembed endpoint.
Failure in the oembed call is best-effort — transcript is the primary
payload and must drive success / failure (SPEC R3.4).

Why yt-dlp, not ``youtube-transcript-api``:
yt-dlp is a factor more robust against YouTube's anti-scraping. It cycles
through multiple player clients (android / web / ios / tv) — when one is
blocked, another often still works. ``youtube-transcript-api`` is a thin
scraper of a single endpoint (the web client), which YouTube blocks
aggressively on datacenter IPs. Observed on core-01 (1.2.0): popular
videos with transcripts returned RequestBlocked via the old library.
yt-dlp on the same IP reaches the transcript without a proxy because it
hits the Android-client endpoint (a different rate-limit bucket).

Error mapping:
- Video unavailable / private / removed → UnsupportedSourceError (route
  → 422 "no transcript"). Truthful: the video cannot be ingested at all.
- No transcript tracks on the video → UnsupportedSourceError (422).
- YouTube refused the request ("sign in to confirm you're not a bot",
  HTTP 429/403, signature-cipher failures) → SourceFetchError (route
  → 502 "could not reach YouTube"). Retryable and NOT the user's fault.

Optional proxy fallback: when ``settings.youtube_proxy_url`` is set,
upstream-blocked errors trigger one retry via the proxy. Direct path
succeeds far more often with yt-dlp than with the old library, so the
proxy is a last resort — and many tenants may never need it.

Video-ID extraction and oembed title resolution are unchanged from 1.2.0
(the regex is a direct port from klai-focus).
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, cast

import httpx
import structlog
import yt_dlp
from yt_dlp.utils import DownloadError

from app.core.config import settings
from app.services.source_extractors.exceptions import (
    InvalidUrlError,
    SourceFetchError,
    UnsupportedSourceError,
)

logger = structlog.get_logger()

# Matches the four URL shapes we care about and captures the 11-char ID.
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

# Subtitle fetch timeout — yt-dlp extracts URLs fast; fetching the text is short.
_SUBTITLE_FETCH_TIMEOUT = 30.0

# Preferred subtitle languages, in priority order.
_TRANSCRIPT_LANGUAGES: tuple[str, ...] = ("en", "nl")

# Substrings in a yt-dlp DownloadError message that mean the video genuinely
# cannot be retrieved (vs YouTube blocking our request). Case-insensitive match.
_UNSUPPORTED_VIDEO_MARKERS: tuple[str, ...] = (
    "video unavailable",
    "private video",
    "video has been removed",
    "this video is private",
    "members-only content",
    "age-restricted",
    "this live event will begin",
    "premieres in",
    "this video is not available",
)

# Substrings in a yt-dlp DownloadError message that mean YouTube is blocking
# our request (IP block, bot detection, rate limit). Retryable.
_UPSTREAM_BLOCKED_MARKERS: tuple[str, ...] = (
    "sign in to confirm",
    "confirm you're not a bot",
    "http error 429",
    "http error 403",
    "too many requests",
    "unable to extract",
    "signature",  # decipher failures often mean YouTube changed the JS surface
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


def _build_ydl_opts(proxy_url: str | None) -> dict[str, Any]:
    """Construct yt-dlp options. Kept small — defaults are fine for our use-case.

    Key choices:
    - ``skip_download``: we only want metadata + subtitle URLs, never the MP4.
    - ``writesubtitles`` + ``writeautomaticsub``: populate both subtitle dicts
      in the extracted info so we can pick the best track.
    - ``subtitleslangs``: hint preferred languages — yt-dlp still returns all
      available tracks, so this is soft guidance, not a filter.
    - ``player_client=['android', 'web']``: try Android client first; it hits
      a different rate-limit bucket and often succeeds where web is blocked.
    - ``quiet`` + ``no_warnings``: yt-dlp chatters on stderr by default.
    """
    opts: dict[str, Any] = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": list(_TRANSCRIPT_LANGUAGES),
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
        # We handle retry ourselves via the proxy fallback in _fetch_transcript.
        "retries": 0,
        "extractor_retries": 0,
    }
    if proxy_url:
        opts["proxy"] = proxy_url
    return opts


def _extract_info_sync(video_url: str, proxy_url: str | None) -> dict[str, Any]:
    """Blocking call into yt-dlp. Caller must wrap in ``asyncio.to_thread``.

    yt-dlp uses private ``_Params`` / ``_InfoDict`` TypedDicts; our option
    dict is the same shape but pyright sees it as incompatible. Cast at
    the boundary rather than leak yt-dlp internals into our type signatures.
    """
    opts = cast(Any, _build_ydl_opts(proxy_url))
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(video_url, download=False)
    return cast(dict[str, Any], info) if info else {}


def _match_preferred_lang(source: dict[str, list]) -> list | None:
    """Return tracks for the first preferred language found in ``source``.

    YouTube uses both ``en`` and ``en-<hash>`` style keys for manual tracks,
    and ``en-orig`` / ``en`` / ``en-<target>`` for auto captions. We accept
    any key that starts with the preferred language code.
    """
    for lang in _TRANSCRIPT_LANGUAGES:
        if source.get(lang):
            return source[lang]
        for key, tracks in source.items():
            if (key.startswith(f"{lang}-") or key == f"{lang}-orig") and tracks:
                return tracks
    return None


def _first_auto_orig(auto: dict[str, list]) -> list | None:
    """Return the first non-empty ``<lang>-orig`` track list (any language).

    Machine-translated derivations (e.g. ``en-ar``) are deliberately skipped
    — their quality is poor and unsuitable for knowledge ingest.
    """
    for key, tracks in auto.items():
        if key.endswith("-orig") and tracks:
            return tracks
    return None


def _first_any(source: dict[str, list]) -> list | None:
    """Return the first non-empty track list from ``source`` in insertion order."""
    for tracks in source.values():
        if tracks:
            return tracks
    return None


def _pick_format(tracks: list) -> dict[str, Any] | None:
    """Pick the cheapest-to-parse track format. Prefer JSON3 > VTT > others."""
    for fmt in ("json3", "vtt", "srv3", "srt", "ttml"):
        for track in tracks:
            if track.get("ext") == fmt:
                return track
    return tracks[0] if tracks else None


def _pick_transcript_track(info: dict[str, Any]) -> dict[str, Any] | None:
    """Choose the best transcript track from yt-dlp's extracted info.

    Priority:
    1. Manual upload in a preferred language (highest quality, author-vetted).
    2. Auto-caption in the ORIGINAL language (``<lang>-orig`` in yt-dlp's
       naming — recognises the video was spoken in that language).
    3. Any manual upload (video spoken in an unpreferred language, but the
       captions were written by a human — still high quality).
    4. Any ``-orig`` auto-caption (auto-generated in the source language).
    """
    subtitles: dict[str, list] = info.get("subtitles") or {}
    auto: dict[str, list] = info.get("automatic_captions") or {}

    tracks = (
        _match_preferred_lang(subtitles)
        or _match_preferred_lang({k: v for k, v in auto.items() if k.endswith("-orig")})
        or _first_any(subtitles)
        or _first_auto_orig(auto)
    )
    if not tracks:
        return None
    return _pick_format(tracks)


def _parse_json3_transcript(payload: dict[str, Any]) -> str:
    """Extract plain text from YouTube's JSON3 timed-text format.

    Structure::

        {
          "events": [
            {"segs": [{"utf8": "Hello"}, {"utf8": " world"}]},
            {"segs": [{"utf8": "second line"}]},
            ...
          ]
        }
    """
    pieces: list[str] = []
    for event in payload.get("events") or []:
        for seg in event.get("segs") or []:
            text = seg.get("utf8")
            if text and text != "\n":
                pieces.append(text)
    # YouTube segments include trailing newlines between events; collapse to spaces.
    return re.sub(r"\s+", " ", " ".join(pieces)).strip()


def _parse_vtt_transcript(text: str) -> str:
    """Extract plain text from a WebVTT subtitle file.

    Simple strategy: drop cue timing lines and empty lines, concat the rest.
    Good enough for knowledge ingest where we discard timing anyway.
    """
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("WEBVTT") or line.startswith("NOTE"):
            continue
        # Cue-timing lines: "00:00:00.000 --> 00:00:05.000"
        if "-->" in line:
            continue
        # Strip simple HTML tags (<c>, </c>, etc.)
        line = re.sub(r"<[^>]+>", "", line)
        if line:
            lines.append(line)
    return re.sub(r"\s+", " ", " ".join(lines)).strip()


async def _fetch_subtitle_text(track: dict[str, Any]) -> str:
    """Fetch the subtitle URL and parse it to plain text.

    yt-dlp gives us a signed, time-limited URL. We fetch + parse here so the
    resulting transcript is never written to disk.
    """
    url = track.get("url")
    if not url:
        raise UnsupportedSourceError("Subtitle track has no URL")

    async with httpx.AsyncClient(timeout=_SUBTITLE_FETCH_TIMEOUT) as client:
        resp = await client.get(url)
    if resp.status_code != 200:
        raise SourceFetchError(f"Subtitle fetch returned HTTP {resp.status_code}")

    ext = track.get("ext", "")
    if ext == "json3":
        try:
            return _parse_json3_transcript(resp.json())
        except ValueError as exc:
            raise SourceFetchError(f"Subtitle JSON parse failed: {exc}") from exc
    # VTT/SRV3/SRT/TTML all render reasonably with the VTT parser — cue-timing
    # lines and tags are stripped; text remains. If quality ever drops here we
    # can add a TTML-specific parser.
    return _parse_vtt_transcript(resp.text)


def _classify_download_error(exc: DownloadError) -> type[Exception]:
    """Map yt-dlp ``DownloadError`` to one of our typed exceptions by message.

    yt-dlp wraps every low-level failure into ``DownloadError`` with a string
    message — there are no structured error codes. We inspect the message and
    bucket into "unsupported" (user-facing truth) vs "upstream blocked"
    (infrastructure, retryable).
    """
    msg = str(exc).lower()
    for marker in _UNSUPPORTED_VIDEO_MARKERS:
        if marker in msg:
            return UnsupportedSourceError
    for marker in _UPSTREAM_BLOCKED_MARKERS:
        if marker in msg:
            return SourceFetchError
    # Unknown failure — default to SourceFetchError so the user sees a retry
    # banner. Better than a misleading "no transcript".
    return SourceFetchError


async def _fetch_transcript_once(video_id: str, video_url: str, proxy_url: str | None) -> str:
    """Single yt-dlp attempt. Raises typed exceptions on failure."""
    try:
        info = await asyncio.to_thread(_extract_info_sync, video_url, proxy_url)
    except DownloadError as exc:
        mapped = _classify_download_error(exc)
        raise mapped(str(exc)) from exc

    track = _pick_transcript_track(info)
    if not track:
        raise UnsupportedSourceError(f"No transcript track for {video_id}")

    text = await _fetch_subtitle_text(track)
    if not text:
        raise UnsupportedSourceError(f"Transcript for {video_id} is empty")
    return text


async def _fetch_transcript(video_id: str, video_url: str) -> str:
    """Return the transcript text, optionally retrying via proxy.

    Direct call first. On ``SourceFetchError`` AND
    ``settings.youtube_proxy_url`` configured, retry once via the proxy.
    ``UnsupportedSourceError`` never triggers a retry — the video really
    has no transcript, a proxy won't change that.
    """
    try:
        return await _fetch_transcript_once(video_id, video_url, proxy_url=None)
    except UnsupportedSourceError:
        logger.info("youtube_transcript_unsupported", video_id=video_id)
        raise
    except SourceFetchError as direct_exc:
        proxy_url = settings.youtube_proxy_url or None
        if not proxy_url:
            logger.warning(
                "youtube_upstream_blocked_no_proxy",
                video_id=video_id,
                error=str(direct_exc)[:200],
            )
            raise

        logger.warning(
            "youtube_upstream_blocked_retry_via_proxy",
            video_id=video_id,
            error=str(direct_exc)[:200],
        )
        try:
            return await _fetch_transcript_once(video_id, video_url, proxy_url=proxy_url)
        except UnsupportedSourceError:
            logger.info("youtube_transcript_unsupported_via_proxy", video_id=video_id)
            raise
        except SourceFetchError as proxy_exc:
            logger.exception(
                "youtube_upstream_blocked_via_proxy",
                video_id=video_id,
                error=str(proxy_exc)[:200],
            )
            raise


async def _fetch_oembed_title(video_url: str, video_id: str) -> str:
    """Best-effort fetch of the video title from YouTube's oembed endpoint.

    Fallback is ``"YouTube video {video_id}"`` on ANY failure — network
    error, non-2xx, non-JSON body, or missing 'title' field. Must not
    raise: the transcript is the primary payload (SPEC R3.4).
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
    dedups against the existing row (SPEC R3.5).

    Raises:
        InvalidUrlError: URL does not parse as a YouTube video.
        UnsupportedSourceError: the video genuinely has no transcript.
        SourceFetchError: YouTube refused the request (IP block, rate
            limit, transient failure) — retry-friendly banner, not the
            "no transcript" banner.
    """
    video_id = _extract_video_id(url)
    transcript = await _fetch_transcript(video_id, url)
    title = await _fetch_oembed_title(url, video_id)
    return title, transcript, f"youtube:{video_id}"
