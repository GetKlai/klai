from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).parent.parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_file_encoding="utf-8")

    # Zitadel JWKS validation
    zitadel_issuer: str = "https://auth.getklai.com"

    # Database — shared PostgreSQL, scribe schema
    postgres_dsn: str  # postgresql+asyncpg://...

    # whisper-server
    whisper_server_url: str = "http://172.18.0.1:8000"
    stt_provider: str = "whisper_http"
    # Provider label stored in audit column — update to "whisper-gpu" when moving to Phase 3+
    whisper_provider_name: str = "whisper-cpu"

    # Upload limits
    max_upload_mb: int = 100

    # Audio file storage (persists recordings for retry on transcription failure)
    audio_storage_dir: str = "/data/audio"

    # LiteLLM gateway (for AI summarization)
    litellm_base_url: str = "http://litellm:4000"
    litellm_master_key: str = ""
    extraction_model: str = "klai-fast"
    synthesis_model: str = "klai-primary"

    # Knowledge-ingest service (for KB ingestion)
    knowledge_ingest_url: str = "http://knowledge-ingest:8000"
    knowledge_ingest_secret: str = ""

    log_level: str = "INFO"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


settings = Settings()
