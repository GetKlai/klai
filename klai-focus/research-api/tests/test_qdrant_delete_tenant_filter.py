"""Regression-guard for SPEC-TI-010C B-7: delete_by_source must include tenant_id filter.

Before the fix, delete_by_source(source_id) deleted vectors across ALL tenants that
shared the same source_id. After the fix, tenant_id is a required parameter and added
as a second FieldCondition in the Qdrant must-list.
"""

from __future__ import annotations

import os

os.environ.setdefault("POSTGRES_DSN", "postgresql+asyncpg://test/test")
os.environ.setdefault("RETRIEVAL_API_URL", "http://retrieval-api:8040")
os.environ.setdefault("RETRIEVAL_API_INTERNAL_SECRET", "test-secret")
os.environ.setdefault("ZITADEL_API_AUDIENCE", "test-audience")

from unittest.mock import MagicMock, patch


def test_delete_by_source_requires_tenant_id():
    """delete_by_source signature must have a tenant_id parameter."""
    import inspect

    from app.services import qdrant_store

    sig = inspect.signature(qdrant_store.delete_by_source)
    assert "tenant_id" in sig.parameters, (
        "SPEC-TI-010C B-7: delete_by_source must require tenant_id to prevent "
        "cross-tenant Qdrant vector deletion"
    )


def test_delete_by_source_includes_tenant_id_in_filter():
    """delete_by_source must include tenant_id FieldCondition in the Qdrant filter."""
    from app.services import qdrant_store

    captured_calls = []

    mock_client = MagicMock()
    mock_client.delete = MagicMock(side_effect=lambda **kwargs: captured_calls.append(kwargs))

    with patch.object(qdrant_store, "get_client", return_value=mock_client):
        qdrant_store.delete_by_source("source-abc", tenant_id="tenant-xyz")

    assert len(captured_calls) == 1, "Expected exactly one delete() call"
    call = captured_calls[0]

    # Extract the filter conditions
    selector = call["points_selector"]
    must_conditions = selector.filter.must

    keys = [c.key for c in must_conditions]
    assert "source_id" in keys, "source_id FieldCondition must be present"
    assert "tenant_id" in keys, (
        "SPEC-TI-010C B-7: tenant_id FieldCondition MISSING — "
        "delete_by_source without tenant scope allows cross-tenant vector wipe"
    )

    # Verify values match what was passed
    values_by_key = {c.key: c.match.value for c in must_conditions}
    assert values_by_key["source_id"] == "source-abc"
    assert values_by_key["tenant_id"] == "tenant-xyz"


def test_delete_by_source_different_tenant_uses_different_filter():
    """Two calls with different tenant_ids must produce distinct filters."""
    from app.services import qdrant_store

    captured_calls = []

    mock_client = MagicMock()
    mock_client.delete = MagicMock(side_effect=lambda **kwargs: captured_calls.append(kwargs))

    with patch.object(qdrant_store, "get_client", return_value=mock_client):
        qdrant_store.delete_by_source("source-shared", tenant_id="tenant-A")
        qdrant_store.delete_by_source("source-shared", tenant_id="tenant-B")

    assert len(captured_calls) == 2

    def _tenant_id_from_call(call):
        for c in call["points_selector"].filter.must:
            if c.key == "tenant_id":
                return c.match.value
        return None

    assert _tenant_id_from_call(captured_calls[0]) == "tenant-A"
    assert _tenant_id_from_call(captured_calls[1]) == "tenant-B"


def test_sources_delete_passes_tenant_id_from_user():
    """The sources.py caller must pass user.tenant_id to delete_by_source.

    This is a static smoke-check: if the call site reverts to the old
    delete_by_source(src_id) form without tenant_id, this test will catch
    it via inspection of the call in the API.
    """
    import inspect

    from app.api import sources

    source = inspect.getsource(sources)
    assert "tenant_id=user.tenant_id" in source, (
        "SPEC-TI-010C B-7: sources.py delete_source must pass tenant_id=user.tenant_id "
        "to delete_by_source. Missing this causes cross-tenant Qdrant vector deletion."
    )
