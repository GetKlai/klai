"""SPEC-SEC-INTERNAL-001 B2 acceptance: knowledge-mcp.

Covers REQ-1.5 (constant-time inbound), REQ-4 (sanitised log + return),
REQ-8 (no upstream body echoed to MCP tool return), REQ-9.5 (fail-closed
startup on empty KNOWLEDGE_INGEST_SECRET / DOCS_INTERNAL_SECRET / PORTAL_INTERNAL_SECRET).
"""

from __future__ import annotations

import inspect
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from klai_identity_assert import VerifyResult


def _make_ctx(headers: dict | None = None) -> MagicMock:
    ctx = MagicMock()
    ctx.request_context.request.headers = headers or {}
    return ctx


def _verified_allow() -> VerifyResult:
    return VerifyResult.allow(user_id="user1", org_id="org1", org_slug="testorg", evidence="jwt")


_VALID_HEADERS = {
    "x-user-id": "user1",
    "x-org-id": "org1",
    "x-org-slug": "testorg",
    # Must match KNOWLEDGE_INGEST_SECRET captured at import time -- the
    # session-wide stable value is set by tests/conftest.py.
    "x-internal-secret": "test-secret",
}


# ---------------------------------------------------------------------------
# REQ-8 / AC-11.1: save_to_docs never echoes resp.text to the chat UI
# ---------------------------------------------------------------------------


class TestSaveToDocsDoesNotEchoUpstreamBody:
    @pytest.mark.asyncio
    async def test_upstream_500_with_secret_in_body_is_not_returned_to_user(self):
        """AC-11.1: a klai-docs 500 echoing the DOCS_INTERNAL_SECRET in its body
        MUST NOT surface that secret in the MCP tool return string.
        """
        from main import save_to_docs

        # Upstream tells us about the auth header verbatim -- the kind of
        # accidental reflection that ServerErrorMiddleware does in debug mode.
        # The configured DOCS_INTERNAL_SECRET (per conftest) is "docs-secret".
        bad_upstream_body = (
            'Internal server error: {"Authorization": "Bearer docs-secret", '
            '"reason": "kb-not-found"}'
        )

        ctx = _make_ctx(_VALID_HEADERS)
        with (
            patch("main._asserter.verify", new_callable=AsyncMock, return_value=_verified_allow()),
            patch("main.httpx.AsyncClient") as mock_client_cls,
        ):
            list_resp = MagicMock()
            list_resp.status_code = 200
            list_resp.json.return_value = [{"name": "docs", "slug": "docs"}]
            list_resp.text = ""

            put_resp = MagicMock()
            put_resp.status_code = 500
            put_resp.text = bad_upstream_body

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=list_resp)
            mock_client.put = AsyncMock(return_value=put_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await save_to_docs(
                title="T",
                content="C",
                ctx=ctx,
                kb_name="docs",
                page_path="inbox/x",
            )

        # The verbatim secret MUST NOT appear in the user-visible return.
        assert "docs-secret" not in result, result
        # Neither should any other recognisable substring of the upstream body.
        assert "Authorization" not in result, result
        assert "Bearer" not in result, result
        # The contract response shape: status code + request_id + operator hint.
        assert re.match(
            r"^Error saving to docs: upstream returned HTTP \d+\. Request ID: [0-9a-f-]+\. .+$",
            result,
        ), result

    @pytest.mark.asyncio
    async def test_request_id_in_return_is_a_uuid(self):
        """AC-11.3: the surfaced Request ID is a UUID operators can grep."""
        from main import save_to_docs

        ctx = _make_ctx(_VALID_HEADERS)
        with (
            patch("main._asserter.verify", new_callable=AsyncMock, return_value=_verified_allow()),
            patch("main.httpx.AsyncClient") as mock_client_cls,
        ):
            list_resp = MagicMock()
            list_resp.status_code = 200
            list_resp.json.return_value = [{"name": "docs", "slug": "docs"}]
            list_resp.text = ""

            put_resp = MagicMock()
            put_resp.status_code = 502
            put_resp.text = "ignored"

            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=list_resp)
            mock_client.put = AsyncMock(return_value=put_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await save_to_docs(
                title="T",
                content="C",
                ctx=ctx,
                kb_name="docs",
                page_path="inbox/x",
            )

        match = re.search(r"Request ID: ([0-9a-f-]+)\.", result)
        assert match, result
        # Reject the trivial "no UUID" case -- length should be 36 chars (8-4-4-4-12).
        assert len(match.group(1)) == 36, match.group(1)


# ---------------------------------------------------------------------------
# REQ-1.5 / AC-8.1: source uses verify_shared_secret, not !=
# ---------------------------------------------------------------------------


class TestInboundSecretCompareUsesVerifySharedSecret:
    def test_source_uses_verify_shared_secret(self):
        import main as main_mod

        src = inspect.getsource(main_mod._validate_incoming_secret)
        assert "verify_shared_secret" in src
        # Defensive guard against regression to direct ``!=`` / ``==`` comparison.
        assert "provided != " not in src
        assert "provided == " not in src


# ---------------------------------------------------------------------------
# REQ-9.5 / AC-9.6: fail-closed startup on empty / missing secrets
# ---------------------------------------------------------------------------


_MAIN_PY_DIR = str(Path(__file__).resolve().parent.parent)


def _run_import_main(env_overrides: dict[str, str | None]) -> subprocess.CompletedProcess[str]:
    """Spawn a fresh interpreter that imports ``main`` under the given env.

    Returns the CompletedProcess so callers can assert on returncode + stderr.
    Each test passes the variable it wants to set empty / missing.
    """
    env = {
        # Inherit the parent env, then layer overrides on top.
        **os.environ,
        # Always start from a known-good baseline.
        "KLAI_DOCS_API_BASE": "http://docs-app:3000",
        "DOCS_INTERNAL_SECRET": "docs-secret-12345-9999",
        "KNOWLEDGE_INGEST_URL": "http://knowledge-ingest:8000",
        "KNOWLEDGE_INGEST_SECRET": "ingest-secret-12345-9999",
        "PORTAL_API_URL": "http://portal-api:8010",
        "PORTAL_INTERNAL_SECRET": "portal-internal-secret-12345",
    }
    for key, value in env_overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value

    return subprocess.run(
        [sys.executable, "-c", "import sys; sys.path.insert(0, '.'); import main"],
        env=env,
        cwd=_MAIN_PY_DIR,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


class TestFailClosedStartup:
    def test_empty_KNOWLEDGE_INGEST_SECRET_refuses_import(self):
        """AC-9.6: empty KNOWLEDGE_INGEST_SECRET -> import fails non-zero."""
        result = _run_import_main({"KNOWLEDGE_INGEST_SECRET": ""})
        assert result.returncode != 0, (result.stdout, result.stderr)
        assert "KNOWLEDGE_INGEST_SECRET" in result.stderr, result.stderr

    def test_empty_DOCS_INTERNAL_SECRET_refuses_import(self):
        """AC-9.6: empty DOCS_INTERNAL_SECRET -> import fails non-zero."""
        result = _run_import_main({"DOCS_INTERNAL_SECRET": ""})
        assert result.returncode != 0, (result.stdout, result.stderr)
        assert "DOCS_INTERNAL_SECRET" in result.stderr, result.stderr

    def test_empty_PORTAL_INTERNAL_SECRET_refuses_import(self):
        result = _run_import_main({"PORTAL_INTERNAL_SECRET": ""})
        assert result.returncode != 0, (result.stdout, result.stderr)
        assert "PORTAL_INTERNAL_SECRET" in result.stderr, result.stderr

    def test_missing_KNOWLEDGE_INGEST_SECRET_refuses_import(self):
        """AC-9.6: absent env var raises KeyError at module load."""
        result = _run_import_main({"KNOWLEDGE_INGEST_SECRET": None})
        assert result.returncode != 0, (result.stdout, result.stderr)
        assert "KNOWLEDGE_INGEST_SECRET" in result.stderr, result.stderr

    def test_full_env_imports_cleanly(self):
        """Sanity check: with every required var set the import succeeds."""
        result = _run_import_main({})
        assert result.returncode == 0, (result.stdout, result.stderr)


# ---------------------------------------------------------------------------
# Source-level grep guard: no `if KNOWLEDGE_INGEST_SECRET:` silent-omit guards remain.
# AC-11.2 in spirit (zero matches for the legacy guard pattern).
# ---------------------------------------------------------------------------


class TestNoSilentOmitGuardsRemain:
    def test_main_py_has_no_legacy_silent_omit_guards(self):
        """Reject the conditional-inclusion shape (``if SECRET: headers[...] = SECRET``)
        and the early-return shape (``if not SECRET: return``) inside
        ``_validate_incoming_secret``. The module-level fail-closed startup check
        uses ``if not SECRET: raise RuntimeError(...)`` which is the OPPOSITE
        pattern and is intentionally allowed.
        """
        main_src = (Path(_MAIN_PY_DIR) / "main.py").read_text(encoding="utf-8")

        # Conditional-inclusion in a request handler => silent-omit on outbound.
        forbidden_conditional_inclusion = [
            "if KNOWLEDGE_INGEST_SECRET:",
            "if DOCS_INTERNAL_SECRET:",
            "if PORTAL_INTERNAL_SECRET:",
        ]
        for pattern in forbidden_conditional_inclusion:
            assert pattern not in main_src, (
                f"Regression: legacy silent-omit guard `{pattern}` reappeared in main.py "
                "(SPEC-SEC-INTERNAL-001 REQ-9.5)."
            )

        # Early-return shape inside the inbound validator -- previous "gradual
        # rollout" path that turned the auth check into a no-op.
        early_return_pattern = re.compile(r"if not KNOWLEDGE_INGEST_SECRET:\s*\n\s+return\b")
        assert not early_return_pattern.search(main_src), (
            "Regression: `if not KNOWLEDGE_INGEST_SECRET: return` reappeared inside "
            "_validate_incoming_secret (SPEC-SEC-INTERNAL-001 REQ-9.5)."
        )

    def test_main_py_has_no_resp_text_echo_to_user(self):
        """AC-11.2: ``return f\"Error.*{resp.text\"`` returns nothing."""
        main_src = (Path(_MAIN_PY_DIR) / "main.py").read_text(encoding="utf-8")

        # The return contract for save_to_docs is now:
        #   "Error saving to docs: upstream returned HTTP {status}. Request ID: {request_id}. ..."
        # Any return statement that interpolates resp.text into a user-visible
        # error string would re-introduce the leak.
        forbidden = [
            "return f\"Error: klai-docs returned HTTP",
            "return f'Error: klai-docs returned HTTP",
        ]
        for pattern in forbidden:
            assert pattern not in main_src, (
                f"Regression: `{pattern}` reappeared in main.py (SPEC-SEC-INTERNAL-001 REQ-8.1)."
            )


# silence linter: keep textwrap / Path / pytest imports referenced.
_ = (textwrap.dedent, Path)
