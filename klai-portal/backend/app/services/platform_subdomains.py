"""Curated inventory of all Klai-controlled subdomeinen.

Single source of truth for the platform-admin subdomain overview at
``/admin/platform`` (Subdomains tab). The list is curated by hand because
DNS records live in Hetzner DNS (no API integration) and the Caddyfile
only covers what core-01 serves — public-01 (Coolify) and external
services like Mailgun or ACME need to be tracked here explicitly or
they fall off the radar.

When you add a new subdomain anywhere (Caddyfile, Coolify app, Hetzner
DNS record for an external service), add an entry here in the SAME PR
or it WILL be forgotten. The platform-admin page is the only catalogue.

# @MX:ANCHOR fan_in=2 — used by platform.py (read endpoint) and
#   tested via test_platform_subdomains.py
# @MX:REASON: Hand-curated; drift between this file and reality has
#   real user impact (Jantine cannot see what services exist).
# @MX:NOTE: Tenant-specific subdomains (chat-<slug>-<id>, docs-<slug>,
#   <slug>) are NOT in this list — they are appended dynamically from
#   portal_orgs at request time. This file is only for STATIC infra.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Category = Literal["klai_service", "tooling", "marketing", "tenant"]
"""Where the subdomain lives in our mental model.

- klai_service: production-facing Klai service (portal, auth, mcp, etc.)
- tooling: internal tooling for the team (vault, grafana, crm, etc.)
- marketing: public marketing surfaces (getklai.com, blog, etc.)
- tenant: per-tenant URL (populated dynamically from portal_orgs)
"""

Host = Literal["core-01", "public-01", "gpu-01", "external"]
"""Where the service is physically hosted.

- core-01: production VPS (Hetzner), Caddy-routed
- public-01: Coolify VPS (Hetzner), Coolify-routed
- gpu-01: GPU VPS (Hetzner), tunneled via autossh from core-01
- external: SaaS or third-party (Hetzner DNS only, no Klai infra)
"""


@dataclass(frozen=True)
class Subdomain:
    """A single subdomain entry in the platform overview."""

    subdomain: str
    """The subdomain part, e.g. "vault" for vault.getklai.com.

    Empty string means the apex (getklai.com itself).
    """
    url: str
    """Full HTTPS URL the user should click to reach the service."""
    label: str
    """Short human name. Title-case, not a sentence."""
    description: str
    """One sentence on what the service is for. NL — this overview is
    only ever seen by Klai staff (platform-admin gated)."""
    category: Category
    host: Host
    owner: str
    """Who maintains it. First-name only — this is an internal page."""


# --- Marketing surfaces -----------------------------------------------------

_MARKETING: list[Subdomain] = [
    Subdomain(
        subdomain="",
        url="https://getklai.com",
        label="Marketing site",
        description="Publieke marketing-website (Astro).",
        category="marketing",
        host="public-01",
        owner="Jantine",
    ),
    Subdomain(
        subdomain="www",
        url="https://www.getklai.com",
        label="WWW alias",
        description="Alias-redirect naar getklai.com.",
        category="marketing",
        host="public-01",
        owner="Jantine",
    ),
    Subdomain(
        subdomain="cdn",
        url="https://cdn.getklai.com",
        label="CDN",
        description="DNS-alias gereserveerd voor toekomstige asset-CDN. Geen live service.",
        category="marketing",
        host="external",
        owner="Mark",
    ),
]


# --- Klai-services (productie, gebruiker-facing) ----------------------------

_KLAI_SERVICES: list[Subdomain] = [
    Subdomain(
        subdomain="my",
        url="https://my.getklai.com",
        label="Portal SPA",
        description="Login en portal-applicatie. Eén URL voor alle tenants.",
        category="klai_service",
        host="core-01",
        owner="Mark",
    ),
    Subdomain(
        subdomain="auth",
        url="https://auth.getklai.com",
        label="Zitadel",
        description="OIDC identity provider — alle logins lopen hierdoor.",
        category="klai_service",
        host="core-01",
        owner="Mark",
    ),
    Subdomain(
        subdomain="llm",
        url="https://llm.getklai.com",
        label="LiteLLM",
        description="LLM-proxy met klai-tier aliases (klai-fast, klai-primary, klai-large).",
        category="klai_service",
        host="core-01",
        owner="Mark",
    ),
    Subdomain(
        subdomain="mcp",
        url="https://mcp.getklai.com",
        label="MCP server",
        description="Model Context Protocol server voor agentic flows.",
        category="klai_service",
        host="core-01",
        owner="Mark",
    ),
    Subdomain(
        subdomain="connector",
        url="https://connector.getklai.com",
        label="Klai-connector",
        description="Sync van externe bronnen (Google Drive, Notion, Confluence, etc.).",
        category="klai_service",
        host="core-01",
        owner="Mark",
    ),
    Subdomain(
        subdomain="api",
        url="https://api.getklai.com",
        label="Partner API",
        description="Externe API voor partner-integraties (chat, retrieval).",
        category="klai_service",
        host="core-01",
        owner="Mark",
    ),
    Subdomain(
        subdomain="mailer",
        url="https://mailer.getklai.com",
        label="Klai-mailer",
        description="Transactional email service (Zitadel webhooks, magic links).",
        category="klai_service",
        host="core-01",
        owner="Mark",
    ),
    Subdomain(
        subdomain="meet",
        url="https://meet.getklai.com",
        label="Meet (Vexa)",
        description="Meeting-bot voor automatische transcriptie via Vexa.",
        category="klai_service",
        host="core-01",
        owner="Mark",
    ),
    Subdomain(
        subdomain="logs-ingest",
        url="https://logs-ingest.getklai.com",
        label="Logs ingest",
        description="Public endpoint van Alloy voor externe log-ingest (public-01).",
        category="klai_service",
        host="core-01",
        owner="Mark",
    ),
    Subdomain(
        subdomain="acme",
        url="https://acme.getklai.com",
        label="ACME",
        description="Certificate-provisioning service voor wildcard certs.",
        category="klai_service",
        host="core-01",
        owner="Mark",
    ),
    Subdomain(
        subdomain="dev",
        url="https://dev.getklai.com",
        label="Dev portal",
        description="Dev-versie van de portal (preview branch deployments).",
        category="klai_service",
        host="core-01",
        owner="Mark",
    ),
]


# --- Tooling (intern, team-only) --------------------------------------------

_TOOLING: list[Subdomain] = [
    Subdomain(
        subdomain="vault",
        url="https://vault.getklai.com",
        label="Vaultwarden",
        description="Password manager voor het Klai team.",
        category="tooling",
        host="core-01",
        owner="Mark",
    ),
    Subdomain(
        subdomain="grafana",
        url="https://grafana.getklai.com",
        label="Grafana",
        description="Dashboards (Prometheus + VictoriaLogs + PostgreSQL).",
        category="tooling",
        host="core-01",
        owner="Mark",
    ),
    Subdomain(
        subdomain="errors",
        url="https://errors.getklai.com",
        label="GlitchTip",
        description="Frontend + backend exception tracking (self-hosted Sentry-alternatief).",
        category="tooling",
        host="core-01",
        owner="Mark",
    ),
    Subdomain(
        subdomain="cal",
        url="https://cal.getklai.com",
        label="Cal.com",
        description="Booking-tool voor demo-calls met prospects.",
        category="tooling",
        host="core-01",
        owner="Jantine",
    ),
    Subdomain(
        subdomain="status",
        url="https://status.getklai.com",
        label="Status page",
        description="Uptime Kuma — publieke status van Klai services.",
        category="tooling",
        host="public-01",
        owner="Mark",
    ),
    Subdomain(
        subdomain="crm",
        url="https://crm.getklai.com",
        label="CRM",
        description="Sales-CRM voor pipeline en klantcontact.",
        category="tooling",
        host="public-01",
        owner="Jantine",
    ),
    Subdomain(
        subdomain="feedback",
        url="https://feedback.getklai.com",
        label="Feedback",
        description="Externe feedback-tool (productfeedback van klanten).",
        category="tooling",
        host="public-01",
        owner="Jantine",
    ),
    Subdomain(
        subdomain="analytics",
        url="https://analytics.getklai.com",
        label="Analytics",
        description="Privacy-vriendelijke website-analytics.",
        category="tooling",
        host="public-01",
        owner="Jantine",
    ),
    Subdomain(
        subdomain="firecrawl",
        url="https://firecrawl.getklai.com",
        label="Firecrawl",
        description="Web-crawler service voor knowledge-ingest van publieke websites.",
        category="tooling",
        host="public-01",
        owner="Mark",
    ),
    Subdomain(
        subdomain="mail",
        url="https://mail.getklai.com",
        label="Mail (MX/SPF)",
        description="DNS-entry voor inkomende mail. Verwerkt door externe provider.",
        category="tooling",
        host="external",
        owner="Jantine",
    ),
]


KLAI_SUBDOMAINS: list[Subdomain] = [*_MARKETING, *_KLAI_SERVICES, *_TOOLING]
"""All curated subdomains in display order.

Order matters — the UI renders sections in this sequence (Marketing →
Klai services → Tooling → Tenant). Within each section the order in
this file is preserved, so put related entries next to each other.
"""
