"""Pydantic Settings configuration for klai-connector."""

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

    # Optional
    log_level: str = "INFO"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}
