from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).parent.parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_file_encoding="utf-8")

    # Zitadel JWKS validation
    zitadel_issuer: str = "https://auth.getklai.com"
    # Set to the Zitadel project ID (or client_id) to enforce audience verification.
    # Leave empty only in development; production MUST set this.
    zitadel_api_audience: str = ""

    # Database — shared PostgreSQL, research schema
    postgres_dsn: str  # postgresql+asyncpg://...

    # Vector backend
    vector_backend: str = "pgvector"  # "pgvector" | "qdrant"
    qdrant_url: str = ""
    qdrant_collection: str = "research"

    # Internal service URLs
    docling_url: str = "http://docling-serve:5001"
    tei_url: str = "http://tei:8080"
    litellm_url: str = "http://litellm:4000"
    litellm_api_key: str = ""
    searxng_url: str = "http://searxng:8888"

    # YouTube — optional residential proxy, used as fallback when YouTube blocks server IP
    # Format: https://user:pass@host:port
    youtube_proxy_url: str = ""

    # Limits
    max_upload_mb: int = 50

    log_level: str = "INFO"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


settings = Settings()
