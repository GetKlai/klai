"""Tests for link-count payload indexes and update_link_counts() (SPEC-CRAWLER-003)."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from knowledge_ingest.qdrant_store import ensure_collection, update_link_counts


def _mock_collection_info(indexed_fields: set[str]):
    """Create a mock collection info with given indexed field names."""
    info = MagicMock()
    info.payload_schema = {f: MagicMock() for f in indexed_fields}
    return info


def _mock_collection_entry(collection_name: str):
    """Create a mock collection entry with .name attribute."""
    entry = MagicMock()
    entry.name = collection_name
    return entry


# ---------------------------------------------------------------------------
# ensure_collection: new payload indexes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ensure_collection_creates_source_url_index_when_missing():
    """source_url keyword index should be created when not yet indexed."""
    existing_fields = {"org_id", "kb_slug", "artifact_id", "content_type", "user_id", "entity_uuids"}

    with patch("knowledge_ingest.qdrant_store.get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_collections = AsyncMock(
            return_value=MagicMock(collections=[_mock_collection_entry("klai_knowledge")])
        )
        mock_client.get_collection = AsyncMock(
            return_value=_mock_collection_info(existing_fields)
        )
        mock_client.create_payload_index = AsyncMock()

        await ensure_collection()

        # Find the call that created the source_url index
        calls = mock_client.create_payload_index.call_args_list
        source_url_calls = [
            c for c in calls
            if c.kwargs.get("field_name") == "source_url"
            or (len(c.args) >= 2 and c.args[1] == "source_url")
        ]
        assert len(source_url_calls) == 1
        call_kwargs = source_url_calls[0].kwargs
        assert call_kwargs.get("field_schema") == "keyword"


@pytest.mark.asyncio
async def test_ensure_collection_creates_incoming_link_count_index_when_missing():
    """incoming_link_count integer index should be created when not yet indexed."""
    existing_fields = {"org_id", "kb_slug", "artifact_id", "content_type", "user_id", "entity_uuids"}

    with patch("knowledge_ingest.qdrant_store.get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_collections = AsyncMock(
            return_value=MagicMock(collections=[_mock_collection_entry("klai_knowledge")])
        )
        mock_client.get_collection = AsyncMock(
            return_value=_mock_collection_info(existing_fields)
        )
        mock_client.create_payload_index = AsyncMock()

        await ensure_collection()

        calls = mock_client.create_payload_index.call_args_list
        ilc_calls = [
            c for c in calls
            if c.kwargs.get("field_name") == "incoming_link_count"
            or (len(c.args) >= 2 and c.args[1] == "incoming_link_count")
        ]
        assert len(ilc_calls) == 1
        call_kwargs = ilc_calls[0].kwargs
        assert call_kwargs.get("field_schema") == "integer"


@pytest.mark.asyncio
async def test_ensure_collection_skips_indexes_when_already_present():
    """When source_url and incoming_link_count are already indexed, skip creation."""
    all_fields = {
        "org_id", "kb_slug", "artifact_id", "content_type", "user_id", "entity_uuids",
        "source_url", "incoming_link_count", "taxonomy_node_id", "source_connector_id",
        "taxonomy_node_ids", "tags", "content_label", "source_label", "chunk_type",
    }

    with patch("knowledge_ingest.qdrant_store.get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.get_collections = AsyncMock(
            return_value=MagicMock(collections=[_mock_collection_entry("klai_knowledge")])
        )
        mock_client.get_collection = AsyncMock(
            return_value=_mock_collection_info(all_fields)
        )
        mock_client.create_payload_index = AsyncMock()

        await ensure_collection()

        # No indexes should be created since all fields are already indexed
        mock_client.create_payload_index.assert_not_called()


# ---------------------------------------------------------------------------
# update_link_counts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_link_counts_calls_set_payload_per_url():
    """set_payload should be called once for each URL in the dict."""
    url_to_count = {
        "https://example.com/page-a": 5,
        "https://example.com/page-b": 3,
        "https://example.com/page-c": 1,
    }

    with patch("knowledge_ingest.qdrant_store.get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.set_payload = AsyncMock()

        await update_link_counts("org1", "my-kb", url_to_count)

        assert mock_client.set_payload.call_count == 3


@pytest.mark.asyncio
async def test_update_link_counts_uses_correct_filter():
    """set_payload filter should include org_id, kb_slug, and source_url."""
    url_to_count = {"https://example.com/page-a": 7}

    with patch("knowledge_ingest.qdrant_store.get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.set_payload = AsyncMock()

        await update_link_counts("org1", "my-kb", url_to_count)

        mock_client.set_payload.assert_called_once()
        call_kwargs = mock_client.set_payload.call_args.kwargs
        # If called positionally, check args instead
        if not call_kwargs:
            call_args = mock_client.set_payload.call_args.args
            # set_payload(collection, payload=..., points=...)
            # The first positional arg is the collection name
            assert call_args[0] == "klai_knowledge"
        else:
            # Verify payload contains incoming_link_count
            payload = call_kwargs.get("payload", mock_client.set_payload.call_args.args[1] if len(mock_client.set_payload.call_args.args) > 1 else None)
            assert payload == {"incoming_link_count": 7}

        # Verify the filter structure by inspecting the points argument
        call_kwargs = mock_client.set_payload.call_args
        # Find the Filter in the call (either kwargs or positional)
        points_filter = call_kwargs.kwargs.get("points") or (
            call_kwargs.args[2] if len(call_kwargs.args) > 2 else None
        )
        assert points_filter is not None
        # The filter should have 3 must conditions: org_id, source_url, kb_slug
        assert len(points_filter.must) == 3
        filter_keys = {cond.key for cond in points_filter.must}
        assert filter_keys == {"source_url", "org_id", "kb_slug"}


@pytest.mark.asyncio
async def test_update_link_counts_empty_dict_does_nothing():
    """When url_to_count is empty, set_payload should not be called."""
    with patch("knowledge_ingest.qdrant_store.get_client") as mock_get_client:
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_client.set_payload = AsyncMock()

        await update_link_counts("org1", "my-kb", {})

        mock_client.set_payload.assert_not_called()
