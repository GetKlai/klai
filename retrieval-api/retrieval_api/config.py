from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    qdrant_url: str = "http://qdrant:6333"
    qdrant_api_key: str = ""
    qdrant_collection: str = "klai_knowledge"
    qdrant_focus_collection: str = "klai_focus"

    tei_url: str = "http://tei:8080"
    tei_reranker_url: str = "http://tei-reranker:8080"

    litellm_url: str = "http://litellm:4000"
    litellm_api_key: str = ""

    retrieval_gate_enabled: bool = True
    retrieval_gate_threshold: float = 0.1
    retrieval_candidates: int = 60

    sparse_sidecar_url: str = ""
    sparse_sidecar_timeout: float = 5.0

    coreference_timeout: float = 3.0
    reranker_timeout: float = 2.0
    # Graphiti / FalkorDB knowledge graph
    falkordb_host: str = "falkordb"
    falkordb_port: int = 6379
    graphiti_enabled: bool = True
    graph_search_timeout: float = 5.0
    graphiti_llm_model: str = "klai-fast"


settings = Settings()
