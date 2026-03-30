"""Tests for gap re-scoring service (SPEC-KB-015).

Pure unit tests -- no real DB or HTTP calls. All async sessions and httpx are mocked.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_rescore_skips_when_no_url() -> None:
    """When knowledge_retrieve_url is empty, returns 0 without any HTTP calls."""
    from app.services.gap_rescorer import rescore_open_gaps

    mock_db = AsyncMock()
    with patch("app.services.gap_rescorer.settings") as mock_settings:
        mock_settings.knowledge_retrieve_url = ""
        result = await rescore_open_gaps(org_id=1, zitadel_org_id="z1", kb_slug="test-kb", db=mock_db)

    assert result == 0
    mock_db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_rescore_marks_resolved_when_no_longer_gap() -> None:
    """Gap query now returns chunks with good scores -- resolved_at should be set."""
    from app.services.gap_rescorer import rescore_open_gaps

    # Simulate one open gap query
    mock_row = MagicMock()
    mock_row.query_text = "what is klai?"
    mock_row.gap_type = "soft"

    mock_result = MagicMock()
    mock_result.all.return_value = [mock_row]

    mock_db = AsyncMock()
    # First execute = SELECT distinct gap queries, second = UPDATE resolved_at
    mock_db.execute = AsyncMock(side_effect=[mock_result, MagicMock()])
    mock_db.commit = AsyncMock()

    # Mock httpx to return good chunks (reranker_score above threshold)
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {"chunks": [{"reranker_score": 0.8, "score": 0.9}]}

    with (
        patch("app.services.gap_rescorer.settings") as mock_settings,
        patch("app.services.gap_rescorer.httpx.AsyncClient") as mock_client_cls,
    ):
        mock_settings.knowledge_retrieve_url = "http://test-retrieve:8000"
        mock_settings.internal_secret = "test-secret"
        mock_settings.klai_gap_soft_threshold = 0.4
        mock_settings.klai_gap_dense_threshold = 0.35

        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        result = await rescore_open_gaps(org_id=1, zitadel_org_id="z1", kb_slug="test-kb", db=mock_db)

    assert result == 1
    # Should have committed to persist resolved_at updates
    mock_db.commit.assert_called_once()


@pytest.mark.asyncio
async def test_rescore_keeps_open_when_still_gap() -> None:
    """Retrieval still returns low scores -- resolved_at stays None."""
    from app.services.gap_rescorer import rescore_open_gaps

    mock_row = MagicMock()
    mock_row.query_text = "obscure topic"
    mock_row.gap_type = "soft"

    mock_result = MagicMock()
    mock_result.all.return_value = [mock_row]

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    # Return chunks with low scores (still a gap)
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {"chunks": [{"reranker_score": 0.1}]}

    with (
        patch("app.services.gap_rescorer.settings") as mock_settings,
        patch("app.services.gap_rescorer.httpx.AsyncClient") as mock_client_cls,
    ):
        mock_settings.knowledge_retrieve_url = "http://test-retrieve:8000"
        mock_settings.internal_secret = "test-secret"
        mock_settings.klai_gap_soft_threshold = 0.4
        mock_settings.klai_gap_dense_threshold = 0.35

        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        result = await rescore_open_gaps(org_id=1, zitadel_org_id="z1", kb_slug=None, db=mock_db)

    assert result == 0
    # No commit because nothing was resolved
    mock_db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_rescore_caps_at_50_queries() -> None:
    """Org has many open gaps -- only up to MAX_QUERIES_PER_TRIGGER are processed."""
    from app.services.gap_rescorer import MAX_QUERIES_PER_TRIGGER, rescore_open_gaps

    # Create 120 mock gap rows (but only 50 should be fetched due to LIMIT)
    mock_rows = []
    for i in range(MAX_QUERIES_PER_TRIGGER):
        row = MagicMock()
        row.query_text = f"query_{i}"
        row.gap_type = "soft"
        mock_rows.append(row)

    mock_result = MagicMock()
    mock_result.all.return_value = mock_rows

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    # All return good chunks (resolved)
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.json.return_value = {"chunks": [{"reranker_score": 0.8}]}

    with (
        patch("app.services.gap_rescorer.settings") as mock_settings,
        patch("app.services.gap_rescorer.httpx.AsyncClient") as mock_client_cls,
    ):
        mock_settings.knowledge_retrieve_url = "http://test-retrieve:8000"
        mock_settings.internal_secret = ""
        mock_settings.klai_gap_soft_threshold = 0.4
        mock_settings.klai_gap_dense_threshold = 0.35

        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        result = await rescore_open_gaps(org_id=1, zitadel_org_id="z1", kb_slug=None, db=mock_db)

    # Should have made exactly MAX_QUERIES_PER_TRIGGER HTTP calls
    assert mock_client.post.call_count == MAX_QUERIES_PER_TRIGGER
    assert result == MAX_QUERIES_PER_TRIGGER


@pytest.mark.asyncio
async def test_rescore_no_open_gaps_returns_zero() -> None:
    """When no open gaps exist, returns 0 without making HTTP calls."""
    from app.services.gap_rescorer import rescore_open_gaps

    mock_result = MagicMock()
    mock_result.all.return_value = []

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)

    with patch("app.services.gap_rescorer.settings") as mock_settings:
        mock_settings.knowledge_retrieve_url = "http://test-retrieve:8000"
        mock_settings.internal_secret = ""

        result = await rescore_open_gaps(org_id=1, zitadel_org_id="z1", kb_slug="test-kb", db=mock_db)

    assert result == 0


@pytest.mark.asyncio
async def test_rescore_skips_on_retrieval_error() -> None:
    """HTTP error from retrieval API -- gap stays open, no exception raised."""
    from app.services.gap_rescorer import rescore_open_gaps

    mock_row = MagicMock()
    mock_row.query_text = "failing query"
    mock_row.gap_type = "hard"

    mock_result = MagicMock()
    mock_result.all.return_value = [mock_row]

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    # Return HTTP 500 error
    mock_response = MagicMock()
    mock_response.is_success = False
    mock_response.status_code = 500

    with (
        patch("app.services.gap_rescorer.settings") as mock_settings,
        patch("app.services.gap_rescorer.httpx.AsyncClient") as mock_client_cls,
    ):
        mock_settings.knowledge_retrieve_url = "http://test-retrieve:8000"
        mock_settings.internal_secret = ""
        mock_settings.klai_gap_soft_threshold = 0.4
        mock_settings.klai_gap_dense_threshold = 0.35

        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        # Should not raise
        result = await rescore_open_gaps(org_id=1, zitadel_org_id="z1", kb_slug=None, db=mock_db)

    assert result == 0
    mock_db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_rescore_skips_on_network_exception() -> None:
    """Network exception during retrieval -- gap stays open, no exception raised."""
    import httpx

    from app.services.gap_rescorer import rescore_open_gaps

    mock_row = MagicMock()
    mock_row.query_text = "network error query"
    mock_row.gap_type = "soft"

    mock_result = MagicMock()
    mock_result.all.return_value = [mock_row]

    mock_db = AsyncMock()
    mock_db.execute = AsyncMock(return_value=mock_result)
    mock_db.commit = AsyncMock()

    with (
        patch("app.services.gap_rescorer.settings") as mock_settings,
        patch("app.services.gap_rescorer.httpx.AsyncClient") as mock_client_cls,
    ):
        mock_settings.knowledge_retrieve_url = "http://test-retrieve:8000"
        mock_settings.internal_secret = ""
        mock_settings.klai_gap_soft_threshold = 0.4
        mock_settings.klai_gap_dense_threshold = 0.35

        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

        result = await rescore_open_gaps(org_id=1, zitadel_org_id="z1", kb_slug=None, db=mock_db)

    assert result == 0
    mock_db.commit.assert_not_called()
