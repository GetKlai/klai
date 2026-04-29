"""SPEC-SEC-INTERNAL-001 REQ-1.1 / AC-1: ``taxonomy._require_internal_token`` uses ``verify_shared_secret``.

Pinned invariants:
- 503 when ``settings.internal_secret`` is empty (REQ-1.4 short-circuit before any compare).
- 401 when the Authorization header does not match.
- ``None`` (no raise) when the header matches.
- Source uses ``log_utils.verify_shared_secret`` -- mechanical guard against a regression
  to ``token != f"Bearer {secret}"`` string equality (which leaks length/prefix timing).
"""

from __future__ import annotations

import inspect
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException


@contextmanager
def _internal_secret(value: str):
    """Patch the taxonomy module's ``settings.internal_secret`` for a test."""
    from app.api import taxonomy as tax_mod

    original = tax_mod.settings.internal_secret
    tax_mod.settings.internal_secret = value
    try:
        yield
    finally:
        tax_mod.settings.internal_secret = original


def _request(authorization: str | None) -> MagicMock:
    request = MagicMock()
    request.headers = MagicMock()
    request.headers.get = lambda key, default="": (
        authorization if authorization is not None and key.lower() == "authorization" else default
    )
    return request


def test_correct_token_returns_none():
    """AC-1: matching Bearer header passes through without raising."""
    from app.api import taxonomy as tax_mod

    with _internal_secret("test-secret-12345"):
        # Should not raise.
        tax_mod._require_internal_token(_request("Bearer test-secret-12345"))


def test_incorrect_token_raises_401():
    """AC-1: mismatched Bearer header raises HTTP 401."""
    from app.api import taxonomy as tax_mod

    with _internal_secret("test-secret-12345"):
        with pytest.raises(HTTPException) as exc:
            tax_mod._require_internal_token(_request("Bearer wrong-token-99999"))
        assert exc.value.status_code == 401


def test_missing_header_raises_401():
    """AC-1: absent Authorization header raises HTTP 401, not 503."""
    from app.api import taxonomy as tax_mod

    with _internal_secret("test-secret-12345"):
        with pytest.raises(HTTPException) as exc:
            tax_mod._require_internal_token(_request(None))
        assert exc.value.status_code == 401


def test_empty_secret_short_circuits_to_503():
    """AC-1 / REQ-1.4: empty configured secret returns 503 BEFORE any compare runs."""
    from app.api import taxonomy as tax_mod

    with _internal_secret(""):
        with pytest.raises(HTTPException) as exc:
            tax_mod._require_internal_token(_request("Bearer anything-12345"))
        assert exc.value.status_code == 503


def test_source_uses_verify_shared_secret_not_string_equality():
    """REQ-1.1 mechanical guard: source imports ``verify_shared_secret`` and does not use ``!=`` / ``==`` on the token."""
    from app.api import taxonomy as tax_mod

    src = inspect.getsource(tax_mod._require_internal_token)
    assert "verify_shared_secret" in src, "taxonomy._require_internal_token must use verify_shared_secret"
    # Defensive: nobody introduced raw string equality back in.
    assert 'token != f"Bearer' not in src, "regressed to string-inequality on Bearer token"
    assert 'token == f"Bearer' not in src, "regressed to string-equality on Bearer token"
