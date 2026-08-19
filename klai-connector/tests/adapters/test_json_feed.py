"""Contract tests for the generic JSON feed adapter."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from klai_image_storage import PinnedResolverTransport

from app.adapters.json_feed import JsonFeedAdapter
from app.clients.knowledge_ingest import MAX_INGEST_CONTENT_CHARS
from app.services.url_guard import PersistedUrlRejectedError


def _connector(url: str = "https://data.example.com/feed.json?token=secret") -> SimpleNamespace:
    return SimpleNamespace(id="connector-123", config={"url": url})


async def test_list_documents_exposes_one_stable_json_document() -> None:
    adapter = JsonFeedAdapter()
    validated = SimpleNamespace(hostname="data.example.com", preferred_ip="203.0.113.10")

    with patch(
        "app.adapters.json_feed.validate_json_feed_url_strict",
        new=AsyncMock(return_value=validated),
    ):
        refs = await adapter.list_documents(_connector())

    assert len(refs) == 1
    assert refs[0].path == "json-feed/connector-123.json"
    assert refs[0].content_type == "kb_article"
    assert refs[0].source_ref == "json-feed:connector-123"
    assert refs[0].source_url == "https://data.example.com"
    assert "secret" not in refs[0].source_url


async def test_list_documents_requires_a_url() -> None:
    adapter = JsonFeedAdapter()

    with pytest.raises(ValueError, match="missing required field 'url'"):
        await adapter.list_documents(SimpleNamespace(id="connector-123", config={}))


async def test_list_documents_reports_missing_url_when_config_is_null() -> None:
    adapter = JsonFeedAdapter()

    with pytest.raises(ValueError, match="missing required field 'url'"):
        await adapter.list_documents(SimpleNamespace(id="connector-123", config=None))


async def test_list_documents_strips_url_credentials_from_citation() -> None:
    adapter = JsonFeedAdapter()
    validated = SimpleNamespace(hostname="data.example.com", preferred_ip="203.0.113.10")

    with patch(
        "app.adapters.json_feed.validate_json_feed_url_strict",
        new=AsyncMock(return_value=validated),
    ):
        refs = await adapter.list_documents(_connector("https://user:password@data.example.com/feed.json?token=secret"))

    assert refs[0].source_url == "https://data.example.com"


async def test_list_documents_strips_url_path_secrets_from_citation() -> None:
    adapter = JsonFeedAdapter()
    validated = SimpleNamespace(hostname="data.example.com", preferred_ip="203.0.113.10")

    with patch(
        "app.adapters.json_feed.validate_json_feed_url_strict",
        new=AsyncMock(return_value=validated),
    ):
        refs = await adapter.list_documents(
            _connector("https://data.example.com/private/path-token/feed.json")
        )

    assert refs[0].source_url == "https://data.example.com"
    assert "path-token" not in refs[0].source_url


@pytest.mark.parametrize(
    ("url", "expected_source_url"),
    [
        (
            "https://user:password@[2001:db8::1]:8443/private/feed.json?token=secret",
            "https://[2001:db8::1]:8443",
        ),
        (
            "https://data.example.com:8443/private/feed.json?token=secret",
            "https://data.example.com:8443",
        ),
    ],
)
async def test_list_documents_preserves_origin_port_and_ipv6_syntax(
    url: str,
    expected_source_url: str,
) -> None:
    adapter = JsonFeedAdapter()
    validated = SimpleNamespace(hostname="data.example.com", preferred_ip="203.0.113.10")

    with patch(
        "app.adapters.json_feed.validate_json_feed_url_strict",
        new=AsyncMock(return_value=validated),
    ):
        refs = await adapter.list_documents(_connector(url))

    assert refs[0].source_url == expected_source_url
    assert "password" not in refs[0].source_url
    assert "secret" not in refs[0].source_url


async def test_fetch_document_returns_json_bytes() -> None:
    adapter = JsonFeedAdapter()
    ref = (await _listed_ref(adapter))[0]
    response = _StreamingResponse(
        status_code=200,
        headers={"content-type": "application/json; charset=utf-8"},
        chunks=[b'[{"service":', b'"Support","price":42}]'],
    )

    with (
        patch(
            "app.adapters.json_feed.validate_json_feed_url_strict",
            new=AsyncMock(return_value=SimpleNamespace(hostname="data.example.com", preferred_ip="203.0.113.10")),
        ),
        patch("app.adapters.json_feed.httpx.AsyncClient", return_value=_StreamingClient(response)),
    ):
        content = await adapter.fetch_document(ref, _connector())

    assert content == b'[\n  {\n    "service": "Support",\n    "price": 42\n  }\n]'


async def test_fetch_document_accepts_valid_json_with_legacy_content_type() -> None:
    adapter = JsonFeedAdapter()
    ref = (await _listed_ref(adapter))[0]
    response = _StreamingResponse(
        status_code=200,
        headers={"content-type": "text/plain"},
        chunks=[b'{"description":"A sufficiently detailed price description","price":42}'],
    )

    with (
        patch(
            "app.adapters.json_feed.validate_json_feed_url_strict",
            new=AsyncMock(return_value=SimpleNamespace(hostname="data.example.com", preferred_ip="203.0.113.10")),
        ),
        patch("app.adapters.json_feed.httpx.AsyncClient", return_value=_StreamingClient(response)),
    ):
        content = await adapter.fetch_document(ref, _connector())

    assert content == (b'{\n  "description": "A sufficiently detailed price description",\n  "price": 42\n}')


async def test_fetch_document_normalizes_utf16_json_to_utf8() -> None:
    adapter = JsonFeedAdapter()
    ref = (await _listed_ref(adapter))[0]
    response = _StreamingResponse(
        status_code=200,
        headers={"content-type": "application/json"},
        chunks=['{"service":"Ondersteuning","description":"Uitgebreide prijsinformatie","price":42}'.encode("utf-16")],
    )

    with (
        patch(
            "app.adapters.json_feed.validate_json_feed_url_strict",
            new=AsyncMock(return_value=SimpleNamespace(hostname="data.example.com", preferred_ip="203.0.113.10")),
        ),
        patch("app.adapters.json_feed.httpx.AsyncClient", return_value=_StreamingClient(response)),
    ):
        content = await adapter.fetch_document(ref, _connector())

    assert content.decode("utf-8") == (
        '{\n  "service": "Ondersteuning",\n  "description": "Uitgebreide prijsinformatie",\n  "price": 42\n}'
    )


async def test_fetch_document_rejects_feed_that_exceeds_ingest_character_limit() -> None:
    adapter = JsonFeedAdapter()
    ref = (await _listed_ref(adapter))[0]
    response = _StreamingResponse(
        status_code=200,
        headers={"content-type": "application/json"},
        chunks=[('"' + ("x" * MAX_INGEST_CONTENT_CHARS) + '"').encode()],
    )

    with (
        patch(
            "app.adapters.json_feed.validate_json_feed_url_strict",
            new=AsyncMock(return_value=SimpleNamespace(hostname="data.example.com", preferred_ip="203.0.113.10")),
        ),
        patch("app.adapters.json_feed.httpx.AsyncClient", return_value=_StreamingClient(response)),
        pytest.raises(ValueError, match=r"ingest limit is 500000 characters"),
    ):
        await adapter.fetch_document(ref, _connector())


@pytest.mark.parametrize("payload", [b"[]", b"{}", b"null", b"42"])
async def test_fetch_document_rejects_json_with_too_little_knowledge(payload: bytes) -> None:
    adapter = JsonFeedAdapter()
    ref = (await _listed_ref(adapter))[0]
    response = _StreamingResponse(status_code=200, headers={}, chunks=[payload])

    with (
        patch(
            "app.adapters.json_feed.validate_json_feed_url_strict",
            new=AsyncMock(return_value=SimpleNamespace(hostname="data.example.com", preferred_ip="203.0.113.10")),
        ),
        patch("app.adapters.json_feed.httpx.AsyncClient", return_value=_StreamingClient(response)),
        pytest.raises(ValueError, match="too little knowledge"),
    ):
        await adapter.fetch_document(ref, _connector())


async def test_fetch_document_rejects_invalid_json() -> None:
    adapter = JsonFeedAdapter()
    ref = (await _listed_ref(adapter))[0]
    response = _StreamingResponse(status_code=200, headers={"content-type": "application/json"}, chunks=[b"<html />"])

    with (
        patch(
            "app.adapters.json_feed.validate_json_feed_url_strict",
            new=AsyncMock(return_value=SimpleNamespace(hostname="data.example.com", preferred_ip="203.0.113.10")),
        ),
        patch("app.adapters.json_feed.httpx.AsyncClient", return_value=_StreamingClient(response)),
        pytest.raises(ValueError, match="invalid JSON"),
    ):
        await adapter.fetch_document(ref, _connector())


async def test_fetch_document_rejects_redirects_without_leaking_url() -> None:
    adapter = JsonFeedAdapter()
    ref = (await _listed_ref(adapter))[0]
    response = _StreamingResponse(status_code=302, headers={"location": "https://elsewhere.example/"}, chunks=[])

    with (
        patch(
            "app.adapters.json_feed.validate_json_feed_url_strict",
            new=AsyncMock(return_value=SimpleNamespace(hostname="data.example.com", preferred_ip="203.0.113.10")),
        ),
        patch("app.adapters.json_feed.httpx.AsyncClient", return_value=_StreamingClient(response)),
        pytest.raises(ValueError) as exc_info,
    ):
        await adapter.fetch_document(ref, _connector())

    assert "HTTP 302" in str(exc_info.value)
    assert "secret" not in str(exc_info.value)


async def test_fetch_document_configures_pinned_transport_and_disables_redirects() -> None:
    adapter = JsonFeedAdapter()
    ref = (await _listed_ref(adapter))[0]
    response = _StreamingResponse(
        status_code=200,
        headers={},
        chunks=[b'{"description":"This payload is deliberately long enough to ingest."}'],
    )
    client_factory = MagicMock(return_value=_StreamingClient(response))

    with (
        patch(
            "app.adapters.json_feed.validate_json_feed_url_strict",
            new=AsyncMock(return_value=SimpleNamespace(hostname="data.example.com", preferred_ip="203.0.113.10")),
        ),
        patch("app.adapters.json_feed.httpx.AsyncClient", client_factory),
    ):
        await adapter.fetch_document(ref, _connector())

    kwargs = client_factory.call_args.kwargs
    assert kwargs["follow_redirects"] is False
    assert isinstance(kwargs["transport"], PinnedResolverTransport)
    assert kwargs["transport"]._pinned == {"data.example.com": "203.0.113.10"}


@pytest.mark.parametrize("declared_size", ["11", "invalid"])
async def test_fetch_document_enforces_stream_limit_even_with_untrusted_content_length(
    monkeypatch: pytest.MonkeyPatch,
    declared_size: str,
) -> None:
    import app.adapters.json_feed as json_feed_module

    monkeypatch.setattr(json_feed_module, "_MAX_FEED_SIZE", 10)
    adapter = JsonFeedAdapter()
    ref = (await _listed_ref(adapter))[0]
    response = _StreamingResponse(
        status_code=200,
        headers={"content-length": declared_size},
        chunks=[b'{"value":', b'"long"}'],
    )

    with (
        patch(
            "app.adapters.json_feed.validate_json_feed_url_strict",
            new=AsyncMock(return_value=SimpleNamespace(hostname="data.example.com", preferred_ip="203.0.113.10")),
        ),
        patch("app.adapters.json_feed.httpx.AsyncClient", return_value=_StreamingClient(response)),
        pytest.raises(ValueError, match="too large"),
    ):
        await adapter.fetch_document(ref, _connector())


async def test_fetch_document_has_a_total_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.adapters.json_feed as json_feed_module

    monkeypatch.setattr(json_feed_module, "_TOTAL_FETCH_TIMEOUT", 0.001, raising=False)
    adapter = JsonFeedAdapter()
    ref = (await _listed_ref(adapter))[0]
    response = _SlowStreamingResponse(
        status_code=200,
        headers={},
        chunks=[b'{"description":"This response never arrives in time."}'],
    )

    with (
        patch(
            "app.adapters.json_feed.validate_json_feed_url_strict",
            new=AsyncMock(return_value=SimpleNamespace(hostname="data.example.com", preferred_ip="203.0.113.10")),
        ),
        patch("app.adapters.json_feed.httpx.AsyncClient", return_value=_StreamingClient(response)),
        pytest.raises(RuntimeError, match="deadline"),
    ):
        await adapter.fetch_document(ref, _connector())


async def test_fetch_document_network_error_does_not_leak_query_token() -> None:
    adapter = JsonFeedAdapter()
    ref = (await _listed_ref(adapter))[0]
    error = httpx.ConnectError(
        "failed",
        request=httpx.Request("GET", "https://data.example.com/feed.json?token=secret"),
    )

    with (
        patch(
            "app.adapters.json_feed.validate_json_feed_url_strict",
            new=AsyncMock(return_value=SimpleNamespace(hostname="data.example.com", preferred_ip="203.0.113.10")),
        ),
        patch("app.adapters.json_feed.httpx.AsyncClient", return_value=_FailingStreamingClient(error)),
        pytest.raises(RuntimeError) as exc_info,
    ):
        await adapter.fetch_document(ref, _connector())

    assert "secret" not in str(exc_info.value)
    assert "data.example.com" not in str(exc_info.value)


async def test_fetch_document_revalidates_before_constructing_http_client() -> None:
    adapter = JsonFeedAdapter()
    validated = SimpleNamespace(hostname="data.example.com", preferred_ip="203.0.113.10")
    rejected = PersistedUrlRejectedError(
        error_code="ssrf_blocked_persisted_json_feed_url",
        hostname="data.example.com",
        message="blocked",
    )
    validator = AsyncMock(side_effect=[validated, rejected])
    client_factory = MagicMock()

    with patch("app.adapters.json_feed.validate_json_feed_url_strict", new=validator):
        ref = (await adapter.list_documents(_connector()))[0]
        with (
            patch("app.adapters.json_feed.httpx.AsyncClient", client_factory),
            pytest.raises(PersistedUrlRejectedError),
        ):
            await adapter.fetch_document(ref, _connector())

    assert validator.await_count == 2
    client_factory.assert_not_called()


async def test_cursor_state_omits_last_synced_at_to_force_every_manual_fetch() -> None:
    assert await JsonFeedAdapter().get_cursor_state(_connector()) == {}


async def _listed_ref(adapter: JsonFeedAdapter):
    with patch(
        "app.adapters.json_feed.validate_json_feed_url_strict",
        new=AsyncMock(return_value=SimpleNamespace(hostname="data.example.com", preferred_ip="203.0.113.10")),
    ):
        return await adapter.list_documents(_connector())


class _StreamingResponse:
    def __init__(self, *, status_code: int, headers: dict[str, str], chunks: list[bytes]) -> None:
        self.status_code = status_code
        self.headers = headers
        self._chunks = chunks

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


class _SlowStreamingResponse(_StreamingResponse):
    async def aiter_bytes(self):
        await asyncio.sleep(0.02)
        for chunk in self._chunks:
            yield chunk


class _StreamContext:
    def __init__(self, response: _StreamingResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _StreamingResponse:
        return self._response

    async def __aexit__(self, *_args: object) -> None:
        return None


class _StreamingClient:
    def __init__(self, response: _StreamingResponse) -> None:
        self._response = response
        self.stream = MagicMock(return_value=_StreamContext(response))

    async def __aenter__(self) -> _StreamingClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FailingStreamContext:
    def __init__(self, error: httpx.HTTPError) -> None:
        self._error = error

    async def __aenter__(self) -> _StreamingResponse:
        raise self._error

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FailingStreamingClient:
    def __init__(self, error: httpx.HTTPError) -> None:
        self._error = error

    def stream(self, *_args: object, **_kwargs: object) -> _FailingStreamContext:
        return _FailingStreamContext(self._error)

    async def __aenter__(self) -> _FailingStreamingClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None
