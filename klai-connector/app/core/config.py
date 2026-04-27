"""Pydantic Settings configuration for klai-connector."""

import base64

from pydantic import field_validator
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
    # When empty, audience verification is skipped (warn-only fallback) so that
    # existing deployments without a configured audience continue to work. SHOULD
    # be set to the klai-connector Zitadel application audience for defense-in-depth.
    zitadel_api_audience: str = ""

    # GitHub App
    github_app_id: str
    github_app_private_key: str

    # Encryption
    encryption_key: str

    # Knowledge-ingest
    knowledge_ingest_url: str
    knowledge_ingest_secret: str = ""  # X-Internal-Secret for service-to-service auth

    # CORS — comma-separated list of allowed origins (e.g. https://getklai.com)
    cors_origins: str = ""

    # Crawl4AI
    crawl4ai_api_url: str = "http://crawl4ai:11235"
    crawl4ai_internal_key: str = ""

    # Portal control plane (used by klai-connector → portal for config + status callbacks)
    portal_api_url: str = "http://portal-api:8100"
    portal_internal_secret: str = ""  # Secret klai-connector sends TO portal (must match portal's INTERNAL_SECRET)
    portal_caller_secret: str = ""  # Secret portal sends TO klai-connector (must match portal's KLAI_CONNECTOR_SECRET)

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
