"""URL-template builder for Zitadel email-link flows.

SPEC-PORTAL-AUTH-EMAIL-LINKS-001 REQ-5.

Zitadel v2 emits invite, password-reset, and email-verification mails with a
button link whose target URL is built from a ``url_template`` field that Zitadel
substitutes server-side using ``{{.UserID}}``, ``{{.Code}}`` and ``{{.OrgID}}``
placeholders. Klai pins those links to ``my.getklai.com`` so the user lands on
the Klai-branded ``/password/set`` (or future ``/verify``) route instead of
Zitadel's stock hosted UI.

This module is the SINGLE place in ``klai-portal/backend/app`` that composes
those template URLs. The ``tests/test_zitadel_email_link_lint.py`` AST-walker
test (SPEC REQ-6) enforces that every Zitadel email-link API call passes a
literal ``urlTemplate`` in its JSON body.

Why a helper instead of inline strings: every caller builds the same URL, and
forgetting one placeholder produces a silent regression (Zitadel ships a
mail with an unusable link). Centralising in one module + boot-time assertion
(SPEC REQ-7) closes the loop.
"""

from __future__ import annotations

from enum import StrEnum
from urllib.parse import urlparse

from app.core.config import settings

# Zitadel substitutes these tokens server-side. They must appear literally in
# the URL we send — no URL-encoding, no formatting. See proto:
#   zitadel/user/v2/{user,password,email}.proto — url_template field comments.
_QUERY_TEMPLATE = "?userID={{.UserID}}&code={{.Code}}&orgID={{.OrgID}}"

# Per-proto max length for url_template across all three Send* messages.
# (validate.rules).string = {min_len: 1, max_len: 200}
URL_TEMPLATE_MAX_LEN = 200


class AuthLinkRoute(StrEnum):
    """Klai frontend routes that consume Zitadel-substituted auth links.

    The enum value is the frontend path. Adding a new value here is the
    one-line change required to wire up a new Zitadel email-link flow
    (e.g. a future email-change verification flow — SPEC REQ-4).
    """

    PASSWORD_SET = "/password/set"
    VERIFY_EMAIL = "/verify"


def build_url_template(route: AuthLinkRoute) -> str:
    """Compose the Zitadel ``url_template`` for an email-link flow.

    Returns a URL of the form
    ``<portal_url>/<route>?userID={{.UserID}}&code={{.Code}}&orgID={{.OrgID}}``
    where the three placeholders are passed literally for Zitadel to substitute.

    Uses ``settings.portal_url`` (which falls back to ``f"https://portal.{domain}"``
    if ``FRONTEND_URL`` is empty) — same source of truth as ``auth.py``'s
    ``create_idp_intent.success_url`` (SPEC-AUTH-008 callsite pattern).

    Raises ``RuntimeError`` if the resulting URL exceeds the 200-character
    proto-level cap — this would cause Zitadel to reject the call with a
    validation error at runtime, which is harder to diagnose than a fail-fast.
    """
    base = settings.portal_url.rstrip("/")
    template = base + route.value + _QUERY_TEMPLATE
    if len(template) > URL_TEMPLATE_MAX_LEN:
        raise RuntimeError(
            f"auth_links url_template exceeds Zitadel max_len=200: "
            f"len={len(template)} route={route.value} base={base!r}"
        )
    return template


def assert_auth_link_templates_ready() -> None:
    """Boot-time assertion (SPEC REQ-7).

    Called from ``app.main.lifespan``. Refuses to start the container if:
      * ``portal_url`` is empty or malformed
      * the composed template misses any of the three placeholders
      * the composed template exceeds the 200-char proto cap

    The pattern mirrors ``assert_portal_users_rls_ready()`` — fail loud at
    startup, not at first request.
    """
    portal_url = settings.portal_url
    if not portal_url:
        raise RuntimeError(
            "auth_links: settings.portal_url is empty. "
            "Cannot build Zitadel url_template — refusing to start. "
            "Set FRONTEND_URL (e.g. https://my.getklai.com) in the env, "
            "or ensure DOMAIN is configured so portal_url falls back to "
            "https://portal.<domain>."
        )
    parsed = urlparse(portal_url)
    if not parsed.scheme or not parsed.netloc:
        raise RuntimeError(
            f"auth_links: settings.portal_url is not a URL: {portal_url!r}. Expected scheme://host form."
        )

    required_placeholders = ("{{.UserID}}", "{{.Code}}", "{{.OrgID}}")
    for route in AuthLinkRoute:
        template = build_url_template(route)
        for placeholder in required_placeholders:
            if placeholder not in template:
                raise RuntimeError(
                    f"auth_links url_template for {route.name} is missing placeholder {placeholder!r}: {template!r}"
                )
