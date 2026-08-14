"""HTTP-level tests: the policy has to be wired to the right requests.

test_policy.py proves the verdict is correct. These prove the proxy asks for a
verdict on the requests that matter and on no others — a correct policy behind a
route that never fires is worth nothing.
"""

from __future__ import annotations

import json

import httpx
import pytest
from starlette.testclient import TestClient

from app.main import build_app
from app.policy import PORTAL_API, VEXA_RUNTIME

PROVEN_ESCALATION = {
    "Image": "alpine:3.22",
    "HostConfig": {"Binds": ["/:/host"], "Privileged": True, "PidMode": "host"},
}


class _RecordingUpstream:
    """Stands in for docker-socket-proxy and records what reached it.

    The assertion that matters is not only "the caller got a 403" but "the
    request never arrived upstream". A proxy that denies the client while still
    forwarding has closed nothing.
    """

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, bytes]] = []

    async def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.method, request.url.path, request.content))
        return httpx.Response(201, json={"Id": "upstream-said-ok"})


@pytest.fixture
def client_and_upstream():
    def _make(policy):
        upstream = _RecordingUpstream()
        app = build_app(policy)
        client = TestClient(app)
        client.__enter__()  # runs the startup hook that builds app.state.client
        app.state.client = httpx.AsyncClient(transport=httpx.MockTransport(upstream.handler))
        return client, upstream

    return _make


def test_hostile_create_is_refused_and_never_reaches_upstream(client_and_upstream) -> None:
    client, upstream = client_and_upstream(PORTAL_API)
    resp = client.post("/v1.43/containers/create", json=PROVEN_ESCALATION, params={"name": "evil"})

    assert resp.status_code == 403
    assert "refused by klai-docker-authz" in resp.json()["message"]
    assert upstream.requests == [], "denied create still reached docker-socket-proxy"


def test_legitimate_create_is_forwarded_unchanged(client_and_upstream) -> None:
    client, upstream = client_and_upstream(PORTAL_API)
    body = {
        "Image": "librechat",
        "HostConfig": {"Binds": ["/opt/klai/librechat/voys/.env:/app/.env:ro"]},
    }
    resp = client.post("/v1.43/containers/create", json=body, params={"name": "librechat-voys"})

    assert resp.status_code == 201
    assert len(upstream.requests) == 1
    method, path, content = upstream.requests[0]
    assert (method, path) == ("POST", "/v1.43/containers/create")
    # Byte-for-byte: the proxy must not rewrite a body it approved. A silent
    # mutation here would be indistinguishable from a Docker bug at the callsite.
    assert json.loads(content) == body


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/v1.43/containers/json"),
        ("GET", "/v1.43/containers/librechat-voys/json"),
        ("POST", "/v1.43/containers/librechat-voys/start"),
        ("POST", "/v1.43/containers/librechat-voys/restart"),
        ("DELETE", "/v1.43/containers/librechat-voys"),
        ("GET", "/v1.43/networks/klai-net"),
        ("POST", "/v1.43/networks/klai-net/connect"),
    ],
)
def test_every_other_endpoint_passes_through(method: str, path: str, client_and_upstream) -> None:
    """Only container-create carries a HostConfig; policing more would be scope creep.

    docker-socket-proxy's own method+path whitelist still sits behind us and is
    unchanged — it remains the first line of defence for these.
    """
    client, upstream = client_and_upstream(PORTAL_API)
    resp = client.request(method, path)

    assert resp.status_code == 201  # the stub upstream's answer
    assert len(upstream.requests) == 1
    assert upstream.requests[0][1] == path


def test_unparseable_create_body_fails_closed(client_and_upstream) -> None:
    """REQ-S-001. We cannot police what we cannot read, so we refuse it."""
    client, upstream = client_and_upstream(PORTAL_API)
    resp = client.post(
        "/v1.43/containers/create",
        content=b"{not json",
        headers={"content-type": "application/json"},
    )

    assert resp.status_code == 403
    assert "unparseable" in resp.json()["message"]
    assert upstream.requests == []


def test_unversioned_create_path_is_also_policed(client_and_upstream) -> None:
    """Docker clients may omit the /v1.xx prefix; matching only the versioned form is a bypass."""
    client, upstream = client_and_upstream(PORTAL_API)
    resp = client.post("/containers/create", json=PROVEN_ESCALATION)

    assert resp.status_code == 403
    assert upstream.requests == []


def test_each_listener_enforces_its_own_principal(client_and_upstream) -> None:
    """REQ-U-002: the bot runtime may not use portal-api's bind allowance."""
    librechat_bind = {
        "Image": "x",
        "HostConfig": {"Binds": ["/opt/klai/librechat/voys/.env:/app/.env:ro"]},
    }

    portal_client, portal_upstream = client_and_upstream(PORTAL_API)
    assert portal_client.post("/v1.43/containers/create", json=librechat_bind).status_code == 201
    assert len(portal_upstream.requests) == 1

    bot_client, bot_upstream = client_and_upstream(VEXA_RUNTIME)
    assert bot_client.post("/v1.43/containers/create", json=librechat_bind).status_code == 403
    assert bot_upstream.requests == []


def test_health_is_served_locally_and_names_the_principal(client_and_upstream) -> None:
    client, upstream = client_and_upstream(VEXA_RUNTIME)
    resp = client.get("/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "principal": "vexa12-runtime"}
    assert upstream.requests == [], "/health must not be proxied to the daemon"
