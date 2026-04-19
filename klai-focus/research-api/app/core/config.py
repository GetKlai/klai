from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).parent.parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_file_encoding="utf-8")

    # Zitadel JWKS validation
    zitadel_issuer: str = "https://auth.getklai.com"
    # SPEC-SEC-012: audience verification is mandatory. Empty/missing audience
    # would historically disable aud-check silently; the model_validator below
    # now fails startup with a clear message naming the env var.
    zitadel_api_audience: str = ""

    # Database — shared PostgreSQL, research schema
    postgres_dsn: str  # postgresql+asyncpg://...

    # Vector backend
    vector_backend: str = "qdrant"
    qdrant_url: str = ""
    qdrant_api_key: str = ""
    qdrant_collection: str = "klai_focus"

    # Internal service URLs
    docling_url: str = "http://docling-serve:5001"
    retrieval_api_url: str = ""
    # SPEC-SEC-010 REQ-6.2: shared secret for X-Internal-Secret header sent to
    # retrieval-api. Must match retrieval-api's RETRIEVAL_API_INTERNAL_SECRET.
    # Empty value is treated as "retrieval disabled" — we log and skip rather
    # than send an unauthenticated request that is guaranteed to 401.
    retrieval_api_internal_secret: str = ""
    tei_url: str = "http://172.18.0.1:7997"
    litellm_url: str = "http://litellm:4000"
    litellm_api_key: str = ""
    searxng_url: str = "http://searxng:8080"

    # YouTube — optional residential proxy, used as fallback when YouTube blocks server IP
    # Format: https://user:pass@host:port
    youtube_proxy_url: str = ""

    # Limits
    max_upload_mb: int = 50

    synthesis_model: str = "klai-primary"

    log_level: str = "INFO"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @model_validator(mode="after")
    def _require_zitadel_api_audience(self) -> "Settings":
        """SPEC-SEC-012: fail-closed on empty/missing ZITADEL_API_AUDIENCE.

        Before SEC-012 the code had a conditional branch that silently
        disabled audience verification when the env var was empty. That
        allowed any valid Zitadel access token (even for a different app)
        to pass auth. This validator makes the audience a required config
        value so startup aborts instead of serving an insecure default.
        """
        if not self.zitadel_api_audience or not self.zitadel_api_audience.strip():
            raise ValueError(
                "Missing required: RESEARCH_API_ZITADEL_AUDIENCE (SPEC-SEC-012). "
                "Must be the Zitadel project Resource ID for research-api."
            )
        return self


settings = Settings()
