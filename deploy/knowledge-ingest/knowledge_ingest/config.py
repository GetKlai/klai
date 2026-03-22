from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    postgres_dsn: str = "postgresql+asyncpg://klai:klai@postgres:5432/klai"
    qdrant_url: str = "http://qdrant:6333"
    qdrant_api_key: str = ""
    tei_url: str = "http://tei:8080"
    gitea_url: str = "http://gitea:3000"
    gitea_token: str = ""
    docs_internal_secret: str = ""
    # Max chars per chunk (roughly 300-400 tokens for BGE-M3)
    chunk_size: int = 1500
    chunk_overlap: int = 200

    model_config = {"env_file": ".env"}


settings = Settings()
