from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

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
    zitadel_project_id: str = ""
    zitadel_org_id: str = ""
    zitadel_portal_org_id: str = ""  # Org where all portal users live (ZITADEL_PORTAL_ORG_ID)
    zitadel_portal_client_id: str = ""  # OIDC client_id for BFF code exchange (ZITADEL_PORTAL_CLIENT_ID)
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
    # SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-10 removed the historical
    # ``bff_session_key or sso_cookie_key`` fallback. The key is now a
    # required, fail-closed setting; the validator at the bottom of this
    # class refuses to start the app on an empty value.
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
    litellm_general_chat_key: str = ""  # Dedicated LiteLLM virtual key for Partner OpenAI-compatible chat.
    litellm_analytics_database_url: str = ""  # Optional RO DB URL for platform usage analytics.
    redis_password: str = ""
    redis_host: str = "redis"
    redis_port: int = 6379
    firecrawl_internal_key: str = ""  # FIRECRAWL_INTERNAL_KEY — shared web search API key

    # Partner OpenAI-compatible chat guardrails. These are intentionally lower
    # than the generic Partner API RPM because this route exposes paid model
    # capacity without knowledge grounding.
    partner_openai_rpm_limit: int = 10
    partner_openai_tpm_limit: int = 60_000
    partner_openai_max_input_tokens: int = 16_000

    # Provisioning paths (container-internal paths, mounted from host)
    caddy_tenants_path: str = "/caddy/tenants"  # per-tenant .caddyfile dir (caddy-tenants volume)
    librechat_container_data_path: str = "/librechat"  # base dir for per-tenant librechat files
    librechat_host_data_path: str = "/opt/klai/librechat"  # HOST path for Docker volume mounts
    librechat_image: str = "ghcr.io/danny-avila/librechat:v0.8.6"
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
    google_drive_picker_api_key: str = ""
    google_drive_picker_app_id: str = ""

    # Microsoft 365 OAuth (SPEC-KB-MS-DOCS-001) — empty client_id disables the provider.
    # Azure AD app registered in the Klai-owned M365 tenant as a multi-tenant application
    # (same ownership model as ZITADEL_IDP_MICROSOFT_ID for social login).
    ms_docs_client_id: str = ""
    ms_docs_client_secret: str = ""
    ms_docs_tenant_id: str = "common"  # multi-tenant default; accepts any M365 tenant

    # HubSpot Custom Channel for internal Klai webchat support fallback.
    # Empty client/refresh token disables the admin lifecycle API while keeping
    # the read-only status endpoint available.
    hubspot_webchat_client_id: str = ""
    hubspot_webchat_client_secret: str = ""
    hubspot_webchat_refresh_token: str = ""
    hubspot_webchat_portal_id: str = "147785398"
    hubspot_webchat_app_id: str = "40776849"
    hubspot_webchat_custom_channel_id: str = "2930388"
    hubspot_webchat_inbox_id: str = "1364799639"
    hubspot_webchat_channel_account_name: str = "Klai Webchat Support"
    hubspot_webchat_delivery_identifier: str = "klai-webchat-support"
    hubspot_webchat_help_desk_url: str = "https://app.hubspot.com/help-desk/147785398/views/all/open"

    # SPEC-LAUNCH-SOFTLAUNCH-001 B-2: HMAC key for waitlist invite tokens.
    # Empty in dev/CI — feature degrades to "no bypass possible" rather than
    # crashing. Generate with: openssl rand -base64 48
    waitlist_token_key: str = ""  # PORTAL_API_WAITLIST_TOKEN_KEY

    # SPEC-LAUNCH-SOFTLAUNCH-001 B-2 sub-batch 3: Twenty CRM REST API.
    # The waitlist endpoint on the website (klai-website/src/pages/api/waitlist.ts)
    # creates the deal; portal-api polls Twenty for stage transitions and sends
    # the corresponding mail. Empty URL = poller is disabled.
    twenty_url: str = ""  # e.g. https://twenty.getklai.com (TWENTY_URL)
    twenty_api_key: str = ""  # TWENTY_API_KEY

    # listmonk mailing platform.
    # Empty URL/user/token disables mailing sync without breaking signup/auth
    # flows. List IDs are provisioned in listmonk and injected via SOPS.
    listmonk_url: str = ""  # e.g. http://listmonk:9000
    listmonk_api_user: str = ""
    listmonk_api_token: str = ""
    listmonk_list_crm_selected_id: int = 0
    listmonk_list_signups_id: int = 0
    listmonk_list_users_id: int = 0
    listmonk_list_updates_opt_in_id: int = 0
    listmonk_tx_onboarding_template_id: int = 5

    # Mock mode — disables real Moneybird calls for pre-launch testing
    mock_billing: bool = False
    frontend_url: str = ""  # e.g. http://localhost:5174 in dev; empty = same origin as API in prod

    # Knowledge ingest service (internal)
    knowledge_ingest_url: str = "http://knowledge-ingest:8000"
    knowledge_ingest_secret: str = ""  # PORTAL_API_KNOWLEDGE_INGEST_SECRET

    # klai-knowledge-mcp (internal). Used for the
    # SPEC-PORTAL-RBAC-REFACTOR-001 REQ-18 role-change notification — when an
    # admin changes a user's role, portal-api POSTs to
    # ``{knowledge_mcp_url}/internal/notify-role-change`` so the MCP server
    # can fan out ``notifications/tools/list_changed`` to that user's active
    # MCP sessions. Empty = feature disabled (notification is fire-and-forget,
    # so an empty URL silently no-ops the role-change refresh; the next
    # tools/list poll picks up the change anyway).
    knowledge_mcp_url: str = "http://klai-knowledge-mcp:8080"

    # crawl4ai HTTP service — used by the URL source extractor (SPEC-KB-SOURCES-001).
    # Same endpoint that klai-knowledge-ingest and klai-connector already target.
    crawl4ai_api_url: str = "http://crawl4ai:11235"

    # docling-serve HTTP service — used by the file-upload binary path
    # (SPEC-KB-FILE-UPLOAD-001). Internal-only on klai-net; no SSRF risk
    # because the URL is config-pinned, not user-supplied.
    docling_url: str = "http://docling-serve:5001"

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

    # SearXNG (self-hosted web search, same instance the chat surfaces use).
    # Default is the internal container address; not a secret, so a plain
    # default is safe even when the env var is unset.
    searxng_url: str = "http://searxng:8080"

    # LiteLLM (for summarization)
    litellm_base_url: str = "http://litellm:4000"
    extraction_model: str = "klai-fast"
    synthesis_model: str = "klai-primary"
    feedback_triage_model: str = "klai-fast"

    # SPEC-INFRA-TENANT-DELETE-001: Garage S3 for Scribe artifact deletion.
    # Generate credentials via: garage key new --name portal-api
    scribe_api_url: str = "http://scribe-api:8020"
    garage_s3_endpoint: str = ""  # e.g. http://garage:3900
    garage_s3_access_key: str = ""
    garage_s3_secret_key: str = ""
    garage_s3_bucket: str = "klai-scribe"  # default bucket name for scribe artifacts
    garage_kb_bucket: str = "klai-images"  # SPEC-TI-009: bucket for KB images (auth-proxied via portal-api)

    # SPEC-INFRA-TENANT-DELETE-001 R1: platform-org slug guard for admin deprovision endpoint.
    # The DELETE /api/admin/orgs/{slug}/deprovision endpoint requires the caller to be
    # a member of the platform org. Override via PORTAL_API_PLATFORM_ORG_SLUG.
    platform_org_slug: str = "getklai"

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

    # REQ-8 (SPEC-SEC-CROSS-TENANT-FOLLOWUP-001): widget_messages retention.
    # Rows older than this many days are deleted daily by the background loop.
    widget_messages_retention_days: int = 90  # PORTAL_API_WIDGET_MESSAGES_RETENTION_DAYS

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

    # ─── SPEC-MCP-AUTH-001: OAuth 2.1 authorization-server config ─────────
    # Issuer base URL — must equal the canonical portal-login host (currently
    # https://my.getklai.com per servers.md). Drives /.well-known/oauth-
    # authorization-server response and `iss` claim shape.
    mcp_oauth_issuer_base_url: str = ""  # MCP_OAUTH_ISSUER_BASE_URL
    # Canonical resource URI of the protected MCP server. RFC 8707 audience
    # binding. Tokens are issued for exactly this resource and validated
    # case-sensitively by knowledge-mcp.
    mcp_oauth_resource_url: str = ""  # MCP_OAUTH_RESOURCE_URL
    # Access-token TTL in days. 30 default — short enough for revoke-bound
    # safety, long enough to keep refresh frequency low for power-users.
    mcp_oauth_token_ttl_days: int = 30
    # Refresh-token TTL in days. 90 default — drives the silent-reconnect
    # cadence in Claude Desktop & Cursor.
    mcp_oauth_refresh_ttl_days: int = 90
    # Per-IP rate-limit on the DCR endpoint (POST /oauth/register).
    # Claude.ai re-registers a client on every fresh connection (DCR is
    # spec'd that way), so a single user iterating on connector setup can
    # easily blow past 10/h. Default 60/h; tunable via env.
    mcp_oauth_dcr_rate_limit_per_hour: int = 60

    @model_validator(mode="after")
    def _require_mcp_oauth_urls(self) -> "Settings":
        """SPEC-MCP-AUTH-001: fail-closed on empty issuer / resource URL.

        Both URLs are functionally required for the OAuth surface to operate.
        An empty value at boot would cause /.well-known endpoints to return
        broken JSON and audience-validation to silently succeed for any
        token. Fail fast at startup — same pattern as the other auth-class
        secrets (secret-fail-closed-on-empty rule in portal-security-auth.md).
        """
        if not self.mcp_oauth_issuer_base_url.strip():
            raise ValueError("mcp_oauth_issuer_base_url must be non-empty (SPEC-MCP-AUTH-001 REQ-7)")
        if not self.mcp_oauth_resource_url.strip():
            raise ValueError("mcp_oauth_resource_url must be non-empty (SPEC-MCP-AUTH-001 REQ-8 / REQ-14)")
        if self.mcp_oauth_token_ttl_days < 1:
            raise ValueError("mcp_oauth_token_ttl_days must be >= 1")
        if self.mcp_oauth_refresh_ttl_days < self.mcp_oauth_token_ttl_days:
            raise ValueError("mcp_oauth_refresh_ttl_days must be >= mcp_oauth_token_ttl_days")
        if self.mcp_oauth_dcr_rate_limit_per_hour < 1:
            raise ValueError("mcp_oauth_dcr_rate_limit_per_hour must be >= 1")
        return self

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
        gate at app.main._should_expose_docs (REQ-28.1) is the runtime fallback;
        this validator is the hard guard that prevents the catastrophic combo
        from ever booting. The (debug=True, portal_env="production") pairing
        is unambiguously a misconfiguration — there is no legitimate reason
        to ship a production deployment with Swagger exposed.

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
    def _validate_frontend_url_host(self) -> "Settings":
        """SPEC-CODEBASE-AUDIT-001 Adversarial Finding 3+4: validate frontend_url
        hostname against a trusted allowlist.

        Empty frontend_url is allowed (falls back to f"https://portal.{domain}").
        When set, the URL hostname MUST be one of:
          - "localhost" / "127.0.0.1" / "::1" (any env, for dev/canary)
          - The configured `domain` itself (e.g. "getklai.com")
          - Any subdomain of the configured `domain` (e.g. "my.getklai.com")

        In production (`portal_env == "production"`) the scheme MUST be https.

        Why: `frontend_url` is read by `portal_url` property and used to construct
        OAuth `redirect_uri` values (oauth.py:163-165) plus post-OAuth browser
        redirects. SOPS drift to e.g. `https://attacker.example` would silently
        redirect OAuth codes to an attacker-controlled host. With a Microsoft
        Graph app-registration that allows wildcard subdomain matches (and some
        do), this is directly exploitable for token capture.

        Env-parity (see pitfall `validator-env-parity`): `frontend_url` defaults
        to "" (empty), so this validator NEVER fires on a missing env var — only
        on a non-empty misconfigured value. No klai-infra/core-01/.env.sops
        change is required for this validator to land.
        """
        # Normalise whitespace-only values to empty string. Without this
        # the validator would early-return with a "" + "   " → portal_url
        # property returns the whitespace string (since `or` treats it as
        # truthy), breaking OAuth redirect_uri construction silently.
        # Audit 2026-05-05 finding 7. Tests cover both empty and whitespace
        # cases assert portal_url falls back to https://portal.{domain}.
        if not self.frontend_url or not self.frontend_url.strip():
            self.frontend_url = ""
            return self

        parsed = urlparse(self.frontend_url.strip())
        if parsed.scheme not in {"http", "https"}:
            raise ValueError(
                f"FRONTEND_URL must use scheme http or https; got '{parsed.scheme}' in '{self.frontend_url}'"
            )
        host = (parsed.hostname or "").lower()
        if not host:
            raise ValueError(f"FRONTEND_URL must contain a hostname; got '{self.frontend_url}'")

        # Allow localhost in any env (dev/test/canary)
        if host in {"localhost", "127.0.0.1", "::1"}:
            return self

        # Allow exact domain or any subdomain of self.domain
        domain = self.domain.lower().lstrip(".")
        if host == domain or host.endswith(f".{domain}"):
            if self.portal_env == "production" and parsed.scheme != "https":
                raise ValueError(
                    f"FRONTEND_URL must use https in production; got '{parsed.scheme}' in '{self.frontend_url}'"
                )
            return self

        raise ValueError(
            f"FRONTEND_URL hostname '{host}' is not in the trusted allowlist. "
            f"Must be 'localhost', '{domain}', or a subdomain of '{domain}'. "
            f"This protects OAuth redirect_uri integrity "
            f"(SPEC-CODEBASE-AUDIT-001 Adversarial Findings 3+4)."
        )

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

    # ------------------------------------------------------------------
    # SPEC-SEC-VALIDATOR-COVERAGE-001 -- fail-closed validators (REQ-1..10)
    # All 10 env vars verified present in /opt/klai/.env on 2026-05-05.
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def _require_internal_secret(self) -> "Settings":
        """SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-1: fail-closed on missing INTERNAL_SECRET.

        Cross-service trust boundary: klai-mailer -> portal-api (and other callers
        of /internal/*) use this as a shared Bearer token. An empty value causes
        the HMAC comparison to accept any caller that also sends an empty token.

        Where used: app/api/internal.py -- X-Internal-Secret / Authorization header.
        Failure mode without validator: any unauthenticated caller reaches internal
        endpoints (rate-limit bypass, data exfiltration via /internal/v1/users/*).

        Env-parity: INTERNAL_SECRET must exist in klai-infra/core-01/.env.sops BEFORE merge.
        Pre-flight verified 2026-05-05: value populated in /opt/klai/.env.
        """
        if not self.internal_secret or not self.internal_secret.strip():
            raise ValueError(
                "Missing required: INTERNAL_SECRET (SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-1). "
                "Set it in SOPS before starting portal-api."
            )
        return self

    @model_validator(mode="after")
    def _require_klai_connector_secret(self) -> "Settings":
        """SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-2: fail-closed on missing KLAI_CONNECTOR_SECRET.

        Cross-service trust boundary: portal-api -> klai-connector (sync orchestration).
        An empty secret silently disables auth on klai-connector/internal/* endpoints,
        letting any caller trigger or inspect sync runs.

        Where used: app/services/klai_connector_client.py -- Authorization: Bearer header.
        Failure mode without validator: connector sync endpoints accept unauthenticated
        requests (empty Bearer = empty expected secret = hmac.compare_digest passes).

        Env-parity: KLAI_CONNECTOR_SECRET must exist in klai-infra/core-01/.env.sops BEFORE merge.
        Pre-flight verified 2026-05-05: value populated in /opt/klai/.env.
        """
        if not self.klai_connector_secret or not self.klai_connector_secret.strip():
            raise ValueError(
                "Missing required: KLAI_CONNECTOR_SECRET (SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-2). "
                "Set it in SOPS before starting portal-api."
            )
        return self

    @model_validator(mode="after")
    def _require_knowledge_ingest_secret(self) -> "Settings":
        """SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-3: fail-closed on missing KNOWLEDGE_INGEST_SECRET.

        Cross-service trust boundary: portal-api -> knowledge-ingest via X-Internal-Secret.
        An empty value silently removes auth, allowing any caller to trigger ingestion
        jobs or list knowledge items on behalf of arbitrary tenants.

        Where used: app/services/knowledge_ingest_client.py -- X-Internal-Secret header.
        Failure mode without validator: empty-secret fail-open (see pitfall empty-secret-fail-open).

        Env-parity: KNOWLEDGE_INGEST_SECRET must exist in klai-infra/core-01/.env.sops BEFORE merge.
        Pre-flight verified 2026-05-05: value populated in /opt/klai/.env.
        """
        if not self.knowledge_ingest_secret or not self.knowledge_ingest_secret.strip():
            raise ValueError(
                "Missing required: KNOWLEDGE_INGEST_SECRET (SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-3). "
                "Set it in SOPS before starting portal-api."
            )
        return self

    @model_validator(mode="after")
    def _require_retrieval_api_internal_secret(self) -> "Settings":
        """SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-4: fail-closed on missing RETRIEVAL_API_INTERNAL_SECRET.

        Cross-service trust boundary: portal-api -> retrieval-api, kept separate from
        internal_secret so both boundaries can be rotated independently (SPEC-SEC-010 REQ-6.1).
        An empty value allows unauthenticated callers to reach retrieval endpoints.

        Where used: app/api/knowledge_gap.py and related -- X-Internal-Secret header.
        Failure mode without validator: retrieval-api accepts any caller sending an empty secret.

        Env-parity: RETRIEVAL_API_INTERNAL_SECRET must exist in klai-infra/core-01/.env.sops BEFORE merge.
        Pre-flight verified 2026-05-05: value populated in /opt/klai/.env.
        """
        if not self.retrieval_api_internal_secret or not self.retrieval_api_internal_secret.strip():
            raise ValueError(
                "Missing required: RETRIEVAL_API_INTERNAL_SECRET (SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-4). "
                "Set it in SOPS before starting portal-api."
            )
        return self

    @model_validator(mode="after")
    def _require_docs_internal_secret(self) -> "Settings":
        """SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-5: fail-closed on missing DOCS_INTERNAL_SECRET.

        Cross-service trust boundary: portal-api -> klai-docs for KB provisioning.
        An empty value silently disables auth, letting any caller provision or
        deprovision knowledge-base resources for arbitrary tenants.

        Where used: app/services/docs_client.py (or equivalent) -- X-Internal-Secret header.
        Failure mode without validator: docs endpoint accepts empty secret, bypassing
        tenant-scoped access control.

        Env-parity: DOCS_INTERNAL_SECRET must exist in klai-infra/core-01/.env.sops BEFORE merge.
        Pre-flight verified 2026-05-05: value populated in /opt/klai/.env.
        """
        if not self.docs_internal_secret or not self.docs_internal_secret.strip():
            raise ValueError(
                "Missing required: DOCS_INTERNAL_SECRET (SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-5). "
                "Set it in SOPS before starting portal-api."
            )
        return self

    @model_validator(mode="after")
    def _require_zitadel_portal_client_secret(self) -> "Settings":
        """SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-6: fail-closed on missing ZITADEL_PORTAL_CLIENT_SECRET.

        Cross-service trust boundary: portal-api <-> Zitadel BFF code-exchange (SPEC-AUTH-008).
        This is the confidential client secret used to exchange an authorization code for
        tokens. An empty value causes every login to fail at token exchange (auth outage).

        Where used: app/api/auth.py -- /api/auth/oidc/callback BFF code-exchange.
        Failure mode without validator: portal-api starts but every login fails at token exchange.

        Env-parity: ZITADEL_PORTAL_CLIENT_SECRET must exist in klai-infra/core-01/.env.sops BEFORE merge.
        Pre-flight verified 2026-05-05: value populated in /opt/klai/.env.
        """
        if not self.zitadel_portal_client_secret or not self.zitadel_portal_client_secret.strip():
            raise ValueError(
                "Missing required: ZITADEL_PORTAL_CLIENT_SECRET (SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-6). "
                "Set it in SOPS before starting portal-api."
            )
        return self

    @model_validator(mode="after")
    def _require_portal_secrets_key(self) -> "Settings":
        """SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-7: fail-closed on missing PORTAL_SECRETS_KEY.

        Encryption at rest: portal_secrets_key is the DEK used to encrypt tenant
        secrets (e.g. zitadel_librechat_client_secret, litellm_team_key) stored
        in the database. An empty key causes decryption of all at-rest tenant
        secrets to fail, breaking every multi-tenant operation.

        Where used: app/utils/crypto.py (or equivalent) -- AES-GCM encrypt/decrypt of tenant secrets.
        Failure mode without validator: portal-api starts but all tenant-secret reads
        return decryption errors; or encrypts with a known-weak/empty key.

        Env-parity: PORTAL_SECRETS_KEY must exist in klai-infra/core-01/.env.sops BEFORE merge.
        Pre-flight verified 2026-05-05: value populated in /opt/klai/.env.
        """
        if not self.portal_secrets_key or not self.portal_secrets_key.strip():
            raise ValueError(
                "Missing required: PORTAL_SECRETS_KEY (SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-7). "
                "Set it in SOPS before starting portal-api."
            )
        return self

    @model_validator(mode="after")
    def _require_encryption_key(self) -> "Settings":
        """SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-8: fail-closed on missing ENCRYPTION_KEY.

        Encryption at rest: encryption_key is the KEK in the two-tier key hierarchy
        (SPEC-KB-020) used to encrypt connector OAuth credentials (access_token,
        refresh_token). An empty key causes connector auth to fail for every tenant.

        Where used: app/utils/crypto.py -- connector credential KEK encrypt/decrypt.
        Failure mode without validator: connector sync fails for all tenants with
        decryption errors, or credentials are stored/retrieved with a null key.

        Env-parity: ENCRYPTION_KEY must exist in klai-infra/core-01/.env.sops BEFORE merge.
        Pre-flight verified 2026-05-05: value populated in /opt/klai/.env.
        """
        if not self.encryption_key or not self.encryption_key.strip():
            raise ValueError(
                "Missing required: ENCRYPTION_KEY (SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-8). "
                "Set it in SOPS before starting portal-api."
            )
        return self

    @model_validator(mode="after")
    def _require_sso_cookie_key(self) -> "Settings":
        """SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-9: fail-closed on missing SSO_COOKIE_KEY.

        SSO auth cookie integrity: sso_cookie_key is the Fernet key that encrypts and
        authenticates the SSO state cookie used in the OIDC login flow. An empty key
        allows arbitrary cookie forgery, enabling session hijacking for any user.

        Where used: app/api/auth.py::_get_sso_fernet -- SSO cookie encrypt/decrypt.
        Failure mode without validator: RuntimeError at first login (defensive
        _get_sso_fernet guard), or, if that guard is removed, unauthenticated cookie
        acceptance. This validator adds the fail-fast-at-startup layer.

        Env-parity: SSO_COOKIE_KEY must exist in klai-infra/core-01/.env.sops BEFORE merge.
        Pre-flight verified 2026-05-05: value populated in /opt/klai/.env.
        """
        if not self.sso_cookie_key or not self.sso_cookie_key.strip():
            raise ValueError(
                "Missing required: SSO_COOKIE_KEY (SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-9). "
                "Set it in SOPS before starting portal-api."
            )
        return self

    @model_validator(mode="after")
    def _require_bff_session_key(self) -> "Settings":
        """SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-10: fail-closed on missing BFF_SESSION_KEY.

        BFF session cookie integrity: bff_session_key is the Fernet key used to encrypt
        BFF session records at rest in Redis (SPEC-AUTH-008). An empty key allows
        arbitrary session forgery -- any attacker who can write to Redis can issue
        authenticated sessions for any user.

        Where used: app/api/auth.py -- BFF session encrypt/decrypt in Redis.
        Failure mode without validator: bff_session_key falls back to sso_cookie_key
        when unset (field comment). This validator closes that undocumented fallback
        path in production by making the misconfiguration explicit at startup.

        Env-parity: BFF_SESSION_KEY must exist in klai-infra/core-01/.env.sops BEFORE merge.
        Pre-flight verified 2026-05-05: value populated in /opt/klai/.env.
        """
        if not self.bff_session_key or not self.bff_session_key.strip():
            raise ValueError(
                "Missing required: BFF_SESSION_KEY (SPEC-SEC-VALIDATOR-COVERAGE-001 REQ-10). "
                "Set it in SOPS before starting portal-api."
            )
        return self

    @model_validator(mode="after")
    def _refuse_mock_billing_in_prod(self) -> "Settings":
        """SPEC-LAUNCH-SOFTLAUNCH-001 S-3: refuse mock_billing=True in production.

        billing.py:62 short-circuits the Moneybird flow and stamps every tenant
        as billing_status="active" when mock_billing is True. If MOCK_BILLING=1
        accidentally lands in /opt/klai/.env on core-01, every new signup gets
        unlimited paid access silently. Fail at startup rather than in prod.
        """
        if self.mock_billing and self.domain == "getklai.com":
            raise ValueError(
                "Refusing to boot: MOCK_BILLING=True is incompatible with domain=getklai.com "
                "(SPEC-LAUNCH-SOFTLAUNCH-001 S-3). Either unset MOCK_BILLING in /opt/klai/.env "
                "or use a non-production domain (dev/staging only)."
            )
        return self

    @model_validator(mode="after")
    def _require_zitadel_identity_ids(self) -> "Settings":
        """SPEC-REPO-SANITIZE-001 followup — fail-closed on missing Zitadel IDs.

        ce31a119 cleared the hardcoded fallback values for three identifiers:
        ``zitadel_project_id``, ``zitadel_portal_org_id`` and
        ``zitadel_portal_client_id``. These are public-but-leak-identifying
        IDs (not credentials, but they reveal production Zitadel structure).
        Sanitize's intent was "force explicit configuration" — i.e. they
        must be set via env var, never silently default to empty.

        Production incident 2026-05-13 16:03 UTC: signup broke for every
        new tenant because ``ZITADEL_PROJECT_ID`` and ``ZITADEL_PORTAL_ORG_ID``
        had no env injection path (no compose entry) → empty default
        reached runtime → ``zitadel.grant_user_role`` POSTed an empty
        ProjectId → Zitadel 500 → portal-api signup 502. The grant-user
        call sits BEFORE the portal_users INSERT, so the user was created
        in Zitadel but never landed in portal_users — three orphans
        accumulated in Zitadel before the root cause was identified.

        Fail-loud at startup means the next regression in this class
        (someone clears a SOPS entry, someone renames an env var, someone
        drops a compose injection line) surfaces in the deploy log instead
        of silently breaking signup hours later.

        Where used: app/services/zitadel.py — grant_user_role (PROJECT_ID),
        delete_org / list_org_users (PORTAL_ORG_ID), BFF auth.py
        (PORTAL_CLIENT_ID). None of these have non-empty fallbacks.

        Env parity: all three live in klai-infra/core-01/.env.sops as of
        the SOPS bump committed alongside this validator (commit f1b6249
        on klai-infra).
        """
        missing: list[str] = []
        if not self.zitadel_project_id.strip():
            missing.append("ZITADEL_PROJECT_ID")
        if not self.zitadel_portal_org_id.strip():
            missing.append("ZITADEL_PORTAL_ORG_ID")
        if not self.zitadel_portal_client_id.strip():
            missing.append("ZITADEL_PORTAL_CLIENT_ID")
        if missing:
            raise ValueError(
                "Missing required Zitadel identifiers in env: "
                + ", ".join(missing)
                + ". These were cleared from config.py defaults by "
                + "SPEC-REPO-SANITIZE-001 (ce31a119); they must come from "
                + "klai-infra SOPS. Without them, signup → grant_user_role "
                + "POSTs empty values and Zitadel returns 500. Update SOPS "
                + "(klai-infra/core-01/.env.sops) and re-run the sync-env "
                + "workflow before retrying the deploy."
            )
        return self


settings = Settings()  # type: ignore[call-arg]  # pydantic-settings reads required fields from env
