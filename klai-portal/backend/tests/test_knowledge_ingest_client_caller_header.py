"""SPEC-TI-003 AC-7 — X-Caller-Service: portal-api on all knowledge-ingest calls.

Every outbound call from knowledge_ingest_client MUST include
``X-Caller-Service: portal-api`` so knowledge-ingest identity-assertion can
verify the caller without falling back to body-trust.

This test pins the header at the module level by inspecting the source code
directly (AST-free grep) so future refactors that rename the header or drop
it from a new method fail CI without any network call.

Regression: before SPEC-TI-003 AC-7, none of the 13 call-sites sent
``X-Caller-Service``. The identity-assertion middleware would have returned 400
on every portal→ingest call once knowledge-ingest enforces the check.
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Module under test
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).parent.parent.parent.parent  # klai-portal/backend/tests -> klai
_CLIENT_FILE = _REPO_ROOT / "klai-portal" / "backend" / "app" / "services" / "knowledge_ingest_client.py"

_EXPECTED_HEADER_VALUE = "portal-api"
_EXPECTED_HEADER_KEY = "X-Caller-Service"
_EXPECTED_HEADER_PAIR = f'"{_EXPECTED_HEADER_KEY}": "{_EXPECTED_HEADER_VALUE}"'


def _read_client_source() -> str:
    return _CLIENT_FILE.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Structural tests — source-level inspection
# ---------------------------------------------------------------------------


def test_client_file_exists() -> None:
    """The client file must exist before header assertions mean anything."""
    assert _CLIENT_FILE.exists(), (
        f"knowledge_ingest_client.py not found at {_CLIENT_FILE}. Did the file get renamed or moved?"
    )


def test_all_httpx_async_clients_send_caller_service_header() -> None:
    """Every httpx.AsyncClient(..., headers={...}) block must include X-Caller-Service.

    AC-7 requirement: knowledge-ingest identity-assertion rejects calls without
    the header. A call that omits it silently disables KB context for users.

    Methodology: count the number of ``httpx.AsyncClient`` instantiations and
    the number of ``X-Caller-Service`` occurrences. They must be equal.
    """
    source = _read_client_source()

    client_count = source.count("httpx.AsyncClient(")
    header_count = source.count(_EXPECTED_HEADER_PAIR)

    assert client_count > 0, (
        "knowledge_ingest_client.py has no httpx.AsyncClient calls — the test is misconfigured or the file is empty."
    )
    assert header_count == client_count, (
        f"SPEC-TI-003 AC-7 VIOLATION: {client_count} httpx.AsyncClient instantiations "
        f"but only {header_count} include {_EXPECTED_HEADER_PAIR!r}. "
        f"Every call to knowledge-ingest MUST send X-Caller-Service: portal-api."
    )


def test_no_call_site_missing_caller_header() -> None:
    """Cross-check: any method that calls httpx.AsyncClient must be preceded by the header.

    This supplements the count check with a line-level scan to surface which
    specific async_with block is missing the header.
    """
    source = _read_client_source()
    lines = source.splitlines()

    # Collect line indices of AsyncClient instantiations
    client_lines = [i for i, line in enumerate(lines) if "httpx.AsyncClient(" in line]

    violations: list[str] = []
    for idx in client_lines:
        # Look in the next 5 lines for the header (typical headers= span 1-3 lines)
        window = "\n".join(lines[idx : idx + 6])
        if _EXPECTED_HEADER_PAIR not in window:
            violations.append(f"  Line {idx + 1}: {lines[idx].strip()!r} — missing {_EXPECTED_HEADER_PAIR!r}")

    assert not violations, (
        "SPEC-TI-003 AC-7: the following AsyncClient calls are missing X-Caller-Service:\n" + "\n".join(violations)
    )


def test_module_docstring_references_spec_ti_003() -> None:
    """The AC-7 note in the docstring must be present (regression against accidental wipe)."""
    source = _read_client_source()
    assert "SPEC-TI-003 AC-7" in source, (
        "The SPEC-TI-003 AC-7 annotation was removed from knowledge_ingest_client.py. "
        "This makes intent invisible to future maintainers — restore the docstring note."
    )


# ---------------------------------------------------------------------------
# Functional tests — httpx transport mock
# ---------------------------------------------------------------------------


def test_get_source_count_sends_caller_service_header(monkeypatch) -> None:
    """get_source_count() sends X-Caller-Service: portal-api (transport mock)."""
    import asyncio
    from unittest.mock import MagicMock, patch

    import httpx

    from app.services.knowledge_ingest_client import get_source_count

    captured_headers: dict = {}

    async def _mock_send(self, request, *args, **kwargs):
        captured_headers.update(dict(request.headers))
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value={"source_count": 42})
        return mock_resp

    with patch.object(httpx.AsyncClient, "send", _mock_send):
        asyncio.run(get_source_count(org_id="org-test", kb_slug="kb-slug"))

    assert "x-caller-service" in captured_headers, (
        f"X-Caller-Service header was not sent; captured: {list(captured_headers.keys())}"
    )
    assert captured_headers["x-caller-service"] == "portal-api", (
        f"Expected portal-api, got {captured_headers['x-caller-service']!r}"
    )


def test_delete_kb_sends_caller_service_header(monkeypatch) -> None:
    """delete_kb() sends X-Caller-Service: portal-api (transport mock)."""
    import asyncio
    from unittest.mock import MagicMock, patch

    import httpx

    from app.services.knowledge_ingest_client import delete_kb

    captured_headers: dict = {}

    async def _mock_send(self, request, *args, **kwargs):
        captured_headers.update(dict(request.headers))
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    with patch.object(httpx.AsyncClient, "send", _mock_send):
        asyncio.run(delete_kb(org_id="org-test", kb_slug="kb-slug"))

    assert captured_headers.get("x-caller-service") == "portal-api", (
        f"delete_kb did not send X-Caller-Service: portal-api; captured: {captured_headers!r}"
    )
