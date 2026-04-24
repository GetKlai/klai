"""AC-5: Zitadel webhook replay within the 5-min window is rejected.

Covers REQ-6.1 (nonce key format + SET NX EX 300), REQ-6.2 (ts+v1 namespace),
REQ-6.3 (fail-closed 503 on Redis outage), REQ-6.4 (nonce AFTER HMAC).

Every nonce-layer failure funnels through the same uniform 401 body
(`{"detail": "invalid signature"}`) established by REQ-7.1 — "replay"
is NOT leaked to the response, only to logs.

Redis-unavailable is a distinct failure — HTTP 503 with
`{"detail": "Service unavailable"}` and log event
`mailer_nonce_redis_unavailable`.
"""

from __future__ import annotations

import importlib
import sys

import pytest
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from tests._signing import sign

VALID_BODY = b'{"contextInfo":{"eventType":"x"},"templateData":{"text":"hi"}}'


def _load_main_with_redis(redis_client):
    """Re-import app.main with the given redis client injected into app.nonce."""
    for mod in ("app.main", "app.nonce", "app.signature"):
        sys.modules.pop(mod, None)
    import app.nonce as nonce_mod
    nonce_mod.set_redis_client(redis_client)
    main = importlib.import_module("app.main")
    # main.py imports nonce; ensure its reference is also patched
    import app.nonce as nonce_after
    nonce_after.set_redis_client(redis_client)
    return main


@pytest.fixture
async def client_with_fakeredis(settings_env, fake_redis, stub_smtp):
    main = _load_main_with_redis(fake_redis)
    return TestClient(main.app), main


@pytest.fixture
async def client_with_broken_redis(settings_env, broken_redis, stub_smtp):
    main = _load_main_with_redis(broken_redis)
    return TestClient(main.app), main


async def test_first_call_accepted_second_is_replay(client_with_fakeredis, settings_env, stub_smtp):
    """REQ-6.1: identical (ts, v1) within 5 min → second call rejected 401."""
    client, _ = client_with_fakeredis
    header, _ = sign(VALID_BODY, settings_env["WEBHOOK_SECRET"])

    # First call — passes signature + nonce. May return 422 (payload has no
    # recipient) but critically NOT 401.
    resp1 = client.post("/notify", content=VALID_BODY, headers={"ZITADEL-Signature": header})
    assert resp1.status_code != 401, f"first call should pass signature: {resp1.text}"

    # Second identical call — replay
    with capture_logs() as events:
        resp2 = client.post("/notify", content=VALID_BODY, headers={"ZITADEL-Signature": header})
    assert resp2.status_code == 401
    assert resp2.json() == {"detail": "invalid signature"}
    replay_events = [
        e for e in events
        if e.get("event") == "mailer_signature_invalid" and e.get("reason") == "replay"
    ]
    assert replay_events, f"expected reason=replay log event; got {events}"


async def test_different_bodies_have_independent_nonces(
    client_with_fakeredis, settings_env, stub_smtp
):
    """REQ-6.2: distinct v1 → distinct nonce slot → both first-seen."""
    client, _ = client_with_fakeredis
    secret = settings_env["WEBHOOK_SECRET"]
    body_a = b'{"contextInfo":{"eventType":"a"},"templateData":{"text":"A"}}'
    body_b = b'{"contextInfo":{"eventType":"b"},"templateData":{"text":"B"}}'
    header_a, _ = sign(body_a, secret)
    header_b, _ = sign(body_b, secret)

    resp_a = client.post("/notify", content=body_a, headers={"ZITADEL-Signature": header_a})
    resp_b = client.post("/notify", content=body_b, headers={"ZITADEL-Signature": header_b})
    assert resp_a.status_code != 401
    assert resp_b.status_code != 401


async def test_redis_unavailable_fails_closed_503(
    client_with_broken_redis, settings_env, stub_smtp
):
    """REQ-6.3: Redis down → 503 Service unavailable + mailer_nonce_redis_unavailable."""
    client, _ = client_with_broken_redis
    header, _ = sign(VALID_BODY, settings_env["WEBHOOK_SECRET"])

    with capture_logs() as events:
        resp = client.post("/notify", content=VALID_BODY, headers={"ZITADEL-Signature": header})

    assert resp.status_code == 503
    assert resp.json() == {"detail": "Service unavailable"}
    assert stub_smtp.sent == []

    nonce_events = [e for e in events if e.get("event") == "mailer_nonce_redis_unavailable"]
    assert nonce_events, f"expected mailer_nonce_redis_unavailable event; got {events}"


async def test_nonce_not_polluted_by_forged_signatures(
    client_with_fakeredis, settings_env, stub_smtp, fake_redis
):
    """REQ-6.4: forged signature MUST NOT record a nonce entry.

    We send a request with an HMAC-invalid header. Then we send a legitimate
    request that happens to use the same timestamp + a valid v1. The second
    call MUST succeed — proving the forged attempt did not pollute the nonce
    cache (which would otherwise cause the legitimate signature to 401 as a
    'replay').
    """
    client, _ = client_with_fakeredis
    secret = settings_env["WEBHOOK_SECRET"]
    header_valid, ts = sign(VALID_BODY, secret)

    # Forged: same timestamp, bogus v1
    forged = f"t={ts},v1=deadbeef"
    resp_forged = client.post(
        "/notify", content=VALID_BODY, headers={"ZITADEL-Signature": forged}
    )
    assert resp_forged.status_code == 401

    # Legitimate: real v1 for same body+timestamp — must be accepted
    resp_real = client.post(
        "/notify", content=VALID_BODY, headers={"ZITADEL-Signature": header_valid}
    )
    assert resp_real.status_code != 401, f"forged attempt polluted the nonce: {resp_real.text}"
