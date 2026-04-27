"""SPEC-SEC-HYGIENE-001 REQ-44.3: JWKS fetch timeout is capped at 3 seconds.

Background: REQ-44.1, .2, .4, .5, .6 describe failure modes that don't exist
in retrieval-api's current code (no `jwt_auth_enabled=False` dev-bypass
branch in the middleware; `jwks_url` is derived from `zitadel_issuer` so
``jwt_auth_enabled=True AND jwks_url=""`` is unreachable by construction).
What IS still worthwhile is the timeout cap on the outbound JWKS fetch:
JWKS endpoints respond in sub-second; a 10-second ceiling is unnecessarily
generous and bounds nothing useful against a slow-loris JWKS endpoint.

This test pins ``_fetch_jwks`` to ``timeout <= 3.0``.
"""

from __future__ import annotations

from typing import Any

import pytest


class _RecordingClient:
    """An ``httpx.AsyncClient`` stub that records the timeout it was constructed with."""

    timeout_seen: float | None = None

    def __init__(self, timeout: float | None = None, **_kw: Any) -> None:
        # httpx accepts a Timeout object too; for our test we always pass a float.
        type(self).timeout_seen = timeout  # type: ignore[assignment]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_a):
        return False

    async def get(self, _url: str):
        class _Resp:
            status_code = 200

            def raise_for_status(self) -> None: ...

            def json(self) -> dict:
                return {"keys": []}

        return _Resp()


async def test_fetch_jwks_timeout_capped_at_three_seconds(monkeypatch):
    """REQ-44.3: ``httpx.AsyncClient`` for JWKS uses ``timeout <= 3.0``."""
    from retrieval_api.middleware import auth

    # Reset the recording slot so prior tests don't leak.
    _RecordingClient.timeout_seen = None
    # ``_fetch_jwks`` does ``import httpx`` locally — patch the httpx
    # module attribute itself so the local rebind picks up our stub.
    import httpx as _httpx
    monkeypatch.setattr(_httpx, "AsyncClient", _RecordingClient)

    await auth._fetch_jwks()

    assert _RecordingClient.timeout_seen is not None, (
        "Test scaffolding broken — _fetch_jwks did not construct an httpx "
        "AsyncClient via our stub."
    )
    assert _RecordingClient.timeout_seen <= 3.0, (
        f"_fetch_jwks built httpx.AsyncClient with timeout="
        f"{_RecordingClient.timeout_seen}s; REQ-44.3 caps it at 3.0s. "
        "JWKS endpoints respond sub-second; the previous 10s upper bound "
        "left workers exposed to slow-loris on the JWKS host."
    )


@pytest.mark.parametrize(
    ("doc_field", "must_appear"),
    [
        (
            "config.py jwt_auth_enabled property docstring",
            "REQ-44",
        ),
    ],
)
def test_unreachable_failure_modes_are_documented(doc_field: str, must_appear: str):
    """REQ-44.2 / REQ-44.6 failure modes are unreachable by construction.

    No code change is needed because ``jwt_auth_enabled`` is derived from
    ``zitadel_issuer``: if the issuer is empty, the property is False;
    therefore ``jwt_auth_enabled=True AND jwks_url=""`` (where
    ``jwks_url == f"{issuer}/oauth/v2/keys"``) cannot occur. The SPEC's
    'documented acceptance' clause requires this rationale to live in the
    code itself so the next reviewer doesn't re-file the finding.
    """
    from pathlib import Path

    config_path = Path(__file__).resolve().parents[1] / "retrieval_api" / "config.py"
    src = config_path.read_text(encoding="utf-8")
    assert must_appear in src, (
        f"{doc_field} must mention {must_appear!r} so the unreachable-by-"
        "construction acceptance is traceable from the source."
    )
