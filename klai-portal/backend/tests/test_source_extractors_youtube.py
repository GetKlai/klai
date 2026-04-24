"""Tests for the YouTube source extractor (SPEC-KB-SOURCES-001 Module 3).

Covers video-ID extraction across URL variants, transcript fetching via
youtube-transcript-api (mocked), and oembed title resolution with
best-effort fallback.

Error-mapping invariants (SPEC-KB-SOURCES-001 v1.3):
- NoTranscriptFound / TranscriptsDisabled / VideoUnavailable
  → UnsupportedSourceError (route → 422, "no transcript").
- RequestBlocked / IpBlocked / YouTubeRequestFailed /
  CouldNotRetrieveTranscript
  → SourceFetchError (route → 502, "could not reach YouTube").
  With ``settings.youtube_proxy_url`` configured, a retry is attempted
  via the proxy before the SourceFetchError is raised.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx
import pytest
from youtube_transcript_api import (
    CouldNotRetrieveTranscript,
    IpBlocked,
    NoTranscriptFound,
    RequestBlocked,
    TranscriptsDisabled,
    VideoUnavailable,
    YouTubeRequestFailed,
)

from app.services.source_extractors.exceptions import (
    InvalidUrlError,
    SourceFetchError,
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


def _make_factory(results: list[list[_Snippet] | Exception]):
    """Build a ``YouTubeTranscriptApi(proxy_config=...)`` factory.

    ``results`` is the queue of outcomes for successive instantiations:
    index 0 is returned on the first ``YouTubeTranscriptApi(...)`` call,
    index 1 on the second, etc. Each entry is either snippet list or an
    exception to raise from ``.fetch``. Matches the production retry
    flow: direct call first, optional proxy-retry on upstream block.
    """
    calls: list[dict[str, Any]] = []

    def _factory(*, proxy_config: Any = None) -> _FakeApi:
        calls.append({"proxy_config": proxy_config})
        result = results[min(len(calls) - 1, len(results) - 1)]
        return _FakeApi(result)

    _factory.calls = calls  # type: ignore[attr-defined]
    return _factory


@pytest.fixture
def mock_transcript(monkeypatch: pytest.MonkeyPatch):
    """Install a one-shot transcript result (no retry)."""

    def _install(result: list[_Snippet] | Exception) -> None:
        monkeypatch.setattr(
            "app.services.source_extractors.youtube.YouTubeTranscriptApi",
            _make_factory([result]),
        )

    return _install


@pytest.fixture
def mock_transcript_sequence(monkeypatch: pytest.MonkeyPatch):
    """Install an ordered sequence of results for successive API instantiations.

    Returns the factory object so tests can inspect ``.calls`` to assert
    how many times (and with which proxy_config) the API was instantiated.
    """

    def _install(results: list[list[_Snippet] | Exception]):
        factory = _make_factory(results)
        monkeypatch.setattr(
            "app.services.source_extractors.youtube.YouTubeTranscriptApi",
            factory,
        )
        return factory

    return _install


@pytest.fixture
def mock_oembed(monkeypatch: pytest.MonkeyPatch):
    """Replace the oembed AsyncClient with a MockTransport."""

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
        assert _extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&feature=share") == "dQw4w9WgXcQ"

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

    async def test_transcripts_disabled_raises_unsupported(self, mock_transcript, mock_oembed) -> None:
        mock_transcript(TranscriptsDisabled("dQw4w9WgXcQ"))
        mock_oembed(httpx.Response(200, json={"title": "T"}))
        with pytest.raises(UnsupportedSourceError):
            await extract_youtube("https://youtu.be/dQw4w9WgXcQ")

    async def test_video_unavailable_raises_unsupported(self, mock_transcript, mock_oembed) -> None:
        mock_transcript(VideoUnavailable("dQw4w9WgXcQ"))
        mock_oembed(httpx.Response(200, json={"title": "T"}))
        with pytest.raises(UnsupportedSourceError):
            await extract_youtube("https://youtu.be/dQw4w9WgXcQ")

    async def test_empty_transcript_raises_unsupported(self, mock_transcript, mock_oembed) -> None:
        mock_transcript([_Snippet(text=""), _Snippet(text="   ")])
        mock_oembed(httpx.Response(200, json={"title": "T"}))
        with pytest.raises(UnsupportedSourceError):
            await extract_youtube("https://youtu.be/dQw4w9WgXcQ")


class TestUpstreamBlocked:
    """RequestBlocked / IpBlocked / YouTubeRequestFailed / CouldNotRetrieveTranscript
    must NOT leak as "no transcript" — they're infrastructure, not user input."""

    async def test_request_blocked_no_proxy_raises_source_fetch(
        self, mock_transcript, mock_oembed, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.services.source_extractors.youtube.settings.youtube_proxy_url", "")
        mock_transcript(RequestBlocked("dQw4w9WgXcQ"))
        mock_oembed(httpx.Response(200, json={"title": "T"}))
        with pytest.raises(SourceFetchError):
            await extract_youtube("https://youtu.be/dQw4w9WgXcQ")

    async def test_ip_blocked_no_proxy_raises_source_fetch(
        self, mock_transcript, mock_oembed, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.services.source_extractors.youtube.settings.youtube_proxy_url", "")
        mock_transcript(IpBlocked("dQw4w9WgXcQ"))
        mock_oembed(httpx.Response(200, json={"title": "T"}))
        with pytest.raises(SourceFetchError):
            await extract_youtube("https://youtu.be/dQw4w9WgXcQ")

    async def test_youtube_request_failed_no_proxy_raises_source_fetch(
        self, mock_transcript, mock_oembed, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.services.source_extractors.youtube.settings.youtube_proxy_url", "")
        mock_transcript(YouTubeRequestFailed("dQw4w9WgXcQ", "network error"))
        mock_oembed(httpx.Response(200, json={"title": "T"}))
        with pytest.raises(SourceFetchError):
            await extract_youtube("https://youtu.be/dQw4w9WgXcQ")

    async def test_could_not_retrieve_transcript_raises_source_fetch(
        self, mock_transcript, mock_oembed, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("app.services.source_extractors.youtube.settings.youtube_proxy_url", "")
        mock_transcript(CouldNotRetrieveTranscript("dQw4w9WgXcQ"))
        mock_oembed(httpx.Response(200, json={"title": "T"}))
        with pytest.raises(SourceFetchError):
            await extract_youtube("https://youtu.be/dQw4w9WgXcQ")


class TestProxyFallback:
    """When YOUTUBE_PROXY_URL is set, upstream blocks trigger one retry."""

    async def test_proxy_retry_recovers(
        self, mock_transcript_sequence, mock_oembed, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """First call blocked, proxy retry succeeds → happy-path transcript."""
        monkeypatch.setattr(
            "app.services.source_extractors.youtube.settings.youtube_proxy_url",
            "http://user:pass@proxy.example:9999",
        )
        factory = mock_transcript_sequence(
            [
                RequestBlocked("dQw4w9WgXcQ"),  # direct call
                [_Snippet(text="works"), _Snippet(text="via"), _Snippet(text="proxy")],  # proxy call
            ]
        )
        mock_oembed(httpx.Response(200, json={"title": "T"}))

        _, content, _ = await extract_youtube("https://youtu.be/dQw4w9WgXcQ")

        assert content == "works via proxy"
        # Two instantiations: first direct (no proxy), second with proxy_config.
        assert len(factory.calls) == 2
        assert factory.calls[0]["proxy_config"] is None
        assert factory.calls[1]["proxy_config"] is not None

    async def test_proxy_retry_still_no_transcript_raises_unsupported(
        self, mock_transcript_sequence, mock_oembed, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Proxy got through but the video really has no transcript."""
        monkeypatch.setattr(
            "app.services.source_extractors.youtube.settings.youtube_proxy_url",
            "http://user:pass@proxy.example:9999",
        )
        mock_transcript_sequence(
            [
                RequestBlocked("dQw4w9WgXcQ"),
                NoTranscriptFound("dQw4w9WgXcQ", ["en"], None),
            ]
        )
        mock_oembed(httpx.Response(200, json={"title": "T"}))

        with pytest.raises(UnsupportedSourceError):
            await extract_youtube("https://youtu.be/dQw4w9WgXcQ")

    async def test_proxy_retry_also_blocked_raises_source_fetch(
        self, mock_transcript_sequence, mock_oembed, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If proxy is ALSO blocked (rare but possible), surface 502."""
        monkeypatch.setattr(
            "app.services.source_extractors.youtube.settings.youtube_proxy_url",
            "http://user:pass@proxy.example:9999",
        )
        mock_transcript_sequence(
            [
                RequestBlocked("dQw4w9WgXcQ"),
                IpBlocked("dQw4w9WgXcQ"),
            ]
        )
        mock_oembed(httpx.Response(200, json={"title": "T"}))

        with pytest.raises(SourceFetchError):
            await extract_youtube("https://youtu.be/dQw4w9WgXcQ")

    async def test_no_proxy_config_no_retry(
        self, mock_transcript_sequence, mock_oembed, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without proxy URL, the direct call is the only attempt."""
        monkeypatch.setattr("app.services.source_extractors.youtube.settings.youtube_proxy_url", "")
        factory = mock_transcript_sequence([RequestBlocked("dQw4w9WgXcQ")])
        mock_oembed(httpx.Response(200, json={"title": "T"}))

        with pytest.raises(SourceFetchError):
            await extract_youtube("https://youtu.be/dQw4w9WgXcQ")

        assert len(factory.calls) == 1
        assert factory.calls[0]["proxy_config"] is None


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

    async def test_fallback_on_oembed_missing_title_key(self, mock_transcript, mock_oembed) -> None:
        mock_transcript([_Snippet(text="words here")])
        mock_oembed(httpx.Response(200, json={"author": "X"}))  # no 'title'
        title, _, _ = await extract_youtube("https://youtu.be/dQw4w9WgXcQ")
        assert title == "YouTube video dQw4w9WgXcQ"

    async def test_fallback_on_oembed_non_json(self, mock_transcript, mock_oembed) -> None:
        mock_transcript([_Snippet(text="words here")])
        mock_oembed(httpx.Response(200, content=b"<html>not json</html>"))
        title, _, _ = await extract_youtube("https://youtu.be/dQw4w9WgXcQ")
        assert title == "YouTube video dQw4w9WgXcQ"

    async def test_oembed_failure_does_not_fail_ingest(self, mock_transcript, mock_oembed) -> None:
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
