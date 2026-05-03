"""SPEC-SEC-TENANT-001 A-8 (portal-side) — klai_connector_client header injection.

REQ-8.1 / REQ-8.4 (v0.5.0 / β): the sync client methods MUST forward
``X-Org-ID`` on every outbound call to klai-connector. The header value
is the Zitadel resourceowner string sourced from
``PortalOrg.zitadel_org_id`` at the route layer (REQ-8.2 / REQ-8.3).

Tests assert the header construction via the private ``_headers``
helper (the single point that decides whether to include ``X-Org-ID``)
and pin the keyword-only signature contract. The end-to-end httpx
behaviour is exercised by integration / smoke tests outside this
suite — the unit-level guarantee is that a non-None ``org_id`` always
produces a header and that callsites cannot silently omit the kwarg.
"""

from __future__ import annotations

from app.services.klai_connector_client import KlaiConnectorClient


def test_headers_include_x_org_id_when_provided() -> None:
    """REQ-8.1: _headers(org_id=...) returns X-Org-ID."""
    client = KlaiConnectorClient()
    headers = client._headers(org_id="org-a-resourceowner")

    assert headers.get("X-Org-ID") == "org-a-resourceowner", (
        f"REQ-8.1: X-Org-ID must be present when org_id is supplied; got {headers!r}"
    )
    assert "Authorization" in headers, "portal-caller bearer must remain present"


def test_headers_omit_x_org_id_when_none() -> None:
    """compute_fingerprint and other non-sync paths must not receive X-Org-ID.

    REQ-7 only attaches the header to sync-route endpoints. The
    private helper takes ``org_id=None`` for callsites that hit other
    endpoints (today: ``compute_fingerprint``).
    """
    client = KlaiConnectorClient()
    headers = client._headers(org_id=None)

    assert "X-Org-ID" not in headers, f"X-Org-ID MUST NOT leak onto non-sync endpoints; got {headers!r}"
    assert "Authorization" in headers


def test_trigger_sync_signature_requires_org_id_kwarg() -> None:
    """REQ-8.4: org_id is keyword-only and required.

    A future portal-side handler that forgets to pass org_id MUST fail
    at call time (TypeError) rather than silently send a request without
    the header. This pins that contract at the signature level.
    """
    import inspect

    sig = inspect.signature(KlaiConnectorClient.trigger_sync)
    org_id_param = sig.parameters.get("org_id")
    assert org_id_param is not None, "trigger_sync MUST declare org_id parameter"
    assert org_id_param.kind == inspect.Parameter.KEYWORD_ONLY, (
        "trigger_sync.org_id MUST be keyword-only so callsites read self-documentingly"
    )
    assert org_id_param.default is inspect.Parameter.empty, (
        "trigger_sync.org_id MUST have no default — REQ-8.4 forbids the silent omission path"
    )


def test_get_sync_runs_signature_requires_org_id_kwarg() -> None:
    """REQ-8.4: same contract on get_sync_runs."""
    import inspect

    sig = inspect.signature(KlaiConnectorClient.get_sync_runs)
    org_id_param = sig.parameters.get("org_id")
    assert org_id_param is not None
    assert org_id_param.kind == inspect.Parameter.KEYWORD_ONLY
    assert org_id_param.default is inspect.Parameter.empty


def test_compute_fingerprint_does_not_send_x_org_id() -> None:
    """compute_fingerprint is NOT a sync-route endpoint.

    REQ-7 only adds X-Org-ID to /connectors/{id}/sync, /syncs, and
    /syncs/{run_id}. The compute_fingerprint endpoint has no
    tenancy filter and MUST NOT receive an X-Org-ID — keeping the
    blast radius of the new header narrow.
    """
    import inspect

    sig = inspect.signature(KlaiConnectorClient.compute_fingerprint)
    assert "org_id" not in sig.parameters
