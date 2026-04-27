"""SPEC-SEC-CORS-001 AC-16 + AC-17: deny-by-default CORSMiddleware in retrieval-api.

AC-16: CORSMiddleware MUST be registered in retrieval_api.main and MUST be the
       LAST add_middleware call (outermost layer per Starlette LIFO).
       Verified by both static source inspection and runtime introspection.

AC-17: With an empty allowlist, the retrieval-api MUST NOT echo
       Access-Control-Allow-Origin for ANY origin (deny-by-default).
       Verified by OPTIONS preflight probes against the test client.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MAIN_PY = Path(__file__).parent.parent / "retrieval_api" / "main.py"


# ---------------------------------------------------------------------------
# AC-16 static: grep-based source order assertion
# ---------------------------------------------------------------------------


class TestCorsPresenceStatic:
    """AC-16 static-check variant: scan main.py source for CORSMiddleware."""

    def test_cors_middleware_call_present(self) -> None:
        """A CORSMiddleware add_middleware call must appear exactly once in main.py.

        The call may be multi-line (``app.add_middleware(\\nCORSMiddleware,``),
        so we check the call-start line plus the immediately following code line.
        We skip comment lines and blank lines when looking for the class name so
        that comment windows do not produce false positives.
        """
        src = _MAIN_PY.read_text(encoding="utf-8")
        lines = src.splitlines()

        cors_add_calls = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("app.add_middleware("):
                # Check the line itself (single-line call) …
                if "CORSMiddleware" in stripped:
                    cors_add_calls += 1
                    continue
                # … or the next non-blank, non-comment code line (multi-line call).
                for j in range(i + 1, min(i + 4, len(lines))):
                    next_stripped = lines[j].strip()
                    if not next_stripped or next_stripped.startswith("#"):
                        continue  # skip blanks and comments
                    if "CORSMiddleware" in next_stripped:
                        cors_add_calls += 1
                    break  # only the immediate next code line counts

        assert cors_add_calls == 1, (
            f"Expected exactly 1 app.add_middleware(CORSMiddleware call, found {cors_add_calls}. "
            "Add deny-by-default CORSMiddleware per SPEC-SEC-CORS-001 REQ-7.1."
        )

    def test_cors_is_last_add_middleware_call(self) -> None:
        """No app.add_middleware() call must appear AFTER the CORSMiddleware call.

        Multi-line calls are handled: we join up to 3 consecutive lines starting
        from each ``app.add_middleware(`` occurrence.
        """
        src = _MAIN_PY.read_text(encoding="utf-8")
        lines = src.splitlines()

        add_middleware_calls: list[tuple[int, str]] = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("app.add_middleware("):
                joined = " ".join(lines[i : i + 3])
                add_middleware_calls.append((i, joined))

        assert add_middleware_calls, "No app.add_middleware(...) calls found in main.py"

        last_lineno, last_call = add_middleware_calls[-1]
        assert "CORSMiddleware" in last_call, (
            f"Last app.add_middleware call (line {last_lineno + 1}) must be "
            f"CORSMiddleware, but found: {last_call!r}. "
            "Reorder so CORSMiddleware is the last (outermost) middleware per "
            "SPEC-SEC-CORS-001 REQ-7.2."
        )

    def test_cors_uses_empty_allowlist(self) -> None:
        """The CORSMiddleware registration must use allow_origins=[] (deny-by-default)."""
        src = _MAIN_PY.read_text(encoding="utf-8")
        assert "allow_origins=[]" in src, (
            "CORSMiddleware must be configured with allow_origins=[] for deny-by-default "
            "stance (SPEC-SEC-CORS-001 REQ-7.2)."
        )

    def test_cors_disables_credentials(self) -> None:
        """The CORSMiddleware registration must set allow_credentials=False."""
        src = _MAIN_PY.read_text(encoding="utf-8")
        assert "allow_credentials=False" in src, (
            "CORSMiddleware must set allow_credentials=False for deny-by-default "
            "stance (SPEC-SEC-CORS-001 REQ-7.2)."
        )


# ---------------------------------------------------------------------------
# AC-16 runtime: app.user_middleware introspection
# ---------------------------------------------------------------------------


class TestCorsPresenceRuntime:
    """AC-16 runtime variant: inspect app.user_middleware after module import."""

    def test_cors_middleware_registered(self) -> None:
        """app.user_middleware contains exactly one CORSMiddleware entry."""
        from retrieval_api.main import app

        cors_entries = [m for m in app.user_middleware if m.cls is CORSMiddleware]
        assert len(cors_entries) == 1, (
            f"Expected 1 CORSMiddleware in app.user_middleware, found {len(cors_entries)}."
        )

    def test_cors_is_first_in_user_middleware(self) -> None:
        """CORSMiddleware is the first entry in app.user_middleware.

        Starlette stores user_middleware in reverse-registration order, so the
        LAST added middleware appears FIRST in app.user_middleware.
        """
        from retrieval_api.main import app

        assert app.user_middleware, "app.user_middleware is empty — no middleware registered"
        first_entry = app.user_middleware[0]
        assert first_entry.cls is CORSMiddleware, (
            f"First entry in app.user_middleware must be CORSMiddleware (last added, "
            f"i.e. outermost). Found: {first_entry.cls}."
        )


# ---------------------------------------------------------------------------
# AC-17: OPTIONS preflight probes — deny-by-default for all origins
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def retrieval_client() -> TestClient:
    """TestClient without any default auth headers — we are testing CORS policy."""
    from retrieval_api.main import app

    return TestClient(app, raise_server_exceptions=False)


class TestCorsOptionsProbes:
    """AC-17: No origin receives Access-Control-Allow-Origin from retrieval-api."""

    @pytest.mark.parametrize(
        "origin",
        [
            "https://my.getklai.com",
            "http://localhost:5174",
            "https://customer.example",
            "https://evil.example",
        ],
    )
    def test_options_preflight_returns_no_acao(
        self,
        retrieval_client: TestClient,
        origin: str,
    ) -> None:
        """OPTIONS preflight MUST NOT return Access-Control-Allow-Origin for any origin."""
        resp = retrieval_client.options(
            "/retrieve",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
            },
        )
        acao = resp.headers.get("access-control-allow-origin")
        assert acao is None or acao == "", (
            f"retrieval-api must not echo ACAO for origin {origin!r} "
            f"(deny-by-default per SPEC-SEC-CORS-001 REQ-7.2). Got: {acao!r}"
        )

    @pytest.mark.parametrize(
        "origin",
        [
            "https://my.getklai.com",
            "http://localhost:5174",
            "https://customer.example",
            "https://evil.example",
        ],
    )
    def test_options_preflight_returns_no_acac(
        self,
        retrieval_client: TestClient,
        origin: str,
    ) -> None:
        """OPTIONS preflight MUST NOT return Access-Control-Allow-Credentials: true."""
        resp = retrieval_client.options(
            "/retrieve",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
            },
        )
        acac = resp.headers.get("access-control-allow-credentials", "").lower()
        assert acac != "true", (
            f"retrieval-api must not return Access-Control-Allow-Credentials: true "
            f"for origin {origin!r} (deny-by-default per SPEC-SEC-CORS-001 REQ-7.4). "
            f"Got: {acac!r}"
        )
