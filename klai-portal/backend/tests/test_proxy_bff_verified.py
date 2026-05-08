"""SPEC-SEC-IDENTITY-ASSERT-002 REQ-2: BFF proxy verifies identity before
forwarding to scribe / docs upstreams and injects ``X-Klai-Verified-*``
headers from the verified decision.

Pinned invariants:
- B1: allow → upstream receives ``X-Klai-Verified-User-Id``,
  ``X-Klai-Verified-Org-Id``, ``X-Klai-Verified-Org-Slug``,
  ``X-Internal-Secret``, plus the existing ``Authorization`` Bearer token.
- B2: deny → 403 returned, no upstream call ever made.
- B3: client-asserted ``X-Klai-Verified-*`` headers are stripped before
  portal-api re-injects its own values.
- B6: verification log line is always emitted.
- Helper: ``verify_bff_session_identity`` returns membership-authoritative
  decision in a single SELECT.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import structlog.testing

# ---------------------------------------------------------------------------
# verify_bff_session_identity (helper)
# ---------------------------------------------------------------------------


class TestVerifyBffSessionIdentity:
    """Direct unit tests on the membership-authoritative helper."""

    async def test_allow_when_active_membership_exists(self) -> None:
        from app.services.identity_verifier import verify_bff_session_identity

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.one_or_none = MagicMock(return_value=("zitadel-org-acme", "acme"))
        mock_db.execute = AsyncMock(return_value=mock_result)

        decision = await verify_bff_session_identity(
            db=mock_db,
            zitadel_user_id="u-1",
            portal_org_id=42,
        )

        assert decision.verified is True
        assert decision.user_id == "u-1"
        assert decision.org_id == "zitadel-org-acme"
        assert decision.org_slug == "acme"
        assert decision.evidence == "jwt"
        # Single SELECT — no follow-up queries.
        assert mock_db.execute.await_count == 1

    async def test_deny_when_no_membership(self) -> None:
        from app.services.identity_verifier import verify_bff_session_identity

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.one_or_none = MagicMock(return_value=None)
        mock_db.execute = AsyncMock(return_value=mock_result)

        decision = await verify_bff_session_identity(
            db=mock_db,
            zitadel_user_id="u-1",
            portal_org_id=999,
        )

        assert decision.verified is False
        assert decision.reason == "no_membership"
        assert decision.org_id is None
        assert decision.org_slug is None


# ---------------------------------------------------------------------------
# _proxy verify-before-forward integration (mocked DB + httpx)
# ---------------------------------------------------------------------------


def _async_iter(chunks: list[bytes]) -> Any:
    """Build an async iterator over fixed bytes chunks."""

    async def _gen() -> Any:
        for c in chunks:
            yield c

    return _gen()


class _FakeUpstreamResponse:
    """Minimal stand-in for ``httpx.Response`` returned by stream-mode send."""

    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.headers: dict[str, str] = {"content-type": "application/json"}
        self._closed = False

    def aiter_raw(self) -> Any:
        return _async_iter([b'{"ok":true}'])

    async def aclose(self) -> None:
        self._closed = True


def _request(method: str = "GET", headers: dict[str, str] | None = None) -> MagicMock:
    request = MagicMock()
    request.method = method
    request.headers = headers or {}
    request.query_params = MagicMock()
    request.query_params.multi_items = MagicMock(return_value=[])
    request.body = AsyncMock(return_value=b"")
    return request


def _session(*, org_id: int | None = 42) -> SimpleNamespace:
    return SimpleNamespace(
        access_token="real-portal-bearer-token",
        zitadel_user_id="u-zitadel-1",
        org_id=org_id,
    )


class TestProxyVerifyBeforeForward:
    """REQ-2.1, REQ-2.2, REQ-2.6: portal-api proxy verifies identity before
    forwarding any byte upstream."""

    async def test_allow_forwards_with_verified_headers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.api import proxy as proxy_mod
        from app.core.config import settings

        # Stub verify_bff_session_identity → allow
        async def _fake_verify(*, db: Any, zitadel_user_id: str, portal_org_id: int) -> Any:
            from app.services.identity_verifier import VerifyDecision

            assert zitadel_user_id == "u-zitadel-1"
            assert portal_org_id == 42
            return VerifyDecision.allow(
                user_id="u-zitadel-1",
                org_id="o-zitadel-acme",
                org_slug="acme",
                evidence="jwt",
            )

        monkeypatch.setattr(proxy_mod, "verify_bff_session_identity", _fake_verify)

        # Capture the upstream request the proxy builds.
        captured: dict[str, Any] = {}

        class _StubClient:
            def build_request(self, *, method: str, url: str, headers: dict[str, str], content: bytes) -> Any:
                captured["method"] = method
                captured["url"] = url
                captured["headers"] = headers
                captured["body"] = content
                return SimpleNamespace(method=method, url=url)

            async def send(self, _req: Any, *, stream: bool) -> Any:
                captured["streamed"] = stream
                return _FakeUpstreamResponse()

        monkeypatch.setattr(proxy_mod, "_get_client", lambda: _StubClient())

        response = await proxy_mod._proxy(
            service="scribe",
            rest="v1/transcriptions",
            request=_request(headers={"Accept": "application/json"}),
            session=_session(),
            db=AsyncMock(),
        )

        # Upstream was called.
        assert "headers" in captured, "verify-before-forward should not block this allow path"
        sent = captured["headers"]
        # Verified-headers injected by the BFF, not from the inbound request.
        assert sent["X-Klai-Verified-User-Id"] == "u-zitadel-1"
        assert sent["X-Klai-Verified-Org-Id"] == "o-zitadel-acme"
        assert sent["X-Klai-Verified-Org-Slug"] == "acme"
        # Portal-api injects its own internal-secret from settings — exact
        # value is set by conftest at import time. We only assert that the
        # header IS present and matches the live settings value.
        assert sent["X-Internal-Secret"] == settings.internal_secret
        assert sent["X-Internal-Secret"]  # non-empty
        # Existing Authorization injection still wired.
        assert sent["Authorization"] == "Bearer real-portal-bearer-token"
        # Response code propagates.
        assert response.status_code == 200

    async def test_deny_returns_403_without_upstream_call(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.api import proxy as proxy_mod

        async def _fake_verify(*, db: Any, zitadel_user_id: str, portal_org_id: int) -> Any:
            from app.services.identity_verifier import VerifyDecision

            return VerifyDecision.deny("no_membership")

        monkeypatch.setattr(proxy_mod, "verify_bff_session_identity", _fake_verify)

        # Tripwire — must NEVER be called when verify denies.
        send_called = {"ok": False}

        class _Tripwire:
            def build_request(self, **_kw: Any) -> Any:
                send_called["ok"] = True
                raise AssertionError("upstream MUST NOT be contacted on deny")

            async def send(self, *_args: Any, **_kw: Any) -> Any:
                send_called["ok"] = True
                raise AssertionError("upstream MUST NOT be contacted on deny")

        monkeypatch.setattr(proxy_mod, "_get_client", lambda: _Tripwire())

        response = await proxy_mod._proxy(
            service="scribe",
            rest="v1/transcriptions",
            request=_request(),
            session=_session(),
            db=AsyncMock(),
        )

        assert response.status_code == 403
        assert send_called["ok"] is False

    async def test_session_without_org_returns_403(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Session in inconsistent state (org_id is None) → 403, no verify call."""
        from app.api import proxy as proxy_mod

        verify_called = {"ok": False}

        async def _fake_verify(**_kw: Any) -> Any:
            verify_called["ok"] = True
            raise AssertionError("verify MUST NOT run when session.org_id is None")

        monkeypatch.setattr(proxy_mod, "verify_bff_session_identity", _fake_verify)

        response = await proxy_mod._proxy(
            service="scribe",
            rest="v1/transcriptions",
            request=_request(),
            session=_session(org_id=None),
            db=AsyncMock(),
        )

        assert response.status_code == 403
        assert verify_called["ok"] is False

    async def test_log_emits_verified_true_with_evidence(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.api import proxy as proxy_mod
        from app.services.identity_verifier import VerifyDecision

        async def _fake_verify(**_kw: Any) -> VerifyDecision:
            return VerifyDecision.allow(
                user_id="u-1",
                org_id="o-1",
                org_slug="acme",
                evidence="jwt",
            )

        monkeypatch.setattr(proxy_mod, "verify_bff_session_identity", _fake_verify)

        class _Stub:
            def build_request(self, **_kw: Any) -> Any:
                return SimpleNamespace()

            async def send(self, *_a: Any, **_k: Any) -> Any:
                return _FakeUpstreamResponse()

        monkeypatch.setattr(proxy_mod, "_get_client", lambda: _Stub())

        with structlog.testing.capture_logs() as logs:
            await proxy_mod._proxy(
                service="scribe",
                rest="v1/transcriptions",
                request=_request(),
                session=_session(),
                db=AsyncMock(),
            )

        verified_events = [e for e in logs if e.get("event") == "bff_proxy_verified"]
        assert len(verified_events) == 1
        entry = verified_events[0]
        assert entry["verified"] is True
        assert entry["evidence"] == "jwt"
        assert entry["service"] == "scribe"
        assert "verify_latency_ms" in entry

    async def test_log_emits_verified_false_with_reason_on_deny(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.api import proxy as proxy_mod
        from app.services.identity_verifier import VerifyDecision

        async def _fake_verify(**_kw: Any) -> VerifyDecision:
            return VerifyDecision.deny("no_membership")

        monkeypatch.setattr(proxy_mod, "verify_bff_session_identity", _fake_verify)

        with structlog.testing.capture_logs() as logs:
            await proxy_mod._proxy(
                service="scribe",
                rest="v1/transcriptions",
                request=_request(),
                session=_session(),
                db=AsyncMock(),
            )

        verified_events = [e for e in logs if e.get("event") == "bff_proxy_verified"]
        assert len(verified_events) == 1
        entry = verified_events[0]
        assert entry["verified"] is False
        assert entry["reason"] == "no_membership"


# ---------------------------------------------------------------------------
# Inbound X-Klai-Verified-* stripping (REQ-2.3)
# ---------------------------------------------------------------------------


class TestInboundVerifiedHeaderStripping:
    def test_inbound_klai_verified_user_id_is_stripped(self) -> None:
        from app.api.proxy import _build_upstream_headers

        request = MagicMock()
        request.headers = {
            "X-Klai-Verified-User-Id": "ATTACKER",
            "X-Klai-Verified-Org-Id": "TARGET-ORG",
            "X-Klai-Verified-Org-Slug": "victim",
            "Accept": "application/json",
        }
        session = SimpleNamespace(access_token="bearer")

        headers = _build_upstream_headers(
            request,
            session,
            service="scribe",
            verified_user_id="real-user-1",
            verified_org_id="real-org-1",
            verified_org_slug="real-slug",
        )

        # The inbound forged values are NOT present.
        assert "ATTACKER" not in headers.values()
        assert "TARGET-ORG" not in headers.values()
        assert "victim" not in headers.values()
        # Portal-api injects the verified values.
        assert headers["X-Klai-Verified-User-Id"] == "real-user-1"
        assert headers["X-Klai-Verified-Org-Id"] == "real-org-1"
        assert headers["X-Klai-Verified-Org-Slug"] == "real-slug"

    def test_inbound_strip_emits_injection_blocked_log(self) -> None:
        from app.api.proxy import _build_upstream_headers

        request = MagicMock()
        request.headers = {"X-Klai-Verified-User-Id": "ATTACKER"}
        session = SimpleNamespace(access_token="bearer")

        with structlog.testing.capture_logs() as logs:
            _build_upstream_headers(
                request,
                session,
                service="scribe",
                verified_user_id="real-user-1",
                verified_org_id="real-org-1",
                verified_org_slug="real-slug",
            )

        # The strip path logs proxy_header_injection_blocked exactly as for
        # other secret-bearing names.
        blocked = [e for e in logs if e.get("event") == "proxy_header_injection_blocked"]
        assert any(e.get("header") == "x-klai-verified-user-id" for e in blocked)
