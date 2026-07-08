"""SPEC-KB-IMAGES-V2-001 REQ-3 / AC-3: boot-time assertion sabotage tests.

These tests verify that ``_assert_kb_image_routes_match_value_class`` in
``app.main`` actually fires (and aborts boot) when the kb-image route
declaration drifts from the ``KbImage`` value-class templates. The
assertion is defense-in-depth on top of the ast-grep CI guard (REQ-5);
testing the assertion itself ensures the guard isn't a no-op.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI

from app.core.kb_image_url import KbImage
from app.main import _assert_kb_image_routes_match_value_class


def _make_app_with_routes(paths: list[str]) -> FastAPI:
    """Build a minimal FastAPI app exposing ``paths`` as no-op handlers
    so the assertion sees them as ``app.routes`` entries."""
    app = FastAPI()
    for p in paths:
        # Each path needs at least one method registered to appear in app.routes
        @app.get(p)
        async def _noop() -> dict:
            return {}

    return app


def test_assertion_passes_on_canonical_routes() -> None:
    """Happy path: when the app declares exactly the two KbImage templates,
    the assertion does not raise."""
    app = _make_app_with_routes([KbImage.ROUTE_TEMPLATE, KbImage.UPLOAD_ROUTE_TEMPLATE])
    _assert_kb_image_routes_match_value_class(app)  # should not raise


def test_assertion_passes_on_real_portal_app_routes() -> None:
    """The production app must expose the canonical routes through FastAPI's
    real include_router representation, not only a minimal test app."""
    from app.main import app

    _assert_kb_image_routes_match_value_class(app)


def test_assertion_aborts_on_4_segment_drift() -> None:
    """The exact v1 regression: route uses {org_id}/{kb_slug}/{filename}
    (4 segments) instead of {zitadel_org_id}/images/{kb_slug}/{filename}
    (5 segments). The assertion MUST refuse to boot."""
    sabotaged = "/kb-images/{org_id}/{kb_slug}/{filename}"
    app = _make_app_with_routes([sabotaged, KbImage.UPLOAD_ROUTE_TEMPLATE])

    with pytest.raises(RuntimeError, match="boot-time check failed"):
        _assert_kb_image_routes_match_value_class(app)


def test_assertion_aborts_on_renamed_segment() -> None:
    """Another realistic drift: someone renames 'images' to 'imgs' in the
    route. URL would still be 5 segments but never match the actual S3
    keys (which use 'images'). The assertion MUST refuse to boot."""
    sabotaged = "/kb-images/{zitadel_org_id}/imgs/{kb_slug}/{filename}"
    app = _make_app_with_routes([sabotaged, KbImage.UPLOAD_ROUTE_TEMPLATE])

    with pytest.raises(RuntimeError, match="boot-time check failed"):
        _assert_kb_image_routes_match_value_class(app)


def test_assertion_aborts_when_upload_route_missing() -> None:
    """Half the contract missing — boot must abort even when the GET route
    is correctly declared."""
    app = _make_app_with_routes([KbImage.ROUTE_TEMPLATE])

    with pytest.raises(RuntimeError, match="boot-time check failed"):
        _assert_kb_image_routes_match_value_class(app)


def test_assertion_aborts_on_extra_kb_image_route() -> None:
    """If someone adds a legacy/parallel route alongside the canonical
    one, that's also a drift — the SPEC explicitly forbids legacy routes
    surviving (REQ-8)."""
    extra = "/kb-images/legacy/{org_id}/{filename}"
    app = _make_app_with_routes([KbImage.ROUTE_TEMPLATE, KbImage.UPLOAD_ROUTE_TEMPLATE, extra])

    with pytest.raises(RuntimeError, match="boot-time check failed"):
        _assert_kb_image_routes_match_value_class(app)
