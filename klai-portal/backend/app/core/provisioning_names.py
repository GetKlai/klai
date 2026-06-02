"""Central external-name construction for tenant provisioning.

Tenant slugs are reused by multiple external systems. Keep the raw slug as the
public contract for URLs, but validate derived names against hard external
limits before provisioning starts so a tenant cannot fail halfway through.
"""

from __future__ import annotations

from dataclasses import dataclass

DNS_LABEL_MAX_LENGTH = 63
TENANT_SLUG_MAX_LENGTH = DNS_LABEL_MAX_LENGTH - len("chat-")


class ProvisioningNameError(ValueError):
    """Raised when a tenant slug produces an invalid external resource name."""


@dataclass(frozen=True)
class ProvisioningNames:
    slug: str
    chat_label: str
    chat_host: str
    chat_origin: str
    librechat_container: str
    mongodb_database: str
    mongodb_user: str
    caddyfile_name: str
    caddy_rate_limit_zone: str
    litellm_team_alias: str
    zitadel_oidc_app_name: str
    zitadel_redirect_uri: str
    zitadel_post_logout_redirect_uris: tuple[str, str]


def provisioning_names_for_slug(slug: str, *, domain: str) -> ProvisioningNames:
    """Return every external name currently derived from a tenant slug."""
    chat_label = f"chat-{slug}"
    chat_origin = f"https://{chat_label}.{domain}"
    librechat_container = f"librechat-{slug}"
    return ProvisioningNames(
        slug=slug,
        chat_label=chat_label,
        chat_host=f"{chat_label}.{domain}",
        chat_origin=chat_origin,
        librechat_container=librechat_container,
        mongodb_database=librechat_container,
        mongodb_user=librechat_container,
        caddyfile_name=f"{slug}.caddyfile",
        caddy_rate_limit_zone=f"chat_{slug}_per_ip",
        litellm_team_alias=slug,
        zitadel_oidc_app_name=librechat_container,
        zitadel_redirect_uri=f"{chat_origin}/oauth/openid/callback",
        zitadel_post_logout_redirect_uris=(chat_origin, f"{chat_origin}/login"),
    )


def validate_provisioning_names(names: ProvisioningNames) -> None:
    """Validate derived external names against known hard limits.

    The relevant failure class here is not the database slug constraint; it is
    external systems with smaller derived-name limits. Today the public
    ``chat-{slug}`` DNS label is the tightest shared limit in portal
    provisioning. Docs-app Gitea org names have their own deterministic
    shortening helper because Gitea's max is even smaller and not URL-facing.
    """
    if len(names.chat_label) > DNS_LABEL_MAX_LENGTH:
        raise ProvisioningNameError(
            "tenant slug is too long for the LibreChat DNS label: "
            f"{names.chat_label!r} is {len(names.chat_label)} characters, "
            f"max {DNS_LABEL_MAX_LENGTH}"
        )


def validate_slug_for_provisioning(slug: str, *, domain: str) -> ProvisioningNames:
    """Build and validate external names for a slug, then return them."""
    names = provisioning_names_for_slug(slug, domain=domain)
    validate_provisioning_names(names)
    return names


__all__ = [
    "DNS_LABEL_MAX_LENGTH",
    "TENANT_SLUG_MAX_LENGTH",
    "ProvisioningNameError",
    "ProvisioningNames",
    "provisioning_names_for_slug",
    "validate_provisioning_names",
    "validate_slug_for_provisioning",
]
