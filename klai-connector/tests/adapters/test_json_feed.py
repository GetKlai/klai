"""Contract tests for the structure-aware JSON feed adapter."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from klai_image_storage import PinnedResolverTransport

from app.adapters.json_feed import JsonFeedAdapter
from app.clients.knowledge_ingest import _build_payload
from app.services.url_guard import PersistedUrlRejectedError


def _connector(
    url: str = "https://data.example.com/feed.json?token=secret",
    **config: object,
) -> SimpleNamespace:
    return SimpleNamespace(id="connector-123", config={"url": url, **config})


def _voys_records() -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for category in ("Telefoonnummers", "Gesprekken", "Abonnementen"):
        for entity in ("nl", "be"):
            records.append(
                {
                    "category": category,
                    "entity": entity,
                    "brand": "voys",
                    "name": f"{category} {entity}",
                    "monthly": 9.25000,
                    "per_minute": None,
                }
            )
    records.insert(
        0,
        {
            "category": "Telefoonnummers",
            "entity": "nl",
            "brand": "voys",
            "name": "Nummer Portugal",
            "monthly": 9.25000,
            "per_minute": None,
        },
    )
    records.insert(
        0,
        {
            "category": "Telefoonnummers",
            "entity": "nl",
            "brand": "voys",
            "name": "Nummer Italie",
            "monthly": 9.25000,
            "per_minute": None,
        },
    )
    records.insert(1, dict(records[0]))
    return records


async def _list_payload(
    adapter: JsonFeedAdapter,
    payload: object,
    connector: SimpleNamespace | None = None,
) -> tuple[list, MagicMock, AsyncMock]:
    response = _StreamingResponse(status_code=200, headers={}, chunks=[json.dumps(payload).encode()])
    client_factory = MagicMock(return_value=_StreamingClient(response))
    validator = AsyncMock(return_value=SimpleNamespace(hostname="data.example.com", preferred_ip="203.0.113.10"))
    with (
        patch("app.adapters.json_feed.validate_json_feed_url_strict", new=validator),
        patch("app.adapters.json_feed.httpx.AsyncClient", client_factory),
    ):
        refs = await adapter.list_documents(connector or _connector())
    return refs, client_factory, validator


async def test_ac1_flat_records_are_grouped_verbalized_sorted_and_deduplicated() -> None:
    adapter = JsonFeedAdapter()
    connector = _connector(
        group_by=["category", "entity", "brand"],
        title="Prijzen",
        field_labels={"monthly": "per maand (EUR)"},
    )
    refs, _, _ = await _list_payload(adapter, _voys_records(), connector)

    assert len(refs) == 6
    ref = next(item for item in refs if item.path.endswith("/telefoonnummers-nl-voys"))
    assert ref.content_type == "kb_article"
    assert ref.source_ref == "json-feed:connector-123:telefoonnummers-nl-voys"
    assert ref.source_url == "https://data.example.com"
    assert ref.extra == {
        "json_feed_group": {"category": "Telefoonnummers", "entity": "nl", "brand": "voys"},
        "json_feed_record_count": 3,
    }
    content = (await adapter.fetch_document(ref, connector)).decode()
    assert content.startswith("# Prijzen — Telefoonnummers — voys (nl)\n\nVelden: ")
    assert content.count("**Nummer Italie**") == 1
    assert content.index("**Nummer Italie**") < content.index("**Nummer Portugal**")
    assert "per maand (EUR): 9.25" in content
    assert "per minute:" not in content
    assert "secret" not in ref.source_url
    assert adapter.get_sync_metrics(connector) == {
        "groups_total": 6,
        "records_total": 9,
        "duplicates_collapsed": 1,
    }


async def test_ac2_without_group_by_batches_in_feed_order() -> None:
    adapter = JsonFeedAdapter()
    connector = _connector(max_records_per_doc=2)
    records = [{"name": name, "price": index} for index, name in enumerate(("Zulu", "Alpha", "Mike", "Bravo", "Echo"))]
    refs, _, _ = await _list_payload(adapter, records, connector)

    assert [ref.path for ref in refs] == [
        "json-feed/connector-123/part-0001",
        "json-feed/connector-123/part-0002",
        "json-feed/connector-123/part-0003",
    ]
    assert [ref.extra["json_feed_record_count"] for ref in refs] == [2, 2, 1]
    first = (await adapter.fetch_document(refs[0], connector)).decode()
    assert first.index("**Zulu**") < first.index("**Alpha**")


async def test_ac3_documents_preserve_chunker_soft_boundaries() -> None:
    adapter = JsonFeedAdapter()
    connector = _connector(group_by=["category"])
    records = [{"category": "prijzen", "name": f"Product {index:02d}", "description": "x" * 300} for index in range(6)]
    refs, _, _ = await _list_payload(adapter, records, connector)

    for ref in refs:
        content = (await adapter.fetch_document(ref, connector)).decode()
        assert content.startswith("# ")
        record_lines = [line for line in content.splitlines() if line.startswith("- **")]
        assert record_lines
        assert all(len(line) <= 900 for line in record_lines)
        assert all(f"{line}\n\n" in f"{content}\n\n" for line in record_lines)


async def test_ac5_oversized_group_splits_at_record_boundaries_below_cap() -> None:
    adapter = JsonFeedAdapter()
    connector = _connector(group_by=["category"], max_doc_chars=700)
    records = [{"category": "prijzen", "name": f"Product {index:02d}", "description": "x" * 240} for index in range(8)]
    refs, _, _ = await _list_payload(adapter, records, connector)

    assert len(refs) > 1
    assert [ref.path for ref in refs] == [
        f"json-feed/connector-123/prijzen--{index}" for index in range(1, len(refs) + 1)
    ]
    seen_labels: list[str] = []
    for index, ref in enumerate(refs, start=1):
        content = (await adapter.fetch_document(ref, connector)).decode()
        assert len(content) <= 700
        assert f"— deel {index}/{len(refs)}" in content.splitlines()[0]
        assert len(content) < 200 * 2_000
        seen_labels.extend(line for line in content.splitlines() if line.startswith("- **"))
    assert len(seen_labels) == len(records)


async def test_ac7_nested_json_uses_headings_and_path_lines() -> None:
    adapter = JsonFeedAdapter()
    payload = {
        "catalogus": {
            "telefoon": {"monthly": 9.25, "countries": ["NL", "BE"]},
            "support": "inbegrepen",
        },
        "updated_at": "2026-08-25",
    }
    refs, _, _ = await _list_payload(adapter, payload, _connector(title="Prijzen"))

    assert [ref.path for ref in refs] == ["json-feed/connector-123/document"]
    content = (await adapter.fetch_document(refs[0], _connector())).decode()
    assert content.startswith("# Prijzen")
    assert "## catalogus" in content
    assert "- catalogus.telefoon.monthly: 9.25" in content
    assert '- catalogus.telefoon.countries: ["NL","BE"]' in content
    assert not any(line.startswith("{") for line in content.splitlines())


async def test_ac8_extra_reaches_ingest_and_enrichment_payload_contract() -> None:
    adapter = JsonFeedAdapter()
    connector = _connector(group_by=["category"])
    refs, _, _ = await _list_payload(
        adapter,
        [{"category": "Telefoonnummers", "name": "Nummer Italie", "monthly": 9.25}],
        connector,
    )
    ref = refs[0]

    payload = _build_payload(
        org_id="org-1",
        kb_slug="prices",
        path=ref.path,
        content=(await adapter.fetch_document(ref, connector)).decode(),
        source_connector_id="connector-123",
        source_ref=ref.source_ref,
        content_type=ref.content_type,
        document_extra=ref.extra,
    )

    assert payload["extra"]["json_feed_group"] == {"category": "Telefoonnummers"}
    assert payload["extra"]["json_feed_record_count"] == 1
    ingest_route = Path(__file__).parents[3] / "klai-knowledge-ingest" / "knowledge_ingest" / "routes" / "ingest.py"
    assert "extra_payload.update(req.extra)" in ingest_route.read_text()


async def test_ac9_record_text_contains_price_and_group_context() -> None:
    adapter = JsonFeedAdapter()
    connector = _connector(group_by=["category", "entity"])
    refs, _, _ = await _list_payload(
        adapter,
        [
            {
                "category": "Telefoonnummers",
                "entity": "nl",
                "name": "Nummer Italie",
                "monthly": 9.25,
            }
        ],
        connector,
    )
    content = (await adapter.fetch_document(refs[0], connector)).decode()

    assert "Telefoonnummers — nl" in content
    assert "Nummer Italie" in content
    assert "monthly: 9.25" in content


async def test_list_documents_fetches_once_and_cache_lives_until_post_sync() -> None:
    adapter = JsonFeedAdapter()
    connector = _connector()
    refs, client_factory, validator = await _list_payload(
        adapter,
        [{"name": "Support", "description": "A sufficiently detailed service description"}],
        connector,
    )

    first = await adapter.fetch_document(refs[0], connector)
    second = await adapter.fetch_document(refs[0], connector)
    assert first == second
    assert client_factory.call_count == 1
    assert validator.await_count == 1

    await adapter.post_sync(connector)
    assert adapter.get_sync_metrics(connector) == {}
    with pytest.raises(RuntimeError, match="not cached"):
        await adapter.fetch_document(refs[0], connector)


async def test_group_by_field_missing_from_more_than_half_fails_explicitly() -> None:
    adapter = JsonFeedAdapter()
    with pytest.raises(ValueError, match=r"group_by field 'category'.*absent.*>50%"):
        await _list_payload(
            adapter,
            [{"name": "A"}, {"name": "B"}, {"category": "prijzen", "name": "C"}],
            _connector(group_by=["category"]),
        )


async def test_oversized_record_fails_with_group_and_record_index() -> None:
    adapter = JsonFeedAdapter()
    connector = _connector(group_by=["category"])
    refs, _, _ = await _list_payload(
        adapter,
        [{"category": "prijzen", "name": "A", "description": "x" * 900}],
        connector,
    )

    with pytest.raises(ValueError, match=r"group 'prijzen' record index 0"):
        await adapter.fetch_document(refs[0], connector)


async def test_list_documents_requires_a_url() -> None:
    adapter = JsonFeedAdapter()
    with pytest.raises(ValueError, match="missing required field 'url'"):
        await adapter.list_documents(SimpleNamespace(id="connector-123", config={}))


async def test_list_documents_reports_missing_url_when_config_is_null() -> None:
    adapter = JsonFeedAdapter()
    with pytest.raises(ValueError, match="missing required field 'url'"):
        await adapter.list_documents(SimpleNamespace(id="connector-123", config=None))


@pytest.mark.parametrize(
    ("url", "expected_source_url"),
    [
        ("https://user:password@data.example.com/private/feed.json?token=secret", "https://data.example.com"),
        ("https://data.example.com/private/path-token/feed.json", "https://data.example.com"),
        ("https://user:password@[2001:db8::1]:8443/private/feed.json", "https://[2001:db8::1]:8443"),
        ("https://data.example.com:8443/private/feed.json", "https://data.example.com:8443"),
    ],
)
async def test_list_documents_exposes_only_credential_stripped_origin(
    url: str,
    expected_source_url: str,
) -> None:
    adapter = JsonFeedAdapter()
    refs, _, _ = await _list_payload(
        adapter,
        [{"name": "Support", "description": "A sufficiently detailed service description"}],
        _connector(url),
    )
    assert refs[0].source_url == expected_source_url
    assert "password" not in refs[0].source_url
    assert "secret" not in refs[0].source_url
    assert "path-token" not in refs[0].source_url


async def test_list_documents_normalizes_utf16_json_to_utf8() -> None:
    adapter = JsonFeedAdapter()
    payload = {"description": "Uitgebreide prijsinformatie", "price": 42}
    response = _StreamingResponse(
        status_code=200,
        headers={"content-type": "application/json"},
        chunks=[json.dumps(payload).encode("utf-16")],
    )
    with (
        patch(
            "app.adapters.json_feed.validate_json_feed_url_strict",
            new=AsyncMock(return_value=SimpleNamespace(hostname="data.example.com", preferred_ip="203.0.113.10")),
        ),
        patch("app.adapters.json_feed.httpx.AsyncClient", return_value=_StreamingClient(response)),
    ):
        refs = await adapter.list_documents(_connector())

    assert "Uitgebreide prijsinformatie" in (await adapter.fetch_document(refs[0], _connector())).decode()


@pytest.mark.parametrize("payload", [b"[]", b"{}", b"null", b"42"])
async def test_list_documents_rejects_json_with_too_little_knowledge(payload: bytes) -> None:
    adapter = JsonFeedAdapter()
    response = _StreamingResponse(status_code=200, headers={}, chunks=[payload])
    with (
        patch(
            "app.adapters.json_feed.validate_json_feed_url_strict",
            new=AsyncMock(return_value=SimpleNamespace(hostname="data.example.com", preferred_ip="203.0.113.10")),
        ),
        patch("app.adapters.json_feed.httpx.AsyncClient", return_value=_StreamingClient(response)),
        pytest.raises(ValueError, match="too little knowledge"),
    ):
        await adapter.list_documents(_connector())


async def test_list_documents_rejects_invalid_json() -> None:
    adapter = JsonFeedAdapter()
    response = _StreamingResponse(status_code=200, headers={}, chunks=[b"<html />"])
    with (
        patch(
            "app.adapters.json_feed.validate_json_feed_url_strict",
            new=AsyncMock(return_value=SimpleNamespace(hostname="data.example.com", preferred_ip="203.0.113.10")),
        ),
        patch("app.adapters.json_feed.httpx.AsyncClient", return_value=_StreamingClient(response)),
        pytest.raises(ValueError, match="invalid JSON"),
    ):
        await adapter.list_documents(_connector())


async def test_list_documents_rejects_redirects_without_leaking_url() -> None:
    adapter = JsonFeedAdapter()
    response = _StreamingResponse(status_code=302, headers={"location": "https://elsewhere.example/"}, chunks=[])
    with (
        patch(
            "app.adapters.json_feed.validate_json_feed_url_strict",
            new=AsyncMock(return_value=SimpleNamespace(hostname="data.example.com", preferred_ip="203.0.113.10")),
        ),
        patch("app.adapters.json_feed.httpx.AsyncClient", return_value=_StreamingClient(response)),
        pytest.raises(ValueError) as exc_info,
    ):
        await adapter.list_documents(_connector())
    assert "HTTP 302" in str(exc_info.value)
    assert "secret" not in str(exc_info.value)


async def test_list_documents_uses_pinned_transport_without_redirects() -> None:
    adapter = JsonFeedAdapter()
    _, client_factory, _ = await _list_payload(
        adapter,
        [{"name": "Support", "description": "A sufficiently detailed service description"}],
    )
    kwargs = client_factory.call_args.kwargs
    assert kwargs["follow_redirects"] is False
    assert isinstance(kwargs["transport"], PinnedResolverTransport)
    assert kwargs["transport"]._pinned == {"data.example.com": "203.0.113.10"}


@pytest.mark.parametrize("declared_size", ["11", "invalid"])
async def test_list_documents_enforces_stream_limit_with_untrusted_content_length(
    monkeypatch: pytest.MonkeyPatch,
    declared_size: str,
) -> None:
    import app.adapters.json_feed as json_feed_module

    monkeypatch.setattr(json_feed_module, "_MAX_FEED_SIZE", 10)
    adapter = JsonFeedAdapter()
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
        await adapter.list_documents(_connector())


async def test_list_documents_has_a_total_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.adapters.json_feed as json_feed_module

    monkeypatch.setattr(json_feed_module, "_TOTAL_FETCH_TIMEOUT", 0.001)
    adapter = JsonFeedAdapter()
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
        await adapter.list_documents(_connector())


async def test_list_documents_network_error_does_not_leak_query_token() -> None:
    adapter = JsonFeedAdapter()
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
        await adapter.list_documents(_connector())
    assert "secret" not in str(exc_info.value)
    assert "data.example.com" not in str(exc_info.value)


async def test_list_documents_stops_when_url_revalidation_fails() -> None:
    adapter = JsonFeedAdapter()
    rejected = PersistedUrlRejectedError(
        error_code="ssrf_blocked_persisted_json_feed_url",
        hostname="data.example.com",
        message="blocked",
    )
    client_factory = MagicMock()
    with (
        patch("app.adapters.json_feed.validate_json_feed_url_strict", new=AsyncMock(side_effect=rejected)),
        patch("app.adapters.json_feed.httpx.AsyncClient", client_factory),
        pytest.raises(PersistedUrlRejectedError),
    ):
        await adapter.list_documents(_connector())
    client_factory.assert_not_called()


async def test_cursor_state_omits_last_synced_at_to_force_every_manual_fetch() -> None:
    assert await JsonFeedAdapter().get_cursor_state(_connector()) == {}


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
        self.stream = MagicMock(return_value=_StreamContext(response))

    async def __aenter__(self) -> _StreamingClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


class _RaisingStreamContext:
    def __init__(self, error: httpx.HTTPError) -> None:
        self._error = error

    async def __aenter__(self):
        raise self._error

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FailingStreamingClient:
    def __init__(self, error: httpx.HTTPError) -> None:
        self.stream = MagicMock(return_value=_RaisingStreamContext(error))

    async def __aenter__(self) -> _FailingStreamingClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None
