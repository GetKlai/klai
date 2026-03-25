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

    # GitHub App
    github_app_id: str
    github_app_private_key: str

    # Encryption
    encryption_key: str

    # Knowledge-ingest
    knowledge_ingest_url: str

    # CORS — comma-separated list of allowed origins (e.g. https://getklai.com)
    cors_origins: str = ""

    # Crawl4AI
    crawl4ai_api_url: str = "http://crawl4ai:11235"
    crawl4ai_internal_key: str = ""

    # Portal control plane (used by klai-connector → portal for config + status callbacks)
    portal_api_url: str = "http://portal-api:8100"
    portal_internal_secret: str = ""  # Secret klai-connector sends TO portal (must match portal's INTERNAL_SECRET)
    portal_caller_secret: str = ""  # Secret portal sends TO klai-connector (must match portal's KLAI_CONNECTOR_SECRET)

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
