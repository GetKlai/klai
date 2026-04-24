"""Tests for the YouTube source extractor (SPEC-KB-SOURCES-001 Module 3).

Covers video-ID extraction, yt-dlp-based transcript extraction (mocked),
error classification for yt-dlp DownloadError messages, proxy fallback
flow, JSON3 / VTT subtitle parsing, and oembed title resolution.

Error-mapping invariants:
- "Video unavailable" / "Private video" / "removed" / age-restricted
  → UnsupportedSourceError (route → 422 "no transcript").
- "Sign in to confirm" / "429" / "403" / signature failure
  → SourceFetchError (route → 502 "could not reach YouTube").
  With ``settings.youtube_proxy_url`` configured, a retry is attempted
  via the proxy before SourceFetchError propagates.
- No transcript track found after successful extract → UnsupportedSourceError.
- Subtitle URL fetch non-200 → SourceFetchError.
"""

from __future__ import annotations

from typing import Any, ClassVar

import httpx
import pytest
from yt_dlp.utils import DownloadError

from app.services.source_extractors.exceptions import (
    InvalidUrlError,
    SourceFetchError,
    UnsupportedSourceError,
)
from app.services.source_extractors.youtube import (
    _extract_video_id,
    _parse_json3_transcript,
    _parse_vtt_transcript,
    _pick_transcript_track,
    extract_youtube,
)

# --- Helpers ---------------------------------------------------------------


def _track(ext: str, url: str = "https://subtitles.example/track") -> dict[str, Any]:
    return {"ext": ext, "url": url}


def _make_info(
    *,
    subtitles: dict[str, list] | None = None,
    automatic_captions: dict[str, list] | None = None,
) -> dict[str, Any]:
    return {
        "id": "dQw4w9WgXcQ",
        "title": "Some Video",
        "subtitles": subtitles or {},
        "automatic_captions": automatic_captions or {},
    }


class _FakeYoutubeDL:
    """Context-manager stand-in for ``yt_dlp.YoutubeDL``.

    ``results`` is the queue of return values for successive instantiations
    (mirroring the production retry flow: direct call, then proxy retry).
    Each entry is either a dict (returned from ``extract_info``) or an
    exception to raise from ``extract_info``.
    """

    # Set by the factory; global across instances for test introspection.
    calls: ClassVar[list[dict[str, Any]]] = []
    results: ClassVar[list[dict[str, Any] | Exception]] = []

    def __init__(self, opts: dict[str, Any]) -> None:
        self.opts = opts
        _FakeYoutubeDL.calls.append({"proxy": opts.get("proxy")})

    def __enter__(self) -> _FakeYoutubeDL:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def extract_info(self, url: str, download: bool = False) -> dict[str, Any]:
        idx = min(len(_FakeYoutubeDL.calls) - 1, len(_FakeYoutubeDL.results) - 1)
        result = _FakeYoutubeDL.results[idx]
        if isinstance(result, Exception):
            raise result
        return result


def _install_ydl(monkeypatch: pytest.MonkeyPatch, results: list[dict[str, Any] | Exception]) -> type[_FakeYoutubeDL]:
    _FakeYoutubeDL.calls = []
    _FakeYoutubeDL.results = list(results)
    monkeypatch.setattr("app.services.source_extractors.youtube.yt_dlp.YoutubeDL", _FakeYoutubeDL)
    return _FakeYoutubeDL


def _install_subtitle_http(monkeypatch: pytest.MonkeyPatch, response: httpx.Response) -> None:
    """Replace the httpx.AsyncClient inside the youtube module with a MockTransport.

    Note: both the subtitle fetch AND the oembed fetch use the same patched
    client. Tests that care about oembed handling separately need to send a
    response shape that satisfies both; in practice the tests either use a
    JSON subtitle response (which oembed doesn't read) or use `_install_oembed`
    for oembed-specific testing.
    """

    def handler(_request: httpx.Request) -> httpx.Response:
        return response

    transport = httpx.MockTransport(handler)

    class _Client(httpx.AsyncClient):
        def __init__(self, *args: object, **kwargs: object) -> None:  # type: ignore[no-untyped-def]
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.services.source_extractors.youtube.httpx.AsyncClient", _Client)


def _install_http_by_url(
    monkeypatch: pytest.MonkeyPatch,
    *,
    subtitle_response: httpx.Response,
    oembed_response: httpx.Response,
) -> None:
    """Dispatch httpx responses by URL host so subtitle + oembed can differ."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "youtube.com/oembed" in str(request.url):
            return oembed_response
        return subtitle_response

    transport = httpx.MockTransport(handler)

    class _Client(httpx.AsyncClient):
        def __init__(self, *args: object, **kwargs: object) -> None:  # type: ignore[no-untyped-def]
            kwargs["transport"] = transport
            super().__init__(*args, **kwargs)

    monkeypatch.setattr("app.services.source_extractors.youtube.httpx.AsyncClient", _Client)


# --- Video ID extraction ---------------------------------------------------


class TestVideoIdExtraction:
    def test_standard_watch_url(self) -> None:
        assert _extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_youtu_be_short(self) -> None:
        assert _extract_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_youtu_be_with_timestamp(self) -> None:
        assert _extract_video_id("https://youtu.be/dQw4w9WgXcQ?t=42") == "dQw4w9WgXcQ"

    def test_mobile_youtube(self) -> None:
        assert _extract_video_id("https://m.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_http_scheme_accepted(self) -> None:
        assert _extract_video_id("http://youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_no_www(self) -> None:
        assert _extract_video_id("https://youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_shorts_url(self) -> None:
        assert _extract_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_embed_url(self) -> None:
        assert _extract_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"

    def test_with_extra_query_params(self) -> None:
        assert _extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&feature=share") == "dQw4w9WgXcQ"

    def test_with_underscore_and_dash_in_id(self) -> None:
        assert _extract_video_id("https://youtu.be/a_B-c1D2E3F") == "a_B-c1D2E3F"

    def test_non_youtube_url_raises(self) -> None:
        with pytest.raises(InvalidUrlError):
            _extract_video_id("https://vimeo.com/12345")

    def test_malformed_url_raises(self) -> None:
        with pytest.raises(InvalidUrlError):
            _extract_video_id("not a url at all")

    def test_empty_string_raises(self) -> None:
        with pytest.raises(InvalidUrlError):
            _extract_video_id("")

    def test_youtube_url_without_id_raises(self) -> None:
        with pytest.raises(InvalidUrlError):
            _extract_video_id("https://www.youtube.com/watch")


# --- Track picker ----------------------------------------------------------


class TestPickTranscriptTrack:
    def test_returns_none_when_no_tracks(self) -> None:
        assert _pick_transcript_track(_make_info()) is None

    def test_prefers_manual_english(self) -> None:
        info = _make_info(
            subtitles={"en": [_track("json3", "manual-en")]},
            automatic_captions={"en-orig": [_track("json3", "auto-en")]},
        )
        track = _pick_transcript_track(info)
        assert track is not None
        assert track["url"] == "manual-en"

    def test_accepts_en_variant_key(self) -> None:
        """YouTube uses weird keys like 'en-eEY6OEpapPo' for manual tracks."""
        info = _make_info(
            subtitles={"en-eEY6OEpapPo": [_track("json3", "variant-en")]},
        )
        track = _pick_transcript_track(info)
        assert track is not None
        assert track["url"] == "variant-en"

    def test_falls_back_to_dutch_manual(self) -> None:
        info = _make_info(
            subtitles={"nl": [_track("json3", "manual-nl")]},
        )
        track = _pick_transcript_track(info)
        assert track is not None
        assert track["url"] == "manual-nl"

    def test_falls_back_to_auto_original(self) -> None:
        info = _make_info(
            automatic_captions={"en-orig": [_track("json3", "auto-en-orig")]},
        )
        track = _pick_transcript_track(info)
        assert track is not None
        assert track["url"] == "auto-en-orig"

    def test_picks_json3_format_over_vtt_when_both_available(self) -> None:
        info = _make_info(
            subtitles={
                "en": [_track("vtt", "vtt-url"), _track("json3", "json3-url")],
            },
        )
        track = _pick_transcript_track(info)
        assert track is not None
        assert track["ext"] == "json3"
        assert track["url"] == "json3-url"

    def test_skips_machine_translated_auto_captions(self) -> None:
        """en-ar (English translated FROM Arabic) is poor quality; we skip it."""
        info = _make_info(
            automatic_captions={
                "en-ar": [_track("json3", "translated")],  # translated from Arabic
                "ar-orig": [_track("json3", "arabic-orig")],  # original Arabic
            },
        )
        track = _pick_transcript_track(info)
        assert track is not None
        assert track["url"] == "arabic-orig"


# --- JSON3 + VTT parsers ---------------------------------------------------


class TestJson3Parser:
    def test_concatenates_segments(self) -> None:
        payload = {
            "events": [
                {"segs": [{"utf8": "Hello"}, {"utf8": " "}, {"utf8": "world"}]},
                {"segs": [{"utf8": "second"}, {"utf8": " line"}]},
            ]
        }
        assert _parse_json3_transcript(payload) == "Hello world second line"

    def test_skips_newline_segments(self) -> None:
        payload = {"events": [{"segs": [{"utf8": "a"}, {"utf8": "\n"}, {"utf8": "b"}]}]}
        assert _parse_json3_transcript(payload) == "a b"

    def test_handles_missing_events(self) -> None:
        assert _parse_json3_transcript({}) == ""

    def test_handles_empty_segs(self) -> None:
        assert _parse_json3_transcript({"events": [{}, {"segs": []}]}) == ""


class TestVttParser:
    def test_strips_cue_timing_and_header(self) -> None:
        vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nHello world\n\n00:00:05.000 --> 00:00:10.000\nSecond line\n"
        assert _parse_vtt_transcript(vtt) == "Hello world Second line"

    def test_strips_html_tags(self) -> None:
        vtt = "WEBVTT\n\n00:00:00.000 --> 00:00:05.000\n<c.colorCCCCCC>formatted</c> text\n"
        assert _parse_vtt_transcript(vtt) == "formatted text"


# --- Happy path ------------------------------------------------------------


class TestTranscriptHappyPath:
    async def test_direct_fetch_returns_transcript(self, monkeypatch: pytest.MonkeyPatch) -> None:
        info = _make_info(subtitles={"en": [_track("json3", "https://sub/track")]})
        _install_ydl(monkeypatch, [info])
        _install_http_by_url(
            monkeypatch,
            subtitle_response=httpx.Response(
                200,
                json={"events": [{"segs": [{"utf8": "hello"}, {"utf8": " "}, {"utf8": "world"}]}]},
            ),
            oembed_response=httpx.Response(200, json={"title": "Test Video"}),
        )

        title, content, source_ref = await extract_youtube("https://youtu.be/dQw4w9WgXcQ")
        assert title == "Test Video"
        assert content == "hello world"
        assert source_ref == "youtube:dQw4w9WgXcQ"

    async def test_falls_back_to_vtt_when_no_json3(self, monkeypatch: pytest.MonkeyPatch) -> None:
        info = _make_info(subtitles={"en": [_track("vtt", "https://sub/vtt")]})
        _install_ydl(monkeypatch, [info])
        _install_http_by_url(
            monkeypatch,
            subtitle_response=httpx.Response(
                200,
                text="WEBVTT\n\n00:00:00.000 --> 00:00:05.000\nhello from vtt\n",
            ),
            oembed_response=httpx.Response(200, json={"title": "T"}),
        )

        _, content, _ = await extract_youtube("https://youtu.be/dQw4w9WgXcQ")
        assert content == "hello from vtt"


# --- Error classification --------------------------------------------------


class TestErrorClassification:
    @pytest.mark.parametrize(
        "message",
        [
            "ERROR: [youtube] abc: Video unavailable",
            "ERROR: [youtube] abc: Private video. Sign in",
            "ERROR: [youtube] abc: This video has been removed",
            "ERROR: [youtube] abc: This video is age-restricted",
        ],
    )
    async def test_unsupported_video_markers_map_to_422(self, message: str, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_ydl(monkeypatch, [DownloadError(message)])
        _install_http_by_url(
            monkeypatch,
            subtitle_response=httpx.Response(200, json={}),
            oembed_response=httpx.Response(200, json={"title": "T"}),
        )
        monkeypatch.setattr("app.services.source_extractors.youtube.settings.youtube_proxy_url", "")

        with pytest.raises(UnsupportedSourceError):
            await extract_youtube("https://youtu.be/dQw4w9WgXcQ")

    @pytest.mark.parametrize(
        "message",
        [
            "ERROR: [youtube] abc: Sign in to confirm you're not a bot",
            "ERROR: [youtube] abc: HTTP Error 429: Too Many Requests",
            "ERROR: [youtube] abc: HTTP Error 403: Forbidden",
            "ERROR: [youtube] abc: Unable to extract player response",
            "ERROR: [youtube] abc: Signature extraction failed",
        ],
    )
    async def test_upstream_blocked_markers_map_to_502(self, message: str, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_ydl(monkeypatch, [DownloadError(message)])
        _install_http_by_url(
            monkeypatch,
            subtitle_response=httpx.Response(200, json={}),
            oembed_response=httpx.Response(200, json={"title": "T"}),
        )
        monkeypatch.setattr("app.services.source_extractors.youtube.settings.youtube_proxy_url", "")

        with pytest.raises(SourceFetchError):
            await extract_youtube("https://youtu.be/dQw4w9WgXcQ")

    async def test_unknown_download_error_defaults_to_source_fetch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An unclassified DownloadError should surface as retryable, not 'no transcript'."""
        _install_ydl(monkeypatch, [DownloadError("ERROR: [youtube] abc: unexpected something")])
        _install_http_by_url(
            monkeypatch,
            subtitle_response=httpx.Response(200, json={}),
            oembed_response=httpx.Response(200, json={"title": "T"}),
        )
        monkeypatch.setattr("app.services.source_extractors.youtube.settings.youtube_proxy_url", "")

        with pytest.raises(SourceFetchError):
            await extract_youtube("https://youtu.be/dQw4w9WgXcQ")

    async def test_no_subtitle_tracks_raises_unsupported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Extract succeeded but the video has zero transcripts in any language."""
        _install_ydl(monkeypatch, [_make_info()])  # empty subtitles + auto_captions
        _install_http_by_url(
            monkeypatch,
            subtitle_response=httpx.Response(200, json={}),
            oembed_response=httpx.Response(200, json={"title": "T"}),
        )

        with pytest.raises(UnsupportedSourceError):
            await extract_youtube("https://youtu.be/dQw4w9WgXcQ")

    async def test_subtitle_fetch_non_200_raises_source_fetch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        info = _make_info(subtitles={"en": [_track("json3", "https://sub/broken")]})
        _install_ydl(monkeypatch, [info])
        _install_http_by_url(
            monkeypatch,
            subtitle_response=httpx.Response(503, text="service unavailable"),
            oembed_response=httpx.Response(200, json={"title": "T"}),
        )

        with pytest.raises(SourceFetchError):
            await extract_youtube("https://youtu.be/dQw4w9WgXcQ")


# --- Proxy fallback --------------------------------------------------------


class TestProxyFallback:
    async def test_proxy_retry_recovers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Direct blocked, proxy succeeds → happy path transcript."""
        monkeypatch.setattr(
            "app.services.source_extractors.youtube.settings.youtube_proxy_url",
            "http://user:pass@proxy.example:9999",
        )
        blocked = DownloadError("Sign in to confirm you're not a bot")
        success_info = _make_info(subtitles={"en": [_track("json3", "https://sub/ok")]})
        factory = _install_ydl(monkeypatch, [blocked, success_info])
        _install_http_by_url(
            monkeypatch,
            subtitle_response=httpx.Response(
                200,
                json={"events": [{"segs": [{"utf8": "via proxy"}]}]},
            ),
            oembed_response=httpx.Response(200, json={"title": "T"}),
        )

        _, content, _ = await extract_youtube("https://youtu.be/dQw4w9WgXcQ")

        assert content == "via proxy"
        assert len(factory.calls) == 2
        assert factory.calls[0]["proxy"] is None
        assert factory.calls[1]["proxy"] == "http://user:pass@proxy.example:9999"

    async def test_proxy_also_blocked_raises_source_fetch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "app.services.source_extractors.youtube.settings.youtube_proxy_url",
            "http://user:pass@proxy.example:9999",
        )
        _install_ydl(
            monkeypatch,
            [
                DownloadError("HTTP Error 429: Too Many Requests"),
                DownloadError("HTTP Error 403: Forbidden"),
            ],
        )
        _install_http_by_url(
            monkeypatch,
            subtitle_response=httpx.Response(200, json={}),
            oembed_response=httpx.Response(200, json={"title": "T"}),
        )

        with pytest.raises(SourceFetchError):
            await extract_youtube("https://youtu.be/dQw4w9WgXcQ")

    async def test_unsupported_does_not_trigger_proxy_retry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """'Video unavailable' → proxy wouldn't help, so we shouldn't retry."""
        monkeypatch.setattr(
            "app.services.source_extractors.youtube.settings.youtube_proxy_url",
            "http://user:pass@proxy.example:9999",
        )
        factory = _install_ydl(monkeypatch, [DownloadError("Video unavailable")])
        _install_http_by_url(
            monkeypatch,
            subtitle_response=httpx.Response(200, json={}),
            oembed_response=httpx.Response(200, json={"title": "T"}),
        )

        with pytest.raises(UnsupportedSourceError):
            await extract_youtube("https://youtu.be/dQw4w9WgXcQ")

        assert len(factory.calls) == 1  # no proxy retry

    async def test_no_proxy_config_no_retry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("app.services.source_extractors.youtube.settings.youtube_proxy_url", "")
        factory = _install_ydl(monkeypatch, [DownloadError("Sign in to confirm you're not a bot")])
        _install_http_by_url(
            monkeypatch,
            subtitle_response=httpx.Response(200, json={}),
            oembed_response=httpx.Response(200, json={"title": "T"}),
        )

        with pytest.raises(SourceFetchError):
            await extract_youtube("https://youtu.be/dQw4w9WgXcQ")

        assert len(factory.calls) == 1


# --- Oembed title ----------------------------------------------------------


class TestOembedTitle:
    def _base_info(self) -> dict[str, Any]:
        return _make_info(subtitles={"en": [_track("json3", "https://sub/ok")]})

    async def test_title_from_oembed_on_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_ydl(monkeypatch, [self._base_info()])
        _install_http_by_url(
            monkeypatch,
            subtitle_response=httpx.Response(200, json={"events": [{"segs": [{"utf8": "x"}]}]}),
            oembed_response=httpx.Response(200, json={"title": "Never Gonna Give You Up"}),
        )

        title, _, _ = await extract_youtube("https://youtu.be/dQw4w9WgXcQ")
        assert title == "Never Gonna Give You Up"

    async def test_fallback_on_oembed_404(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_ydl(monkeypatch, [self._base_info()])
        _install_http_by_url(
            monkeypatch,
            subtitle_response=httpx.Response(200, json={"events": [{"segs": [{"utf8": "x"}]}]}),
            oembed_response=httpx.Response(404, json={"error": "not found"}),
        )

        title, _, _ = await extract_youtube("https://youtu.be/dQw4w9WgXcQ")
        assert title == "YouTube video dQw4w9WgXcQ"

    async def test_fallback_on_oembed_missing_title_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_ydl(monkeypatch, [self._base_info()])
        _install_http_by_url(
            monkeypatch,
            subtitle_response=httpx.Response(200, json={"events": [{"segs": [{"utf8": "x"}]}]}),
            oembed_response=httpx.Response(200, json={"author": "X"}),
        )

        title, _, _ = await extract_youtube("https://youtu.be/dQw4w9WgXcQ")
        assert title == "YouTube video dQw4w9WgXcQ"


# --- Source ref + return shape --------------------------------------------


class TestSourceRef:
    async def test_source_ref_format(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_ydl(
            monkeypatch,
            [_make_info(subtitles={"en": [_track("json3", "https://sub/ok")]})],
        )
        _install_http_by_url(
            monkeypatch,
            subtitle_response=httpx.Response(200, json={"events": [{"segs": [{"utf8": "x"}]}]}),
            oembed_response=httpx.Response(200, json={"title": "T"}),
        )

        _, _, source_ref = await extract_youtube("https://youtu.be/dQw4w9WgXcQ")
        assert source_ref == "youtube:dQw4w9WgXcQ"

    async def test_source_ref_stable_across_variants(self, monkeypatch: pytest.MonkeyPatch) -> None:
        info = _make_info(subtitles={"en": [_track("json3", "https://sub/ok")]})
        # Because the factory stack reuses the last entry once exhausted,
        # we can just provide one info dict; it'll be returned for every call.
        _install_ydl(monkeypatch, [info])
        _install_http_by_url(
            monkeypatch,
            subtitle_response=httpx.Response(200, json={"events": [{"segs": [{"utf8": "x"}]}]}),
            oembed_response=httpx.Response(200, json={"title": "T"}),
        )

        refs = []
        for url in (
            "https://youtu.be/dQw4w9WgXcQ",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://m.youtube.com/watch?v=dQw4w9WgXcQ&feature=share",
            "http://youtube.com/watch?v=dQw4w9WgXcQ",
        ):
            _, _, ref = await extract_youtube(url)
            refs.append(ref)
        assert len(set(refs)) == 1
        assert refs[0] == "youtube:dQw4w9WgXcQ"


class TestReturnShape:
    async def test_returns_three_tuple(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _install_ydl(
            monkeypatch,
            [_make_info(subtitles={"en": [_track("json3", "https://sub/ok")]})],
        )
        _install_http_by_url(
            monkeypatch,
            subtitle_response=httpx.Response(200, json={"events": [{"segs": [{"utf8": "x"}]}]}),
            oembed_response=httpx.Response(200, json={"title": "T"}),
        )

        result = await extract_youtube("https://youtu.be/dQw4w9WgXcQ")
        assert isinstance(result, tuple)
        assert len(result) == 3
