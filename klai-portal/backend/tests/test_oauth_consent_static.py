"""Tests for portal-api static file serving (SPEC-MCP-AUTH-001 refactor).

Verifies that:
- GET /static/oauth/consent.css returns HTTP 200 with content-type text/css.
- The static mount does not interfere with the /health route.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client() -> TestClient:
    from app.main import app

    return TestClient(app, raise_server_exceptions=True)


def test_consent_css_served(client: TestClient) -> None:
    """GET /static/oauth/consent.css must return 200 text/css."""
    response = client.get("/static/oauth/consent.css")
    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}. "
        "Check that StaticFiles is mounted and app/static/oauth/consent.css exists."
    )
    content_type = response.headers.get("content-type", "")
    assert "text/css" in content_type, (
        f"Expected content-type text/css, got: {content_type}"
    )


def test_consent_css_not_empty(client: TestClient) -> None:
    """consent.css must contain actual CSS content."""
    response = client.get("/static/oauth/consent.css")
    assert response.status_code == 200
    body = response.text
    assert ":root" in body, "Expected :root block with CSS custom properties"
    assert "--color-rl-accent" in body, "Expected Klai token --color-rl-accent"
    assert "--font-sans" in body, "Expected --font-sans token"


def test_static_nonexistent_returns_404(client: TestClient) -> None:
    """A missing static file must return 404, not the SPA index.html."""
    response = client.get("/static/oauth/nonexistent.css")
    assert response.status_code == 404


def test_health_still_accessible(client: TestClient) -> None:
    """StaticFiles mount must not shadow the /health route."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
