"""Shared pytest fixtures for klai-image-storage tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from klai_image_storage import ImageStore


def _make_store(**overrides: object) -> ImageStore:
    kwargs: dict[str, object] = {
        "endpoint": "garage:3900",
        "access_key": "test-access",
        "secret_key": "test-secret",
        "bucket": "klai-images",
        "region": "garage",
    }
    kwargs.update(overrides)
    return ImageStore(**kwargs)  # type: ignore[arg-type]


@pytest.fixture()
def store() -> ImageStore:
    """Default ImageStore with a MagicMock minio client that accepts uploads."""
    s = _make_store()
    mock_client = MagicMock()
    # By default, stat_object raises S3Error (object missing) so upload proceeds.
    from minio.error import S3Error

    mock_client.stat_object = MagicMock(
        side_effect=S3Error("NoSuchKey", "Not found", "", "", "", "")  # pyright: ignore[reportArgumentType]
    )
    mock_client.put_object = MagicMock()
    s._client = mock_client  # type: ignore[attr-defined]
    return s


@pytest.fixture()
def fresh_store() -> ImageStore:
    """ImageStore without its minio client pre-mocked.

    Use when a test wants to swap in a specific MagicMock (e.g. with
    return_value on stat_object for dedup scenarios).
    """
    return _make_store()
