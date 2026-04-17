from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    qdrant_url: str = "http://qdrant:6333"
    qdrant_api_key: str = ""
    qdrant_collection: str = "klai_knowledge"
    qdrant_focus_collection: str = "klai_focus"

    tei_url: str = "http://172.18.0.1:7997"
    infinity_reranker_url: str = "http://172.18.0.1:7998"

    litellm_url: str = "http://litellm:4000"
    litellm_api_key: str = ""

    retrieval_gate_enabled: bool = True
    retrieval_gate_threshold: float = 0.1
    retrieval_candidates: int = 60
    reranker_candidates: int = 20  # top-N from retrieval sent to reranker (CPU budget)

    sparse_sidecar_url: str = "http://172.18.0.1:8001"
    sparse_sidecar_timeout: float = 5.0

    reranker_enabled: bool = True  # Infinity GPU reranker on infinity_reranker_url; ~96ms/20 docs
    coreference_model: str = "klai-fast"
    coreference_timeout: float = 3.0
    reranker_timeout: float = 30.0
    # Graphiti / FalkorDB knowledge graph
    falkordb_host: str = "falkordb"
    falkordb_port: int = 6379
    graphiti_enabled: bool = True
    graph_search_timeout: float = 5.0
    graphiti_llm_model: str = "klai-fast"

    # Model used by the synthesis (chat) layer.
    # Toggle via env var: RETRIEVAL_API_SYNTHESIS_MODEL=klai-claude
    synthesis_model: str = "klai-primary"

    # Link-graph expansion (SPEC-CRAWLER-003, R19/R20)
    link_expand_enabled: bool = True
    link_expand_seed_k: int = 10
    link_expand_max_urls: int = 30
    link_expand_candidates: int = 20
    link_authority_boost: float = 0.05

    # Source quota (SPEC-KB-021)
    source_quota_enabled: bool = True
    source_quota_max_per_source: int = 2
    source_quota_bypass_on_mention: bool = True

    # Query router (SPEC-KB-021)
    # Default OFF: router requires a populated source_label_catalog to function.
    # Enable after implementing catalog population (Qdrant distinct values or portal API).
    router_enabled: bool = False
    router_min_source_label_count: int = 4
    router_margin_single: float = 0.15
    router_margin_dual: float = 0.08
    router_llm_fallback: bool = False
    router_centroid_ttl_seconds: int = 600

    # Portal events — set to emit product_events to the portal database.
    # Separate fields avoid URL-encoding issues with special chars in passwords.
    portal_events_host: str = ""
    portal_events_port: int = 5432
    portal_events_user: str = "klai"
    portal_events_password: str = ""
    portal_events_db: str = "klai"


settings = Settings()
