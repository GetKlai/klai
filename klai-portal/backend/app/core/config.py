from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).parent.parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        # Tolerate keys that exist in the server .env but no longer have a
        # matching Settings field (e.g. ZITADEL_PORTAL_APP_ID after SPEC-AUTH-008
        # decommissioned the old SPA portal app). Without this the container
        # refuses to start whenever operational env and code drift briefly.
        extra="ignore",
    )

    # Zitadel
    zitadel_base_url: str = "https://auth.getklai.com"
    zitadel_pat: str = ""  # PORTAL_API_ZITADEL_PAT — never exposed to frontend
    zitadel_project_id: str = "362771533686374406"
    zitadel_org_id: str = ""
    zitadel_portal_org_id: str = "362757920133283846"  # Org where all portal users live
    zitadel_portal_client_id: str = "369262708920483857"  # OIDC client_id for BFF code exchange (confidential WEB app)
    zitadel_portal_client_secret: str = ""  # PORTAL_API_ZITADEL_PORTAL_CLIENT_SECRET (SPEC-AUTH-008)
    zitadel_idp_google_id: str = ""  # ZITADEL_IDP_GOOGLE_ID — instance-level Google IDP
    zitadel_idp_microsoft_id: str = ""  # ZITADEL_IDP_MICROSOFT_ID — instance-level Microsoft IDP

    # Database
    database_url: str = ""  # asyncpg DSN: postgresql+asyncpg://...
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_recycle: int = 3600
    db_pool_pre_ping: bool = True

    # Moneybird
    moneybird_api_token: str = ""
    moneybird_admin_id: str = "480855402911630899"
    moneybird_webhook_token: str = ""

    # Product IDs — one per plan/cycle combination (Moneybird > Instellingen > Producten)
    # Fetch IDs: source .env && curl -s -H "Authorization: Bearer $MONEYBIRD_API_TOKEN" \
    #   "https://moneybird.com/api/v2/$MONEYBIRD_ADMIN_ID/products.json" | python3 -m json.tool
    moneybird_product_core_monthly: str = ""
    moneybird_product_core_yearly: str = ""
    moneybird_product_professional_monthly: str = ""
    moneybird_product_professional_yearly: str = ""
    moneybird_product_complete_monthly: str = ""
    moneybird_product_complete_yearly: str = ""

    def moneybird_product_id(self, plan: str, cycle: str) -> str:
        key = f"moneybird_product_{plan}_{cycle}"
        value = getattr(self, key, "")
        if not value:
            raise ValueError(f"Moneybird product ID niet geconfigureerd voor {plan}/{cycle}")
        return value

    # Application-level encryption for tenant secrets (zitadel_librechat_client_secret, litellm_team_key)
    # 64-char hex string = 32 bytes; generate with: openssl rand -hex 32
    portal_secrets_key: str = ""  # PORTAL_API_PORTAL_SECRETS_KEY

    # Connector credential encryption (KEK for two-tier key hierarchy -- SPEC-KB-020)
    # 64-char hex string = 32 bytes; generate with: openssl rand -hex 32
    encryption_key: str = ""  # PORTAL_API_ENCRYPTION_KEY

    # Domain
    domain: str = "getklai.com"

    # SSO cookie encryption (Fernet key)
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    sso_cookie_key: str = ""  # PORTAL_API_SSO_COOKIE_KEY
    sso_cookie_max_age: int = 7776000  # 90 days; Zitadel session lifetime is the real authority

    # SPEC-SEC-SESSION-001 REQ-1.1: TTL for the Redis-backed TOTP pending-login
    # state. Default 300 s preserves the legacy in-memory ``_pending_totp``
    # window. Tunable so ops can compress it without code changes.
    totp_pending_ttl_seconds: int = 300

    # BFF — Backend-for-Frontend session auth (SPEC-AUTH-008)
    # Fernet key for encrypting BFF session records at rest in Redis.
    # Falls back to sso_cookie_key when unset — single key during the rollout.
    bff_session_key: str = ""  # PORTAL_API_BFF_SESSION_KEY
    bff_session_ttl_seconds: int = 30 * 24 * 3600  # 30 days, matches Zitadel refresh-token lifetime
    bff_access_token_skew_seconds: int = 60  # refresh this many seconds before expiry

    # Container name for the MongoDB instance (varies per docker-compose project name)
    mongodb_container_name: str = "mongodb"

    # Secrets passed to new LibreChat containers (read from /opt/klai/.env)
    mongo_root_username: str = "root"
    mongo_root_password: str = ""
    meili_master_key: str = ""
    litellm_master_key: str = ""
    redis_password: str = ""
    redis_host: str = "redis"
    redis_port: int = 6379
    firecrawl_internal_key: str = ""  # FIRECRAWL_INTERNAL_KEY — shared web search API key

    # Provisioning paths (container-internal paths, mounted from host)
    caddy_tenants_path: str = "/caddy/tenants"  # per-tenant .caddyfile dir (caddy-tenants volume)
    librechat_container_data_path: str = "/librechat"  # base dir for per-tenant librechat files
    librechat_host_data_path: str = "/opt/klai/librechat"  # HOST path for Docker volume mounts
    librechat_image: str = "ghcr.io/danny-avila/librechat:latest"
    caddy_container_name: str = "klai-core-caddy-1"  # Docker container name for Caddy reload
    redis_container_name: str = "klai-core-redis-1"  # Docker container name; legacy operational reference

    # Internal service-to-service secret (used by klai-mailer → portal)
    # Generate with: openssl rand -hex 32
    internal_secret: str = ""

    # SPEC-SEC-005 REQ-1.7: per-caller-IP rate limit ceiling for /internal/* endpoints.
    # Sliding-window (60s) over Redis; fails open when Redis is unavailable.
    # Tune via INTERNAL_RATE_LIMIT_RPM env var without code change.
    internal_rate_limit_rpm: int = 100

    # SPEC-SEC-INTERNAL-001 REQ-5: behaviour when the rate-limit Redis
    # backend is unavailable. ``closed`` returns HTTP 503 (production
    # default -- bounded blast radius); ``open`` falls through with a
    # warning log (legacy SEC-005 REQ-1.3 behaviour, kept for staging
    # / dev availability).
    # Production env file in klai-infra/ sets INTERNAL_RATE_LIMIT_FAIL_MODE=closed
    # explicitly so a future default flip does not surprise the rotation.
    internal_rate_limit_fail_mode: Literal["open", "closed"] = "closed"

    # SPEC-SEC-INTERNAL-001 REQ-2.3: Redis key pattern that the LibreChat
    # config-regenerate handler invalidates via SCAN+UNLINK. Default is
    # the upstream ``configs:*`` namespace; settable via env so a future
    # LibreChat upgrade that renames the namespace can ship in SOPS
    # without a code change.
    librechat_cache_key_pattern: str = "configs:*"

    # klai-mailer service URL (for sending transactional emails)
    mailer_url: str = ""  # e.g. http://klai-mailer:8300

    # klai-docs internal secret (used by portal → klai-docs for KB provisioning)
    docs_internal_secret: str = ""

    # SPEC-SEC-010 REQ-6.1: shared secret for X-Internal-Secret header sent to
    # retrieval-api. Must match retrieval-api's RETRIEVAL_API_INTERNAL_SECRET.
    # Kept separate from ``internal_secret`` (mailer → portal) so the two
    # cross-service trust boundaries can be rotated independently.
    retrieval_api_internal_secret: str = ""

    # MongoDB root URI for lazy LibreChat user ID mapping (KB-010).
    # Needs read access to all tenant databases (root user or klai_readonly role).
    # Required for GET /internal/v1/users/{librechat_user_id}/feature/knowledge.
    librechat_mongo_root_uri: str = ""

    # klai-connector integration (used by portal → klai-connector for sync orchestration)
    klai_connector_url: str = "http://klai-connector:8200"
    klai_connector_secret: str = ""  # Shared internal secret; generate with: openssl rand -hex 32

    # Google Drive OAuth (SPEC-KB-025) — empty client_id disables the provider
    google_drive_client_id: str = ""
    google_drive_client_secret: str = ""

    # Microsoft 365 OAuth (SPEC-KB-MS-DOCS-001) — empty client_id disables the provider.
    # Azure AD app registered in the Klai-owned M365 tenant as a multi-tenant application
    # (same ownership model as ZITADEL_IDP_MICROSOFT_ID for social login).
    ms_docs_client_id: str = ""
    ms_docs_client_secret: str = ""
    ms_docs_tenant_id: str = "common"  # multi-tenant default; accepts any M365 tenant

    # Mock mode — disables real Moneybird calls for pre-launch testing
    mock_billing: bool = False
    frontend_url: str = ""  # e.g. http://localhost:5174 in dev; empty = same origin as API in prod

    # Knowledge ingest service (internal)
    knowledge_ingest_url: str = "http://knowledge-ingest:8000"
    knowledge_ingest_secret: str = ""  # PORTAL_API_KNOWLEDGE_INGEST_SECRET

    # crawl4ai HTTP service — used by the URL source extractor (SPEC-KB-SOURCES-001).
    # Same endpoint that klai-knowledge-ingest and klai-connector already target.
    crawl4ai_api_url: str = "http://crawl4ai:11235"

    # Redis (used for retrieval logs and feedback idempotency -- SPEC-KB-015)
    redis_url: str = ""

    # Knowledge / Qdrant
    qdrant_url: str = "http://qdrant:6333"
    qdrant_collection: str = "klai_knowledge"
    qdrant_api_key: str = ""

    # Vexa meeting API (agentic-runtime)
    vexa_meeting_api_url: str = "http://vexa-meeting-api:8080"
    # @MX:NOTE: reserved for Vexa admin API calls (tenant provisioning, quota inspection).
    # Stored in SOPS + compose; no runtime reader yet. Keep until admin surface lands.
    vexa_admin_token: str = ""
    vexa_api_key: str = ""
    vexa_webhook_secret: str = ""

    # LiteLLM (for summarization)
    litellm_base_url: str = "http://litellm:4000"
    extraction_model: str = "klai-fast"
    synthesis_model: str = "klai-primary"

    # Knowledge gap thresholds (mirror of LiteLLM hook env vars for re-scoring)
    klai_gap_soft_threshold: float = 0.4
    klai_gap_dense_threshold: float = 0.35

    # Knowledge retrieval API (for gap re-scoring)
    knowledge_retrieve_url: str = ""  # e.g. http://retrieval-api:8000

    # GitHub — for org member removal during offboarding (A.6.5)
    # PAT requires admin:org scope; stored in SOPS as GITHUB_ADMIN_PAT
    github_admin_pat: str = ""
    github_org: str = "GetKlai"

    # Whisper server (internal -- for direct post-meeting transcription)
    whisper_server_url: str = "http://whisper-server:8000"

    # Dev mode — enables Swagger UI and /openapi.json; NEVER enable in production.
    # Gated on portal_env in app.main (SPEC-SEC-HYGIENE-001 REQ-28.1) and at
    # Settings construction (REQ-28.3 — see _no_debug_in_production validator).
    debug: bool = False

    # SPEC-SEC-HYGIENE-001 REQ-28.2: explicit deployment-environment marker.
    # Conservative default "production" so an unset env var on a fresh deploy
    # does NOT expose /docs by accident. Accepts: "development" | "staging"
    # | "production". Local-dev .env sets PORTAL_ENV=development.
    portal_env: str = "production"

    # Auth dev mode — bypasses Zitadel authentication for local development.
    # REQUIRES debug=True as additional safeguard. NEVER enable in production.
    # When enabled, all Bearer tokens are accepted and mapped to auth_dev_user_id.
    auth_dev_mode: bool = False
    auth_dev_user_id: str = ""  # Zitadel user ID of a real user in the local portal_users table

    # IMAP calendar invite listener
    imap_host: str | None = None
    imap_port: int = 993
    imap_username: str | None = None
    imap_password: str | None = None
    imap_poll_interval_seconds: int = 60
    invite_bot_rate_limit_per_user_per_day: int = 10

    # SPEC-SEC-IMAP-001: mail-auth (DKIM/SPF/ARC) enforcement on the listener.
    # `imap_authserv_id` is the authserv-id stamped into Authentication-Results
    # by the trusted upstream relay. Only auth-results from that authserv-id
    # are consulted; sender-injected headers are ignored. The default matches
    # the cloud86 hosting relay observed in production headers (April 2026);
    # override via PORTAL_API_IMAP_AUTHSERV_ID after a hosting-provider change.
    imap_authserv_id: str = "shared199.cloud86-host.io"
    # Per-message wall-clock timeout for the synchronous DKIM/ARC verify call
    # (runs in `asyncio.to_thread` with `asyncio.wait_for`).
    imap_auth_timeout_seconds: float = 5.0
    # Allowlist of ARC sealing domains (`d=` of the outermost valid ARC-Seal)
    # whose `ARC-Authentication-Results` we accept when direct DKIM alignment
    # is broken — legitimate forwarded mail. `getklai.com` is in this list
    # because the operator-controlled mail.getklai.com MX seals every inbound
    # invite, and that seal is the trusted boundary for our IMAP ingress.
    imap_trusted_arc_sealers: list[str] = [
        "getklai.com",
        "google.com",
        "outlook.com",
        "icloud.com",
        "fastmail.com",
        "protonmail.ch",
    ]

    # Widget JWT secret (SPEC-WIDGET-001)
    # Generate with: openssl rand -hex 32
    # When empty, widget endpoints return 503.
    widget_jwt_secret: str = ""  # PORTAL_API_WIDGET_JWT_SECRET

    # CORS — explicit allowlist of trusted origins for credentialed requests.
    # SPEC-SEC-CORS-001 REQ-1.6: cors_allow_origin_regex removed. The runtime
    # CORS check is the fixed first-party regex in KlaiCORSMiddleware (REQ-1.2)
    # UNION this comma-separated list. Add explicit origins here for dev/staging
    # (e.g. http://localhost:5174). Do NOT add wildcard patterns.
    cors_origins: str = "http://localhost:5174"

    @property
    def portal_url(self) -> str:
        """Base URL of the portal (SPA + API proxy). Used for OAuth callback URLs."""
        return self.frontend_url or f"https://portal.{self.domain}"

    @property
    def is_auth_dev_mode(self) -> bool:
        """True only when BOTH debug and auth_dev_mode are enabled."""
        return self.debug and self.auth_dev_mode

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",")]

    @model_validator(mode="after")
    def _require_vexa_webhook_secret(self) -> "Settings":
        """SEC-013 F-033: fail-closed on missing vexa_webhook_secret.

        Vexa integration is active (SPEC-VEXA-003 rolled out). An empty/
        whitespace-only secret silently disabled auth on /api/bots/internal/webhook
        before this validator — same class of bug as F-003/F-012. Fail fast at
        startup rather than accept un-authenticated webhooks.
        """
        if not self.vexa_webhook_secret or not self.vexa_webhook_secret.strip():
            raise ValueError(
                "Missing required: VEXA_WEBHOOK_SECRET (SEC-013 F-033). Set it in SOPS before starting portal-api."
            )
        return self

    @model_validator(mode="after")
    def _require_moneybird_webhook_token(self) -> "Settings":
        """SPEC-SEC-WEBHOOK-001 REQ-3: fail-closed on missing moneybird_webhook_token.

        Moneybird webhooks flip `PortalOrg.billing_status` between active, cancelled
        and payment_failed. Before this validator, an empty/whitespace-only token
        made the signature check at /api/webhooks/moneybird optional (guarded by
        `if settings.moneybird_webhook_token:`) — any unauthenticated POST could
        mutate billing state. Fail fast at startup rather than ship a silent
        fail-open. Same pattern as _require_vexa_webhook_secret above.

        If Moneybird webhook processing must be disabled, unregister the router
        instead of emptying the secret (see SPEC-SEC-WEBHOOK-001 REQ-3.3).

        Env-parity: MONEYBIRD_WEBHOOK_TOKEN must exist in
        klai-infra/core-01/.env.sops BEFORE this validator lands (see pitfall
        `validator-env-parity` in .claude/rules/klai/pitfalls/process-rules.md).
        """
        if not self.moneybird_webhook_token or not self.moneybird_webhook_token.strip():
            raise ValueError(
                "Missing required: MONEYBIRD_WEBHOOK_TOKEN (SPEC-SEC-WEBHOOK-001 REQ-3). "
                "Set it in SOPS before starting portal-api, or unregister the Moneybird router."
            )
        return self

    @model_validator(mode="after")
    def _no_debug_in_production(self) -> "Settings":
        """SPEC-SEC-HYGIENE-001 REQ-28.3: refuse to boot when DEBUG=true and
        PORTAL_ENV=production.

        DEBUG=true exposes Swagger UI and OpenAPI surface, and also enables
        `auth_dev_mode` (which bypasses Zitadel) when set together. The soft
        gate at ``should_expose_docs`` below (REQ-28.1) is the runtime
        fallback; this validator is the hard guard that prevents the
        catastrophic combo from ever booting. The (debug=True,
        portal_env="production") pairing is unambiguously a misconfiguration
        — there is no legitimate reason to ship a production deployment with
        Swagger exposed.

        Env-parity (see pitfall `validator-env-parity`): both PORTAL_ENV and
        DEBUG default to safe values ("production" and False respectively),
        so this validator NEVER fires on a missing env var — only on the
        explicit catastrophic pairing. No klai-infra/core-01/.env.sops
        change is required for this validator to land.
        """
        if self.debug and self.portal_env == "production":
            raise ValueError(
                "DEBUG=true is forbidden when PORTAL_ENV=production "
                "(SPEC-SEC-HYGIENE-001 REQ-28.3). Either set PORTAL_ENV "
                "to 'development' or 'staging' for the deployment that "
                "needs Swagger UI, or unset DEBUG."
            )
        return self

    @model_validator(mode="after")
    def _require_imap_authserv_id_when_listener_enabled(self) -> "Settings":
        """SPEC-SEC-IMAP-001: when the IMAP listener is enabled, the upstream
        relay's authserv-id MUST be explicitly set.

        An empty value silently breaks SPF observability (REQ-2.1), and a
        wrong default (e.g. left over from an earlier hosting provider after
        a migration) leaves the SPF check searching for a header that the
        new relay never stamps. This validator catches the explicit-empty
        case at startup; operators must still review the default after any
        mail-host change to avoid silent default rot.

        IMAP is considered enabled iff both ``imap_host`` and ``imap_username``
        are set — matches the assertion in
        :func:`app.services.imap_listener._poll_once`.
        """
        listener_enabled = bool(self.imap_host) and bool(self.imap_username)
        if listener_enabled and not (self.imap_authserv_id and self.imap_authserv_id.strip()):
            raise ValueError(
                "Missing required: PORTAL_API_IMAP_AUTHSERV_ID (SPEC-SEC-IMAP-001). "
                "Set it to the authserv-id stamped by your trusted upstream mail relay; "
                "inspect Authentication-Results headers in a recent message at "
                "meet@getklai.com to find the correct value."
            )
        return self


def should_expose_docs(s: object) -> bool:
    """SPEC-SEC-HYGIENE-001 REQ-28.1: dual-gate `/docs` and `/openapi.json`.

    Soft fallback that matches the validator at REQ-28.3: only expose
    when DEBUG is on AND we are not running with PORTAL_ENV=production.
    The validator refuses to boot the app at all in the catastrophic
    combination, so this gate fires only on its own when the validator
    is bypassed (e.g. monkey-patched in a test).

    Lives in this module (not in app/main.py) so tests can import it
    without triggering ``setup_logging("portal-api")`` at module-load
    time — the import side-effect that breaks
    ``tests/test_cors_allowlist.py``'s ``structlog.configure``-based
    capture (same trap that ``tests/test_startup_sso_key_guard.py``
    documents for the SSO lifespan check).
    """
    return bool(getattr(s, "debug", False)) and getattr(s, "portal_env", "production") != "production"


settings = Settings()  # type: ignore[call-arg]  # pydantic-settings reads required fields from env
