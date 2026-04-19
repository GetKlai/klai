from __future__ import annotations

from pydantic import model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    postgres_dsn: str = "postgresql+asyncpg://klai:klai@postgres:5432/klai"
    qdrant_url: str = "http://qdrant:6333"
    qdrant_api_key: str = ""
    tei_url: str = "http://172.18.0.1:7997"
    tei_timeout: float = 120.0  # seconds — TEI can take 35s+ on large batches with queue
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
    # Seconds to wait after the last Gitea save before ingesting into the knowledge layer.
    # Prevents LLM enrichment calls on every auto-save during active editing.
    ingest_debounce_seconds: int = 180
    enrichment_model: str = "klai-fast"
    enrichment_timeout: float = 15.0
    enrichment_max_concurrent: int = 2  # Mistral account limit: 60 RPM shared across all aliases
    enrichment_max_document_tokens: int = 2000
    # Sparse embedding sidecar (BGE-M3 FlagEmbedding)
    sparse_sidecar_url: str = "http://172.18.0.1:8001"
    sparse_sidecar_timeout: float = 5.0
    sparse_sidecar_batch_size: int = 64
    sparse_index_on_disk: bool = False  # AC-10: set True to move sparse index to disk
    # Qdrant collection name — single collection with named + sparse vectors
    qdrant_collection: str = "klai_knowledge"
    # Public-facing base URL used as Gitea webhook callback URL (env: KNOWLEDGE_INGEST_PUBLIC_URL)
    knowledge_ingest_public_url: str = "http://knowledge-ingest:8000"
    # Crawl4AI REST API (shared Docker container)
    crawl4ai_api_url: str = "http://crawl4ai:11235"
    crawl4ai_api_key: str = ""
    # Graphiti / FalkorDB knowledge graph
    falkordb_host: str = "falkordb"
    falkordb_port: int = 6379
    graphiti_enabled: bool = True
    graphiti_llm_model: str = "klai-fast"
    graphiti_max_concurrent: int = 1  # concurrent episodes; increase with paid LLM plan
    graphiti_episode_delay: float = 5.0
    # Token bucket rate limit for LLM calls inside add_episode().
    # Graphiti makes ~5 sequential HTTP calls per episode; this ensures they never
    # exceed the upstream API limit regardless of LLM response time.
    # Mistral org limit = 1 req/s → default 1.0. Raise for providers with higher limits.
    graphiti_llm_rps: float = 1.0
    # Portal integration for taxonomy (SPEC-KB-021)
    portal_url: str = "http://portal-api:8000"
    portal_internal_token: str = ""  # X-Internal-Token for portal internal endpoints
    taxonomy_classification_model: str = "klai-fast"
    taxonomy_classification_timeout: float = 30.0
    content_label_timeout: float = 15.0
    # Taxonomy clustering thresholds (SPEC-KB-024 R7)
    taxonomy_centroids_dir: str = "~/.klai/taxonomy_centroids"
    taxonomy_centroid_match_threshold: float = 0.85
    taxonomy_auto_categorise_threshold: float = 0.82
    taxonomy_cluster_min_size: int = 5
    taxonomy_cluster_trigger_count: int = 20
    taxonomy_centroid_max_age_hours: int = 48

    model_config = {"env_file": ".env"}

    @model_validator(mode="after")
    def _require_knowledge_ingest_secret(self) -> Settings:
        """SPEC-SEC-011: fail-closed on empty/missing KNOWLEDGE_INGEST_SECRET.

        Without this, the middleware and the per-route ``_verify_internal_secret``
        helper both historically short-circuited to allow all traffic — a single
        missing env var disabled every auth layer simultaneously. This validator
        makes the secret a required configuration value; startup aborts with a
        clear message naming the env var (the value itself is never logged).
        """
        if not self.knowledge_ingest_secret or not self.knowledge_ingest_secret.strip():
            raise ValueError(
                "Missing required: KNOWLEDGE_INGEST_SECRET (SPEC-SEC-011)"
            )
        return self


settings = Settings()
