"""Tests for the YouTube source extractor (SPEC-KB-SOURCES-001 Module 3).

Covers video-ID extraction across URL variants, transcript fetching via
youtube-transcript-api (mocked), and oembed title resolution with
best-effort fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from youtube_transcript_api import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
)

from app.services.source_extractors.exceptions import (
    InvalidUrlError,
    UnsupportedSourceError,
)
from app.services.source_extractors.youtube import (
    _extract_video_id,
    extract_youtube,
)


@dataclass
class _Snippet:
    """Minimal stand-in for youtube_transcript_api.FetchedTranscriptSnippet."""

    text: str


class _FakeTranscript:
    def __init__(self, snippets: list[_Snippet]) -> None:
        self._snippets = snippets

    def __iter__(self):
        return iter(self._snippets)


class _FakeApi:
    """Drop-in replacement for YouTubeTranscriptApi() in tests."""

    def __init__(self, snippets: list[_Snippet] | Exception) -> None:
        self._result = snippets

    def fetch(self, video_id: str, languages: list[str] | None = None) -> Any:
        if isinstance(self._result, Exception):
            raise self._result
        return _FakeTranscript(self._result)


@pytest.fixture
def mock_transcript(monkeypatch: pytest.MonkeyPatch):
    """Return a callable that installs a transcript or exception into the extractor."""

    def _install(result: list[_Snippet] | Exception) -> None:
        monkeypatch.setattr(
            "app.services.source_extractors.youtube.YouTubeTranscriptApi",
            lambda: _FakeApi(result),
        )

    return _install


@pytest.fixture
def mock_oembed(monkeypatch: pytest.MonkeyPatch):
    """Replace the oembed AsyncClient with a MockTransport that returns the given response."""

    def _install(response: httpx.Response) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return response

        transport = httpx.MockTransport(handler)

        class _Client(httpx.AsyncClient):
            def __init__(self, *args: object, **kwargs: object) -> None:  # type: ignore[no-untyped-def]
                kwargs["transport"] = transport
                super().__init__(*args, **kwargs)

        monkeypatch.setattr("app.services.source_extractors.youtube.httpx.AsyncClient", _Client)

    return _install


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
        assert (
            _extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&feature=share")
            == "dQw4w9WgXcQ"
        )

    def test_with_underscore_and_dash_in_id(self) -> None:
        # Real video IDs contain [A-Za-z0-9_-]
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


class TestTranscriptFetching:
    async def test_happy_path_returns_joined_transcript(self, mock_transcript, mock_oembed) -> None:
        mock_transcript(
            [
                _Snippet(text="Hello"),
                _Snippet(text="world"),
                _Snippet(text="from YouTube"),
            ]
        )
        mock_oembed(httpx.Response(200, json={"title": "Test Video"}))
        _, content, _ = await extract_youtube("https://youtu.be/dQw4w9WgXcQ")
        assert content == "Hello world from YouTube"

    async def test_empty_transcript_segments_skipped(self, mock_transcript, mock_oembed) -> None:
        mock_transcript(
            [
                _Snippet(text="one"),
                _Snippet(text=""),
                _Snippet(text="   "),
                _Snippet(text="two"),
            ]
        )
        mock_oembed(httpx.Response(200, json={"title": "T"}))
        _, content, _ = await extract_youtube("https://youtu.be/dQw4w9WgXcQ")
        assert content == "one two"

    async def test_no_transcript_raises_unsupported(self, mock_transcript, mock_oembed) -> None:
        mock_transcript(NoTranscriptFound("dQw4w9WgXcQ", ["en"], None))
        mock_oembed(httpx.Response(200, json={"title": "T"}))
        with pytest.raises(UnsupportedSourceError):
            await extract_youtube("https://youtu.be/dQw4w9WgXcQ")

    async def test_transcripts_disabled_raises_unsupported(
        self, mock_transcript, mock_oembed
    ) -> None:
        mock_transcript(TranscriptsDisabled("dQw4w9WgXcQ"))
        mock_oembed(httpx.Response(200, json={"title": "T"}))
        with pytest.raises(UnsupportedSourceError):
            await extract_youtube("https://youtu.be/dQw4w9WgXcQ")

    async def test_video_unavailable_raises_unsupported(
        self, mock_transcript, mock_oembed
    ) -> None:
        mock_transcript(VideoUnavailable("dQw4w9WgXcQ"))
        mock_oembed(httpx.Response(200, json={"title": "T"}))
        with pytest.raises(UnsupportedSourceError):
            await extract_youtube("https://youtu.be/dQw4w9WgXcQ")

    async def test_empty_transcript_raises_unsupported(self, mock_transcript, mock_oembed) -> None:
        mock_transcript([_Snippet(text=""), _Snippet(text="   ")])
        mock_oembed(httpx.Response(200, json={"title": "T"}))
        with pytest.raises(UnsupportedSourceError):
            await extract_youtube("https://youtu.be/dQw4w9WgXcQ")


class TestOembedTitle:
    async def test_title_from_oembed_on_success(self, mock_transcript, mock_oembed) -> None:
        mock_transcript([_Snippet(text="words here")])
        mock_oembed(httpx.Response(200, json={"title": "Never Gonna Give You Up"}))
        title, _, _ = await extract_youtube("https://youtu.be/dQw4w9WgXcQ")
        assert title == "Never Gonna Give You Up"

    async def test_fallback_on_oembed_404(self, mock_transcript, mock_oembed) -> None:
        mock_transcript([_Snippet(text="words here")])
        mock_oembed(httpx.Response(404, json={"error": "not found"}))
        title, _, _ = await extract_youtube("https://youtu.be/dQw4w9WgXcQ")
        assert title == "YouTube video dQw4w9WgXcQ"

    async def test_fallback_on_oembed_missing_title_key(
        self, mock_transcript, mock_oembed
    ) -> None:
        mock_transcript([_Snippet(text="words here")])
        mock_oembed(httpx.Response(200, json={"author": "X"}))  # no 'title'
        title, _, _ = await extract_youtube("https://youtu.be/dQw4w9WgXcQ")
        assert title == "YouTube video dQw4w9WgXcQ"

    async def test_fallback_on_oembed_non_json(
        self, mock_transcript, mock_oembed
    ) -> None:
        mock_transcript([_Snippet(text="words here")])
        mock_oembed(httpx.Response(200, content=b"<html>not json</html>"))
        title, _, _ = await extract_youtube("https://youtu.be/dQw4w9WgXcQ")
        assert title == "YouTube video dQw4w9WgXcQ"

    async def test_oembed_failure_does_not_fail_ingest(
        self, mock_transcript, mock_oembed
    ) -> None:
        """R3.4: oembed failure must NOT fail the whole ingest — transcript is primary."""
        mock_transcript([_Snippet(text="primary payload here")])
        mock_oembed(httpx.Response(500))
        title, content, _ = await extract_youtube("https://youtu.be/dQw4w9WgXcQ")
        assert content == "primary payload here"
        assert title.startswith("YouTube video")


class TestSourceRef:
    async def test_source_ref_format(self, mock_transcript, mock_oembed) -> None:
        mock_transcript([_Snippet(text="x")])
        mock_oembed(httpx.Response(200, json={"title": "T"}))
        _, _, source_ref = await extract_youtube("https://youtu.be/dQw4w9WgXcQ")
        assert source_ref == "youtube:dQw4w9WgXcQ"

    async def test_source_ref_stable_across_variants(self, mock_transcript, mock_oembed) -> None:
        mock_transcript([_Snippet(text="x")])
        mock_oembed(httpx.Response(200, json={"title": "T"}))
        variants = [
            "https://youtu.be/dQw4w9WgXcQ",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://m.youtube.com/watch?v=dQw4w9WgXcQ&feature=share",
            "http://youtube.com/watch?v=dQw4w9WgXcQ",
        ]
        refs = []
        for url in variants:
            _, _, ref = await extract_youtube(url)
            refs.append(ref)
        assert len(set(refs)) == 1
        assert refs[0] == "youtube:dQw4w9WgXcQ"


class TestReturnShape:
    async def test_returns_three_tuple(self, mock_transcript, mock_oembed) -> None:
        mock_transcript([_Snippet(text="x")])
        mock_oembed(httpx.Response(200, json={"title": "T"}))
        result = await extract_youtube("https://youtu.be/dQw4w9WgXcQ")
        assert isinstance(result, tuple)
        assert len(result) == 3
