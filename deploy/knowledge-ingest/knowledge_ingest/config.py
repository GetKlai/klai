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
    # Sparse embedding sidecar (BGE-M3 FlagEmbedding)
    sparse_sidecar_url: str = "http://bge-m3-sparse:8001"
    sparse_sidecar_timeout: float = 5.0
    sparse_sidecar_batch_size: int = 64
    sparse_index_on_disk: bool = False  # AC-10: set True to move sparse index to disk
    # Qdrant collection name — single collection with named + sparse vectors
    qdrant_collection: str = "klai_knowledge"
    # Public-facing base URL used as Gitea webhook callback URL (env: KNOWLEDGE_INGEST_PUBLIC_URL)
    knowledge_ingest_public_url: str = "http://knowledge-ingest:8000"
    # Graphiti / FalkorDB knowledge graph
    falkordb_host: str = "falkordb"
    falkordb_port: int = 6379
    graphiti_enabled: bool = True
    graphiti_llm_model: str = "klai-fast"

    model_config = {"env_file": ".env"}


settings = Settings()
