from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    postgres_dsn: str = "postgresql+asyncpg://klai:klai@postgres:5432/klai"
    qdrant_url: str = "http://qdrant:6333"
    qdrant_api_key: str = ""
    tei_url: str = "http://tei:8080"
    gitea_url: str = "http://gitea:3000"
    gitea_token: str = ""
    docs_internal_secret: str = ""
    knowledge_ingest_secret: str = ""  # X-Internal-Secret for service-to-service auth
    gitea_webhook_secret: str = ""  # HMAC secret for Gitea webhook verification
    # Max chars per chunk (roughly 300-400 tokens for BGE-M3)
    chunk_size: int = 1500
    chunk_overlap: int = 200
    # Infinity reranker (optional — disabled when empty)
    reranker_url: str = ""
    reranker_model: str = "bge-reranker-v2-m3"
    # LLM enrichment (contextual prefix + HyPE questions via LiteLLM proxy)
    litellm_url: str = "http://litellm:4000"
    litellm_api_key: str = ""
    enrichment_enabled: bool = True  # global kill switch
    enrichment_model: str = "klai-fast"
    enrichment_timeout: float = 15.0
    enrichment_max_concurrent: int = 5
    enrichment_max_document_tokens: int = 2000
    # Qdrant collection name — set to klai_knowledge_v2 after migration
    qdrant_collection: str = "klai_knowledge"

    model_config = {"env_file": ".env"}


settings = Settings()
