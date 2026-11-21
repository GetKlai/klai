"""The settings a test session builds point no outbound client at a live Klai host.

Outbound clients (identity provider, mailer, ingest, retrieval, LiteLLM, the
docs app, object storage) read their base URL from ``Settings``; in tests each
must be empty, a container name that only resolves inside the deploy network,
or a host that cannot resolve. The fields below are links shown to a browser
and are never called by the portal itself.
"""

from __future__ import annotations

from urllib.parse import urlparse

from app.core.config import settings

_BROWSER_LINK_FIELDS = {"slack_service_public_url", "kb_images_base_url", "hubspot_webchat_help_desk_url"}
_PRODUCTION_DOMAIN = "getklai.com"


def test_identity_provider_host_cannot_resolve() -> None:
    assert urlparse(settings.zitadel_base_url).hostname == "zitadel.invalid"


def test_no_outbound_url_setting_points_at_a_production_host() -> None:
    offenders = {}
    for name, value in settings.model_dump().items():
        if not name.endswith("url") or name in _BROWSER_LINK_FIELDS or not isinstance(value, str):
            continue
        host = urlparse(value).hostname or ""
        if host == _PRODUCTION_DOMAIN or host.endswith(f".{_PRODUCTION_DOMAIN}"):
            offenders[name] = value
    assert offenders == {}
