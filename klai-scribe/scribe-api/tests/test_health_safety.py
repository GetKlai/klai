"""HY-37 / REQ-37 — whisper URL allowlist + sanitised /health regression.

Two concerns covered here:
- REQ-37.1: `whisper_server_url` is validated at Settings load time. A
  misconfig (typo, env drift) refuses to boot rather than silently turning
  the unauthenticated /health endpoint into an SSRF probe.
- REQ-37.2: /health responds with a generic 503 + opaque body on whisper
  failure. The internal URL and exception text never leak to the response
  body — only to structlog (with `exc_info=True`).

See SPEC-SEC-HYGIENE-001 REQ-37.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# REQ-37.1 — Settings validator allowlist
# ---------------------------------------------------------------------------

def _build_settings(whisper_url: str):
    """Build a Settings instance with the given whisper_server_url override.

    Other required env (POSTGRES_DSN etc.) is provided by conftest.
    """
    from app.core.config import Settings

    # Pydantic v2: pass via env override so the validator runs in load context.
    return Settings(whisper_server_url=whisper_url)


@pytest.mark.parametrize(
    "url",
    [
        "http://whisper:8000",
        "http://whisper-server:8000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://172.18.0.1:8000",          # current prod default — see config.py
        "https://whisper.getklai.com",
        "https://transcription.getklai.com:8443/v1",
    ],
)
def test_whisper_url_accepted(url: str) -> None:
    settings = _build_settings(url)
    assert settings.whisper_server_url == url


@pytest.mark.parametrize(
    "url",
    [
        "http://evil.com/",
        "http://evil.com:8000/health",
        "http://169.254.169.254/",         # AWS instance metadata
        "http://10.0.0.5:8000",            # private RFC1918 — not in allowlist
        "http://192.168.1.1:8000",
        "http://internal-admin-api/health",
        "http://whisper.evil.com/",        # subdomain trickery
        "https://attacker.getklai.com.evil/",  # suffix bypass attempt
        "file:///etc/passwd",
        "ftp://whisper:8000/",
        "javascript:alert(1)",
        "",                                # empty
        "not-a-url-at-all",                # no scheme
    ],
)
def test_whisper_url_rejected(url: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        _build_settings(url)
    assert "whisper_server_url" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# REQ-37.2 — /health sanitisation on ConnectError
#
# We call the async handler directly rather than via TestClient — the
# handler is a pure async function returning a JSONResponse, no fixtures
# required. This sidesteps Windows-specific TestClient lifespan hangs.
# ---------------------------------------------------------------------------


def _read(response) -> tuple[int, dict]:
    """Extract (status_code, decoded body dict) from a Starlette Response."""
    body = response.body if hasattr(response, "body") else b"{}"
    return response.status_code, json.loads(body or b"{}")


async def test_health_returns_200_when_whisper_ok() -> None:
    from app.api.health import health

    async def _ok_get(self, url, **kw):
        return httpx.Response(200, json={"status": "ok"})

    with patch.object(httpx.AsyncClient, "get", _ok_get):
        result = await health()

    status, body = _read(result)
    assert status == 200
    assert body["status"] == "ok"


async def test_health_returns_503_on_connect_error_with_sanitised_body() -> None:
    """REQ-37.2 — ConnectError → 503 with opaque body, no URL leak."""
    from app.api.health import health

    async def _connect_error(self, url, **kw):
        raise httpx.ConnectError(
            "http://whisper-internal-secret:8000/health: connection refused"
        )

    with patch.object(httpx.AsyncClient, "get", _connect_error):
        result = await health()

    status, body = _read(result)
    body_text = result.body.decode() if hasattr(result, "body") else ""

    assert status == 503
    assert body.get("detail") == "whisper unreachable"
    assert "whisper-internal-secret" not in body_text
    assert "connection refused" not in body_text
    assert "ConnectError" not in body_text


async def test_health_returns_503_on_unexpected_exception_with_sanitised_body() -> None:
    """Defense-in-depth: any other exception is ALSO sanitised to 503."""
    from app.api.health import health

    async def _boom(self, url, **kw):
        raise RuntimeError("internal-only secret 0xDEADBEEF leaked")

    with patch.object(httpx.AsyncClient, "get", _boom):
        result = await health()

    status, body = _read(result)
    body_text = result.body.decode() if hasattr(result, "body") else ""

    assert status == 503
    assert body.get("detail") == "whisper unreachable"
    assert "DEADBEEF" not in body_text
    assert "RuntimeError" not in body_text


async def test_health_returns_503_on_non_200_whisper_response() -> None:
    """REQ-37.2 — whisper returns 5xx → scribe /health returns 503 (degraded)."""
    from app.api.health import health

    async def _bad_status(self, url, **kw):
        return httpx.Response(503, json={"status": "error"})

    with patch.object(httpx.AsyncClient, "get", _bad_status):
        result = await health()

    status, _body = _read(result)
    assert status == 503
