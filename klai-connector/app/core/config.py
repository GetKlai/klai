"""Pydantic Settings configuration for klai-connector."""

import base64

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Database
    database_url: str

    # Zitadel OIDC
    zitadel_introspection_url: str
    zitadel_client_id: str
    zitadel_client_secret: str
    # Expected `aud` claim value for introspected tokens (SPEC-SEC-008 F-017).
    # SPEC-SEC-AUDIT-2026-04 B2: audience verification is now MANDATORY.
    # Empty/missing value raises ValidationError at startup (fail-closed).
    # The env var KLAI_CONNECTOR_ZITADEL_AUDIENCE MUST exist in SOPS before
    # this validator is deployed — see validator-env-parity pitfall in
    # .claude/rules/klai/pitfalls/process-rules.md.
    zitadel_api_audience: str = ""

    # GitHub App
    github_app_id: str
    github_app_private_key: str

    # Encryption
    encryption_key: str

    # Knowledge-ingest
    knowledge_ingest_url: str
    # SPEC-SEC-INTERNAL-001 REQ-9.3: knowledge_ingest_secret is mandatory.
    # Empty value raises ValidationError at startup -- no silent-omit on the
    # X-Internal-Secret header anymore. Default kept as empty so the
    # validator below is the sole gate; pydantic-settings will overwrite from
    # env, and an empty env (or missing env) trips the validator.
    knowledge_ingest_secret: str = ""

    # CORS — comma-separated list of allowed origins (e.g. https://getklai.com)
    cors_origins: str = ""

    # Crawl4AI
    crawl4ai_api_url: str = "http://crawl4ai:11235"
    crawl4ai_internal_key: str = ""

    # Portal control plane (used by klai-connector → portal for config + status callbacks)
    portal_api_url: str = "http://portal-api:8100"
    # SPEC-SEC-INTERNAL-001 REQ-9.3: portal_internal_secret is mandatory.
    # Empty value raises ValidationError at startup -- PortalClient never
    # sends "Bearer " (empty) on outbound calls anymore.
    portal_internal_secret: str = ""
    portal_caller_secret: str = ""  # Secret portal sends TO klai-connector (must match portal's KLAI_CONNECTOR_SECRET)

    # SPEC-SEC-TENANT-001 REQ-7.6 (v0.5.0): transition-period flag for the
    # X-Org-ID header that portal-side REQ-8.1 starts injecting. When False
    # (default during deploy), missing headers degrade to a WARN log
    # ``event="sync_missing_org_id"`` and the route proceeds without org
    # scoping (backward-compatible). When flipped to True (after the portal
    # deploy lands and VictoriaLogs shows zero ``sync_missing_org_id`` events
    # for the agreed dwell time), missing headers return HTTP 400. Set via
    # SOPS env (``SYNC_REQUIRE_ORG_ID=true``) once the portal-side rollout
    # has soaked. See SPEC REQ-8.5 for the deploy-order runbook.
    sync_require_org_id: bool = False

    # Google Drive OAuth (SPEC-KB-025)
    google_drive_client_id: str = ""  # empty = connector disabled
    google_drive_client_secret: str = ""

    # Microsoft 365 OAuth (SPEC-KB-MS-DOCS-001) — empty client_id disables the connector.
    # Azure AD app registered in the Klai-owned M365 tenant as a multi-tenant app.
    ms_docs_client_id: str = ""
    ms_docs_client_secret: str = ""
    ms_docs_tenant_id: str = "common"  # multi-tenant default; accepts any M365 tenant

    # Image storage (Garage S3)
    garage_s3_endpoint: str = ""
    garage_access_key: str = ""
    garage_secret_key: str = ""
    garage_bucket: str = "klai-images"
    garage_region: str = "garage"

    # Per-org rate limit (SPEC-SEC-HYGIENE-001 HY-32). Empty redis_url
    # disables the feature — rate-limit checks become no-ops and
    # connector.py routes accept every request. Defaults are env-tunable;
    # see PR for source-of-research on the chosen values:
    #   read  120/min ≈ Auth0 free tier; well above admin browsing volume
    #   write  30/min  3× SPEC literal; ≪ Heroku 75/min, supports
    #                  bulk-onboarding admins without pinching, still
    #                  caps unbounded row creation at 1800/hour.
    redis_url: str = ""
    connector_rl_read_per_min: int = 120
    connector_rl_write_per_min: int = 30

    # Optional
    log_level: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @field_validator("github_app_private_key", mode="before")
    @classmethod
    def decode_private_key(cls, v: str) -> str:
        """Accept PEM as-is or base64-encoded (for env var storage)."""
        if v.startswith("-----"):
            return v
        try:
            decoded = base64.b64decode(v).decode("utf-8")
            if decoded.startswith("-----"):
                return decoded
        except Exception:
            pass
        return v

    # ------------------------------------------------------------------
    # SPEC-SEC-INTERNAL-001 REQ-9.3: fail-closed startup on empty outbound
    # secrets. Mirrors the SPEC-SEC-MAILER-INJECTION-001 mailer validators.
    # An ALLOW_EMPTY_OUTBOUND_SECRETS escape hatch (REQ-9.3 exception) is NOT
    # implemented here -- if it is ever needed for a smoke-test profile, add
    # a top-level env check that bypasses these validators only when the
    # explicit flag is set.
    # ------------------------------------------------------------------
    @field_validator("knowledge_ingest_secret", mode="after")
    @classmethod
    def _require_knowledge_ingest_secret(cls, v: str) -> str:
        if not v:
            raise ValueError(
                "KNOWLEDGE_INGEST_SECRET must be a non-empty string. "
                "klai-connector authenticates outbound /ingest calls with this header; "
                "an empty value would silently disable that authentication. "
                "SPEC-SEC-INTERNAL-001 REQ-9.3."
            )
        return v

    @field_validator("portal_internal_secret", mode="after")
    @classmethod
    def _require_portal_internal_secret(cls, v: str) -> str:
        if not v:
            raise ValueError(
                "PORTAL_INTERNAL_SECRET must be a non-empty string. "
                "klai-connector authenticates outbound /internal callbacks with this Bearer; "
                "an empty value would send `Bearer ` (literal trailing space) on every call. "
                "SPEC-SEC-INTERNAL-001 REQ-9.3."
            )
        return v

    # ------------------------------------------------------------------
    # SPEC-SEC-AUDIT-2026-04 B2: fail-closed startup on empty audience.
    # Before this fix the middleware had a warn-only fallback that silently
    # skipped audience verification when zitadel_api_audience was empty,
    # allowing any valid Zitadel token (even for a different app) to pass
    # auth (cross-app token reuse).
    #
    # VALIDATOR-ENV-PARITY: KLAI_CONNECTOR_ZITADEL_AUDIENCE must exist in
    # klai-infra/core-01/.env.sops before this code is deployed. Deploy order
    # is env-var-first, validator-second.  See validator-env-parity (HIGH)
    # pitfall in .claude/rules/klai/pitfalls/process-rules.md.
    # ------------------------------------------------------------------
    @model_validator(mode="after")
    def _require_zitadel_api_audience(self) -> "Settings":
        """SPEC-SEC-AUDIT-2026-04 B2: fail-closed on empty/missing ZITADEL_API_AUDIENCE.

        Mirrors SPEC-SEC-012 in research-api. An empty audience allows cross-app
        token reuse: any Zitadel JWT that introspects as active=true passes auth.
        Setting the audience ensures only tokens issued for klai-connector's own
        Zitadel application are accepted.
        """
        if not self.zitadel_api_audience or not self.zitadel_api_audience.strip():
            raise ValueError(
                "Missing required: KLAI_CONNECTOR_ZITADEL_AUDIENCE (SPEC-SEC-AUDIT-2026-04 B2). "
                "Must be the Zitadel application audience for klai-connector. "
                "An empty value would silently skip audience verification, enabling "
                "cross-app token reuse."
            )
        return self
