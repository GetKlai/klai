from __future__ import annotations

from pydantic import model_validator
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

    # Source-aware selection (SPEC-KB-021)
    source_quota_enabled: bool = True
    source_quota_max_per_source: int = 2

    # Query router (SPEC-KB-021)
    # Pre-search: identifies relevant sources, passes decision to source_aware_select.
    # Centroids computed from actual chunk vectors (not label strings).
    router_enabled: bool = True
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

    # SPEC-SEC-010 — Authentication and request hardening
    # Shared secret for internal service-to-service calls (portal-api, research-api, LiteLLM hook).
    # REQ-1.1 + REQ-5.2: empty / whitespace-only value MUST cause startup failure.
    internal_secret: str = ""
    # Zitadel issuer + audience for JWT validation (REQ-1.2, REQ-5.1).
    zitadel_issuer: str = ""
    zitadel_api_audience: str = ""
    # Sliding-window rate limit per caller identity (REQ-4.3).
    rate_limit_rpm: int = 600
    # Redis URL for the rate limiter (REQ-4.1). Fail-open when unreachable (REQ-4.5).
    redis_url: str = ""

    # SPEC-SEC-IDENTITY-ASSERT-001 REQ-4 — Phase D
    # When an internal-secret caller submits a body with org_id / user_id, the
    # retrieval-api re-asserts the (user, org) tuple against portal-api's
    # /internal/identity/verify before running any retrieval. Both vars are
    # required at startup — the IdentityAsserter constructor in
    # retrieval_api.middleware.auth fails-closed if either is empty, so the
    # service refuses to boot rather than silently routing internal-secret
    # traffic through the old "trust the body" path.
    portal_api_url: str = ""
    portal_internal_secret: str = ""

    @model_validator(mode="after")
    def _validate_security_settings(self) -> Settings:
        """REQ-1.1 / REQ-5.2: fail-closed on missing required security config.

        Required (fail-closed): INTERNAL_SECRET — without it, auth is bypassed.
        Required (fail-closed): REDIS_URL — rate limiter fails open to identity
            check only, but Redis config is still expected.

        Optional (graceful degrade):
          ZITADEL_ISSUER + ZITADEL_API_AUDIENCE — if either is empty, the JWT
          auth path is disabled entirely. All requests MUST then come with a
          valid X-Internal-Secret. Bearer JWTs are rejected with 401.

          This is the correct state until SEC-012 lands: retrieval-api is only
          called by trusted services (portal-api, focus, LiteLLM hook) using
          the internal-secret path; no end-user JWT flows through it yet.
        """
        missing: list[str] = []
        if not self.internal_secret or not self.internal_secret.strip():
            missing.append("INTERNAL_SECRET")
        if not self.redis_url or not self.redis_url.strip():
            missing.append("REDIS_URL")
        if missing:
            raise ValueError(
                "Missing required security configuration (SPEC-SEC-010 REQ-5.2): "
                + ", ".join(missing)
            )
        return self

    @property
    def jwt_auth_enabled(self) -> bool:
        """True when both Zitadel issuer and audience are configured."""
        return bool(
            self.zitadel_issuer
            and self.zitadel_issuer.strip()
            and self.zitadel_api_audience
            and self.zitadel_api_audience.strip()
        )


settings = Settings()
