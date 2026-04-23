"""Specification tests for AirtableAdapter -- SPEC-KB-CONNECTORS-001 Phase 2.

RED phase: these tests define expected behavior before implementation exists.
All tests should FAIL before the adapter is implemented.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.adapters.base import DocumentRef

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_connector(config: dict[str, Any]) -> SimpleNamespace:
    """Build a minimal connector-like object with the given config dict."""
    return SimpleNamespace(
        id="conn-airtable-001",
        org_id="org-001",
        config=config,
    )


def _valid_config(
    *,
    api_key: str = "patABCDEFGH.secret",
    base_id: str = "appABC123",
    table_names: list[str] | None = None,
    view_name: str | None = None,
) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "api_key": api_key,
        "base_id": base_id,
        "table_names": table_names or ["Table1"],
    }
    if view_name is not None:
        cfg["view_name"] = view_name
    return cfg


def _make_record(
    record_id: str = "recABC123",
    fields: dict[str, Any] | None = None,
    created_time: str = "2026-01-01T00:00:00.000Z",
    modified_time: str | None = None,
    created_by: dict[str, Any] | None = None,
    last_modified_by: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a minimal Airtable record dict."""
    record: dict[str, Any] = {
        "id": record_id,
        "fields": fields or {"Name": "Test Record"},
        "createdTime": created_time,
    }
    if modified_time is not None:
        record["_modifiedTime"] = modified_time
    if created_by is not None:
        record["createdBy"] = created_by
    if last_modified_by is not None:
        record["lastModifiedBy"] = last_modified_by
    return record


@pytest.fixture
def airtable_adapter() -> Any:
    """Create an AirtableAdapter with a mock settings object."""
    from app.adapters.airtable import AirtableAdapter
    from app.core.config import Settings

    s = MagicMock(spec=Settings)
    return AirtableAdapter(s)


# ---------------------------------------------------------------------------
# Config extraction
# ---------------------------------------------------------------------------


async def test_extract_config_happy_path(airtable_adapter: Any) -> None:
    """_extract_config returns a dict with all required fields."""
    connector = _make_connector(
        _valid_config(
            api_key="patXYZ.secret",
            base_id="appXYZ123",
            table_names=["Tasks", "Projects"],
            view_name="Active Only",
        )
    )
    cfg = airtable_adapter._extract_config(connector)

    assert cfg["api_key"] == "patXYZ.secret"
    assert cfg["base_id"] == "appXYZ123"
    assert cfg["table_names"] == ["Tasks", "Projects"]
    assert cfg["view_name"] == "Active Only"


async def test_extract_config_missing_api_key_raises(airtable_adapter: Any) -> None:
    """_extract_config raises ValueError when api_key is absent."""
    connector = _make_connector({"base_id": "appABC", "table_names": ["T1"]})
    with pytest.raises(ValueError, match="api_key"):
        airtable_adapter._extract_config(connector)


async def test_extract_config_missing_base_id_raises(airtable_adapter: Any) -> None:
    """_extract_config raises ValueError when base_id is absent."""
    connector = _make_connector({"api_key": "pat123", "table_names": ["T1"]})
    with pytest.raises(ValueError, match="base_id"):
        airtable_adapter._extract_config(connector)


async def test_extract_config_missing_table_names_raises(airtable_adapter: Any) -> None:
    """_extract_config raises ValueError when table_names is absent."""
    connector = _make_connector({"api_key": "pat123", "base_id": "appXYZ"})
    with pytest.raises(ValueError, match="table_names"):
        airtable_adapter._extract_config(connector)


async def test_extract_config_empty_table_names_raises(airtable_adapter: Any) -> None:
    """_extract_config raises ValueError when table_names is an empty list."""
    connector = _make_connector({"api_key": "pat123", "base_id": "appXYZ", "table_names": []})
    with pytest.raises(ValueError, match="table_names"):
        airtable_adapter._extract_config(connector)


async def test_extract_config_with_view_name(airtable_adapter: Any) -> None:
    """_extract_config includes view_name when provided."""
    connector = _make_connector(_valid_config(view_name="Grid View"))
    cfg = airtable_adapter._extract_config(connector)
    assert cfg["view_name"] == "Grid View"


async def test_extract_config_without_view_name(airtable_adapter: Any) -> None:
    """_extract_config defaults view_name to None when not provided."""
    connector = _make_connector(_valid_config())
    cfg = airtable_adapter._extract_config(connector)
    assert cfg["view_name"] is None


# ---------------------------------------------------------------------------
# list_documents
# ---------------------------------------------------------------------------


async def test_list_documents_single_table_happy_path(airtable_adapter: Any) -> None:
    """list_documents with one table and two records yields two DocumentRefs."""
    connector = _make_connector(_valid_config(base_id="appABC123", table_names=["Table1"]))
    records = [
        _make_record("recAAA", {"Name": "Row A"}, created_time="2026-01-01T00:00:00.000Z"),
        _make_record("recBBB", {"Name": "Row B"}, created_time="2026-02-01T00:00:00.000Z"),
    ]

    mock_table = MagicMock()
    mock_table.iterate.return_value = [records]  # iterate yields pages of records

    mock_api_instance = MagicMock()
    mock_api_instance.table.return_value = mock_table

    with patch("app.adapters.airtable.Api") as mock_api_cls:
        mock_api_cls.return_value = mock_api_instance
        refs = await airtable_adapter.list_documents(connector)

    assert len(refs) == 2
    assert all(isinstance(r, DocumentRef) for r in refs)

    ref_a = refs[0]
    assert ref_a.content_type == "text/plain"
    assert "appABC123" in ref_a.source_url
    assert ref_a.source_url.startswith("https://airtable.com/")
    assert "recAAA" in ref_a.source_url or "recAAA" in ref_a.source_ref


async def test_list_documents_multiple_tables(airtable_adapter: Any) -> None:
    """list_documents iterates both tables when two table_names are configured."""
    connector = _make_connector(_valid_config(table_names=["Table1", "Table2"]))

    rec1 = _make_record("rec001", {"Name": "A"})
    rec2 = _make_record("rec002", {"Name": "B"})

    mock_table1 = MagicMock()
    mock_table1.iterate.return_value = [[rec1]]
    mock_table2 = MagicMock()
    mock_table2.iterate.return_value = [[rec2]]

    mock_api_instance = MagicMock()
    mock_api_instance.table.side_effect = [mock_table1, mock_table2]

    with patch("app.adapters.airtable.Api") as mock_api_cls:
        mock_api_cls.return_value = mock_api_instance
        refs = await airtable_adapter.list_documents(connector)

    assert len(refs) == 2
    # Both tables were queried
    assert mock_api_instance.table.call_count == 2


async def test_list_documents_with_view(airtable_adapter: Any) -> None:
    """list_documents passes view_name to table.iterate when configured."""
    connector = _make_connector(_valid_config(view_name="Active View"))
    record = _make_record("rec001", {"Name": "Row"})

    mock_table = MagicMock()
    mock_table.iterate.return_value = [[record]]

    mock_api_instance = MagicMock()
    mock_api_instance.table.return_value = mock_table

    with patch("app.adapters.airtable.Api") as mock_api_cls:
        mock_api_cls.return_value = mock_api_instance
        await airtable_adapter.list_documents(connector)

    # Verify view was passed
    call_kwargs = mock_table.iterate.call_args
    assert call_kwargs is not None
    # view should be passed as keyword arg
    assert call_kwargs.kwargs.get("view") == "Active View" or (
        len(call_kwargs.args) >= 2 and call_kwargs.args[1] == "Active View"
    )


async def test_list_documents_last_edited_prefers_modified_time(airtable_adapter: Any) -> None:
    """DocumentRef.last_edited uses _modifiedTime when present in record."""
    connector = _make_connector(_valid_config())
    record = _make_record(
        "rec001",
        created_time="2026-01-01T00:00:00.000Z",
        modified_time="2026-03-15T12:00:00.000Z",
    )

    mock_table = MagicMock()
    mock_table.iterate.return_value = [[record]]

    mock_api_instance = MagicMock()
    mock_api_instance.table.return_value = mock_table

    with patch("app.adapters.airtable.Api") as mock_api_cls:
        mock_api_cls.return_value = mock_api_instance
        refs = await airtable_adapter.list_documents(connector)

    assert refs[0].last_edited == "2026-03-15T12:00:00.000Z"


async def test_list_documents_last_edited_falls_back_to_created_time(airtable_adapter: Any) -> None:
    """DocumentRef.last_edited falls back to createdTime when _modifiedTime absent."""
    connector = _make_connector(_valid_config())
    record = _make_record("rec001", created_time="2026-02-10T08:00:00.000Z")

    mock_table = MagicMock()
    mock_table.iterate.return_value = [[record]]

    mock_api_instance = MagicMock()
    mock_api_instance.table.return_value = mock_table

    with patch("app.adapters.airtable.Api") as mock_api_cls:
        mock_api_cls.return_value = mock_api_instance
        refs = await airtable_adapter.list_documents(connector)

    assert refs[0].last_edited == "2026-02-10T08:00:00.000Z"


async def test_list_documents_sender_email_from_created_by(airtable_adapter: Any) -> None:
    """DocumentRef.sender_email is populated from createdBy.email."""
    connector = _make_connector(_valid_config())
    record = _make_record(
        "rec001",
        created_by={"id": "usrABC", "email": "creator@example.com", "name": "Creator"},
    )

    mock_table = MagicMock()
    mock_table.iterate.return_value = [[record]]

    mock_api_instance = MagicMock()
    mock_api_instance.table.return_value = mock_table

    with patch("app.adapters.airtable.Api") as mock_api_cls:
        mock_api_cls.return_value = mock_api_instance
        refs = await airtable_adapter.list_documents(connector)

    assert refs[0].sender_email == "creator@example.com"


async def test_list_documents_sender_email_empty_when_absent(airtable_adapter: Any) -> None:
    """DocumentRef.sender_email is empty string when createdBy is absent."""
    connector = _make_connector(_valid_config())
    record = _make_record("rec001")

    mock_table = MagicMock()
    mock_table.iterate.return_value = [[record]]

    mock_api_instance = MagicMock()
    mock_api_instance.table.return_value = mock_table

    with patch("app.adapters.airtable.Api") as mock_api_cls:
        mock_api_cls.return_value = mock_api_instance
        refs = await airtable_adapter.list_documents(connector)

    assert refs[0].sender_email == ""


async def test_list_documents_mentioned_emails_from_collaborator_field(airtable_adapter: Any) -> None:
    """DocumentRef.mentioned_emails includes emails from collaborator-type field values."""
    connector = _make_connector(_valid_config())
    record = _make_record(
        "rec001",
        fields={
            "Name": "Test",
            "Assigned To": {"id": "usrXYZ", "email": "collab@example.com", "name": "Collab"},
        },
    )

    mock_table = MagicMock()
    mock_table.iterate.return_value = [[record]]

    mock_api_instance = MagicMock()
    mock_api_instance.table.return_value = mock_table

    with patch("app.adapters.airtable.Api") as mock_api_cls:
        mock_api_cls.return_value = mock_api_instance
        refs = await airtable_adapter.list_documents(connector)

    assert "collab@example.com" in refs[0].mentioned_emails


async def test_list_documents_mentioned_emails_dedupes(airtable_adapter: Any) -> None:
    """Duplicate emails across multiple collaborator fields appear only once."""
    connector = _make_connector(_valid_config())
    email = "dupe@example.com"
    record = _make_record(
        "rec001",
        fields={
            "Reviewer 1": {"id": "usr1", "email": email, "name": "Reviewer"},
            "Reviewer 2": {"id": "usr2", "email": email, "name": "Reviewer Again"},
        },
    )

    mock_table = MagicMock()
    mock_table.iterate.return_value = [[record]]

    mock_api_instance = MagicMock()
    mock_api_instance.table.return_value = mock_table

    with patch("app.adapters.airtable.Api") as mock_api_cls:
        mock_api_cls.return_value = mock_api_instance
        refs = await airtable_adapter.list_documents(connector)

    assert refs[0].mentioned_emails.count(email) == 1


async def test_list_documents_mentioned_emails_excludes_sender(airtable_adapter: Any) -> None:
    """sender_email is NOT duplicated in mentioned_emails."""
    connector = _make_connector(_valid_config())
    creator_email = "creator@example.com"
    record = _make_record(
        "rec001",
        fields={
            "Collab": {"id": "usrC", "email": creator_email, "name": "Creator as Collab"},
        },
        created_by={"id": "usrC", "email": creator_email, "name": "Creator"},
    )

    mock_table = MagicMock()
    mock_table.iterate.return_value = [[record]]

    mock_api_instance = MagicMock()
    mock_api_instance.table.return_value = mock_table

    with patch("app.adapters.airtable.Api") as mock_api_cls:
        mock_api_cls.return_value = mock_api_instance
        refs = await airtable_adapter.list_documents(connector)

    assert creator_email not in refs[0].mentioned_emails


# ---------------------------------------------------------------------------
# fetch_document
# ---------------------------------------------------------------------------


async def test_fetch_document_flattens_alphabetically(airtable_adapter: Any) -> None:
    """fetch_document returns fields sorted alphabetically by key."""
    connector = _make_connector(_valid_config())
    ref = DocumentRef(
        path="Table1/recAAA",
        ref="recAAA",
        size=0,
        content_type="text/plain",
    )

    # Fields in non-alphabetical order
    record = {"id": "recAAA", "fields": {"Zebra": "last", "Apple": "first", "Mango": "middle"}}

    mock_table = MagicMock()
    mock_table.get.return_value = record

    mock_api_instance = MagicMock()
    mock_api_instance.table.return_value = mock_table

    with patch("app.adapters.airtable.Api") as mock_api_cls:
        mock_api_cls.return_value = mock_api_instance
        content = await airtable_adapter.fetch_document(ref, connector)

    text = content.decode("utf-8")
    apple_pos = text.index("Apple:")
    mango_pos = text.index("Mango:")
    zebra_pos = text.index("Zebra:")
    assert apple_pos < mango_pos < zebra_pos


async def test_fetch_document_handles_list_values(airtable_adapter: Any) -> None:
    """fetch_document comma-joins list field values."""
    connector = _make_connector(_valid_config())
    ref = DocumentRef(path="Table1/recAAA", ref="recAAA", size=0, content_type="text/plain")

    record = {"id": "recAAA", "fields": {"Tags": ["python", "airtable", "kb"]}}

    mock_table = MagicMock()
    mock_table.get.return_value = record

    mock_api_instance = MagicMock()
    mock_api_instance.table.return_value = mock_table

    with patch("app.adapters.airtable.Api") as mock_api_cls:
        mock_api_cls.return_value = mock_api_instance
        content = await airtable_adapter.fetch_document(ref, connector)

    text = content.decode("utf-8")
    assert "Tags: python, airtable, kb" in text


async def test_fetch_document_handles_dict_values(airtable_adapter: Any) -> None:
    """fetch_document uses str() representation for dict field values."""
    connector = _make_connector(_valid_config())
    ref = DocumentRef(path="Table1/recAAA", ref="recAAA", size=0, content_type="text/plain")

    nested = {"id": "usrXYZ", "email": "person@example.com"}
    record = {"id": "recAAA", "fields": {"Owner": nested}}

    mock_table = MagicMock()
    mock_table.get.return_value = record

    mock_api_instance = MagicMock()
    mock_api_instance.table.return_value = mock_table

    with patch("app.adapters.airtable.Api") as mock_api_cls:
        mock_api_cls.return_value = mock_api_instance
        content = await airtable_adapter.fetch_document(ref, connector)

    text = content.decode("utf-8")
    assert "Owner:" in text
    assert str(nested) in text


async def test_fetch_document_returns_bytes(airtable_adapter: Any) -> None:
    """fetch_document always returns bytes."""
    connector = _make_connector(_valid_config())
    ref = DocumentRef(path="Table1/recAAA", ref="recAAA", size=0, content_type="text/plain")

    record = {"id": "recAAA", "fields": {"Name": "Test"}}

    mock_table = MagicMock()
    mock_table.get.return_value = record

    mock_api_instance = MagicMock()
    mock_api_instance.table.return_value = mock_table

    with patch("app.adapters.airtable.Api") as mock_api_cls:
        mock_api_cls.return_value = mock_api_instance
        content = await airtable_adapter.fetch_document(ref, connector)

    assert isinstance(content, bytes)


# ---------------------------------------------------------------------------
# get_cursor_state
# ---------------------------------------------------------------------------


async def test_get_cursor_state_returns_iso_timestamp(airtable_adapter: Any) -> None:
    """get_cursor_state returns a dict with last_run_at as an ISO 8601 string."""
    connector = _make_connector(_valid_config())
    state = await airtable_adapter.get_cursor_state(connector)

    assert isinstance(state, dict)
    assert "last_run_at" in state
    # Verify it's a parseable ISO timestamp
    from datetime import datetime
    from typing import cast

    dt = datetime.fromisoformat(cast(str, state["last_run_at"]))
    assert dt is not None


# ---------------------------------------------------------------------------
# aclose
# ---------------------------------------------------------------------------


async def test_aclose_noop(airtable_adapter: Any) -> None:
    """aclose() completes without raising."""
    await airtable_adapter.aclose()
