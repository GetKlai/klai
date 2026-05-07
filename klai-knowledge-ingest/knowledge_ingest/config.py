from __future__ import annotations

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    postgres_dsn: str = "postgresql+asyncpg://klai:klai@postgres:5432/klai"
    qdrant_url: str = "http://qdrant:6333"
    qdrant_api_key: str = ""
    tei_url: str = "http://172.18.0.1:7997"
    tei_timeout: float = 120.0  # seconds — TEI can take 35s+ on large batches with queue
    gitea_url: str = "http://gitea:3000"
    gitea_token: str = ""
    # @MX:NOTE: reserved for future knowledge-ingest → docs-app service calls
    # (docs-app accepts X-Internal-Secret via requireAuthOrService). Not yet wired.
    docs_internal_secret: str = ""
    knowledge_ingest_secret: str = ""  # X-Internal-Secret for service-to-service auth
    gitea_webhook_secret: str = ""  # HMAC secret for Gitea webhook verification
    # Max chars per chunk (roughly 300-400 tokens for BGE-M3)
    chunk_size: int = 1500
    chunk_overlap: int = 200
    # SPEC-INGEST-LOGIN-WALL-DETECT-001 REQ-05 — anonymous-crawl auth-wall
    # detection. ``mode`` is one of "reject" (default, skip page entirely),
    # "audit_only" (log + ingest unchanged), or "degrade" (ingest with
    # quality_score=0.0 + ingest_warning metadata for audit-trail tenants).
    # Invalid values fail-safe to "audit_only" — never block the crawl pipe
    # due to a config typo.
    ingest_login_wall_detect_enabled: bool = True
    ingest_login_wall_detect_mode: str = "reject"
    # SPEC-INGEST-LOGIN-WALL-DETECT-002 REQ-02 — cluster threshold. A page is
    # flagged as a wall iff this many OR MORE OTHER pages in the same
    # (org_id, kb_slug) have a SimHash within Hamming distance 3 of the page's
    # own. Default 5 catches RedCactus's 149-page cluster easily and protects
    # single/few-page pseudo-walls under cold-start permissiveness (REQ-03).
    ingest_template_cluster_min: int = 5
    # SPEC-CONNECTOR-INPUT-VALIDATION-001 REQ-4 / D-7 — sync-time hard-fail
    # threshold. A crawl_site run with auth_wall_count / total_count above
    # this ratio AND no cookies AND no login_indicator_selector configured
    # MUST end with status='failed_partial' and a structured error_summary
    # so operators see the connector is broken — instead of the silent
    # "synced 5 days ago, 25 docs indexed" UI lie that this SPEC fixes.
    # 0.30 is calibrated against Voys help (0% trip-rate post-PR-#459) and
    # Redcactus 2026-05-07 morning (70% trip-rate). Env-tunable so prod
    # can adjust if a legitimate edge case emerges without code change.
    ingest_authwall_dirty_trip_rate: float = Field(
        default=0.30,
        validation_alias=AliasChoices("KLAI_INGEST_AUTHWALL_DIRTY_TRIP_RATE"),
    )
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
    # @MX:TODO: SPEC-KB-007 AC-10 — wire into qdrant_store.ensure_collection sparse index config.
    # Current code uses a different mechanism at collection creation; this flag is reserved.
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
    # Bearer token for outbound calls to portal-api internal endpoints
    portal_internal_token: str = ""
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

    # SPEC-TAXONOMY-V2-001 bootstrap settings.
    # ``taxonomy_bootstrap_v2_enabled`` flag was deleted in
    # SPEC-TAXONOMY-V2-CONSOLIDATION-001 along with the V1 fallback path —
    # V2 had been the default since PR #408 and the fallback was never used
    # in production.
    #
    # ``min_cluster_size_floor`` lowered 5 → 3 in
    # SPEC-TAXONOMY-V2-CONSOLIDATION-002. With floor=5 + the adaptive formula
    # ``max(floor, doc_count // 50)`` HDBSCAN under-fitted at typical KB sizes
    # (e.g. 154 docs → 3 clusters, 5-9 broken-down by content but EOM merged
    # them into 3 because no smaller stable cluster could form). The IA
    # research sweet spot for top-level taxonomy navigation is 5-9 nodes
    # (Miller's law: above 9 = decision paralysis; below 5 = too coarse).
    # Floor=3 lets HDBSCAN form the smaller stable clusters that exist in
    # the data, landing typical bootstrap output back in the 5-9 range
    # without changing the EOM cluster-selection method.
    taxonomy_bootstrap_min_cluster_size_floor: int = 3
    taxonomy_bootstrap_max_clusters: int = 20
    taxonomy_bootstrap_top_n_per_cluster: int = 8

    # SPEC-TAXONOMY-V2-CONSOLIDATION-003 — HDBSCAN cluster-selection method.
    # Default sklearn HDBSCAN uses ``'eom'`` (excess of mass): finds fewer,
    # more stable clusters by trading sub-structure for stability. On the
    # Voys/support 154-doc corpus EOM landed on 3 clusters even after
    # lowering the min_cluster_size floor 5 → 3 (V2-CONSOLIDATION-002),
    # because EOM rejects the smaller stable sub-clusters HDBSCAN's tree
    # actually contains.
    #
    # ``'leaf'`` returns the leaves of the cluster hierarchy — finer-grained,
    # typically 2-4× more clusters than EOM on the same input. Targets the
    # 5-9 IA sweet spot at typical KB sizes; needs revisiting if the corpus
    # grows >300 docs (then the leaf count may climb past the comfortable
    # browsing range and hierarchical taxonomy becomes warranted).
    #
    # Configurable via env so we can revert without redeploying if leaf-
    # mode turns out to over-segment a different corpus shape.
    taxonomy_bootstrap_cluster_selection_method: str = "leaf"

    # SPEC-TAXONOMY-V2-001-FOLLOWUP-001: UMAP pre-reduction settings (B1)
    taxonomy_bootstrap_umap_n_components: int = 10
    taxonomy_bootstrap_umap_n_neighbors: int = 15
    taxonomy_bootstrap_umap_random_state: int = 42  # for reproducibility

    # SPEC-CRAWLER-004 Fase A — Garage S3 for consolidated crawl image pipeline.
    # Feature-flagged via empty endpoint: when ``garage_s3_endpoint`` is blank
    # the crawler skips image upload and writes no ``image_urls`` into Qdrant.
    garage_s3_endpoint: str = ""
    garage_access_key: str = ""
    garage_secret_key: str = ""
    garage_bucket: str = "klai-images"
    garage_region: str = "garage"
    # httpx timeout for individual image downloads. Kept short — a slow
    # third-party host must not block a whole page ingest.
    image_download_timeout: float = 10.0

    # SPEC-CRAWLER-004 Fase C — KEK for decrypting connector cookies fetched
    # via the new /ingest/v1/crawl/sync endpoint. 64-char hex (32 bytes); an
    # empty value disables the endpoint (501 Not Implemented) so dev
    # environments can run knowledge-ingest without provisioning a KEK.
    encryption_key: str = ""

    # SPEC-RAG-EVAL-001 — nightly RAGAS evaluation harness settings.
    # retrieval_api_url: base URL of klai-retrieval-api (Docker internal network).
    #   Production hostname is `retrieval-api` (no `klai-` prefix) on port 8040,
    #   verified against /opt/klai/.env::KNOWLEDGE_RETRIEVE_URL.
    # retrieval_internal_secret: X-Internal-Secret for /retrieve auth.
    #   Accepts both RETRIEVAL_INTERNAL_SECRET (knowledge-ingest convention)
    #   AND RETRIEVAL_API_INTERNAL_SECRET (production SOPS convention, same name
    #   as portal-api and litellm-hook). Pydantic AliasChoices picks whichever
    #   is set — same fall-through pattern as deploy/litellm/klai_knowledge.py.
    #   Warn-on-empty at startup (fail-open: harness skips retrieval auth when absent
    #   in dev; production must set the secret via SOPS).
    # rag_eval_retrieval_timeout: seconds before a /retrieve call is declared failed (REQ-3).
    # rag_eval_judge_timeout: seconds before a klai-fast judge call is declared failed.
    # rag_eval_judge_model: LiteLLM model alias for answer generation + light RAGAS metrics
    #   (context_precision, context_recall). Mistral Small via LiteLLM proxy.
    # rag_eval_faithfulness_model: middle-tier LiteLLM alias for the Faithfulness
    #   metric. Mistral Small (klai-fast) hits its 3072-token output ceiling on
    #   RAGAS' multi-statement faithfulness JSON, leaving most rows NaN. Mistral
    #   Medium 3.5 (klai-medium) handles the longer JSON reliably without paying
    #   Mistral Large prices. Cost impact small: ~60 calls/night.
    # rag_eval_embeddings_model: LiteLLM alias for the embeddings model used by
    #   AnswerRelevancy (compares imaginary-question vectors to the user's actual
    #   question). Wraps BGE-M3 on TEI/gpu-01.
    # rag_eval_suites_dir: directory containing suite YAML files.
    retrieval_api_url: str = "http://retrieval-api:8040"
    retrieval_internal_secret: str = Field(
        default="",
        validation_alias=AliasChoices(
            "RETRIEVAL_INTERNAL_SECRET",
            "RETRIEVAL_API_INTERNAL_SECRET",
        ),
    )
    rag_eval_retrieval_timeout: int = 10
    rag_eval_judge_timeout: int = 30
    rag_eval_judge_model: str = "klai-fast"
    rag_eval_faithfulness_model: str = "klai-medium"
    rag_eval_embeddings_model: str = "klai-bge-m3"
    rag_eval_suites_dir: str = "knowledge_ingest/eval/suites"

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
            raise ValueError("Missing required: KNOWLEDGE_INGEST_SECRET (SPEC-SEC-011)")
        return self

    @model_validator(mode="after")
    def _require_portal_internal_token(self) -> Settings:
        """SEC-014: fail-closed on empty/missing PORTAL_INTERNAL_TOKEN.

        Same class of bug as F-003/F-012 but for the ingest→portal direction:
        outbound calls in clustering_tasks/portal_client send this token as a
        Bearer header to portal-api internal endpoints. An empty value would
        silently degrade auth on the outbound side, so missing config must
        fail at startup rather than open the surface.

        (The legacy inbound _verify_internal_token per-route check on
        routes/taxonomy.py was removed in SPEC-CODEBASE-AUDIT-001 cluster G
        TP-1 — the InternalSecretMiddleware (X-Internal-Secret) now provides
        the only inbound auth layer.)
        """
        if not self.portal_internal_token or not self.portal_internal_token.strip():
            raise ValueError("Missing required: PORTAL_INTERNAL_TOKEN (SEC-014)")
        return self

    @model_validator(mode="after")
    def _require_gitea_webhook_secret(self) -> Settings:
        """SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-12: fail-closed on missing
        GITEA_WEBHOOK_SECRET.

        Gitea POSTs to /webhooks/gitea on every push to a tenant-tracked
        repository. The handler verifies the X-Gitea-Signature HMAC against
        ``Settings.gitea_webhook_secret`` via ``hmac.compare_digest``. With
        an empty/whitespace-only value, every comparison would succeed
        against an attacker request that also has an empty signature —
        exact fail-open-auth pattern, attacker can trigger ingestion of
        attacker-controlled markdown into the tenant KB.

        Env-parity: GITEA_WEBHOOK_SECRET must exist in
        klai-infra/core-01/.env.sops BEFORE merge. Pre-flight verified
        2026-05-05: value populated in /opt/klai/.env on core-01.
        """
        if not self.gitea_webhook_secret or not self.gitea_webhook_secret.strip():
            raise ValueError(
                "Missing required: GITEA_WEBHOOK_SECRET (SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-12). "
                "knowledge-ingest verifies Gitea push-webhook HMACs against this; "
                "an empty value would accept any caller and let an attacker inject "
                "tenant KB content. Set it in SOPS before starting knowledge-ingest."
            )
        return self


settings = Settings()
