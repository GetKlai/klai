from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_ENV_FILE = Path(__file__).parent.parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_file_encoding="utf-8")

    # Zitadel
    zitadel_base_url: str = "https://auth.getklai.com"
    zitadel_pat: str  # PORTAL_API_ZITADEL_PAT — never exposed to frontend
    zitadel_project_id: str = "362771533686374406"
    zitadel_org_id: str = ""
    zitadel_portal_app_id: str = "362901948573155339"  # "Klai Portal" OIDC app
    zitadel_portal_org_id: str = "362757920133283846"  # Org where all portal users live

    # Database
    database_url: str  # asyncpg DSN: postgresql+asyncpg://...

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

    # Domain
    domain: str = "getklai.com"

    # SSO cookie encryption (Fernet key)
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    sso_cookie_key: str  # PORTAL_API_SSO_COOKIE_KEY
    sso_cookie_max_age: int = 86400  # 24 hours; Zitadel session lifetime is the real authority

    # Secrets passed to new LibreChat containers (read from /opt/klai/.env)
    mongo_root_password: str = ""
    meili_master_key: str = ""
    litellm_master_key: str = ""
    redis_password: str = ""
    firecrawl_internal_key: str = ""  # FIRECRAWL_INTERNAL_KEY — shared web search API key

    # Provisioning paths (container-internal paths, mounted from host)
    caddy_tenants_path: str = "/caddy/tenants"  # per-tenant .caddyfile dir (caddy-tenants volume)
    librechat_container_data_path: str = "/librechat"  # base dir for per-tenant librechat files
    librechat_host_data_path: str = "/opt/klai/librechat"  # HOST path for Docker volume mounts
    librechat_image: str = "ghcr.io/danny-avila/librechat:v0.8.3-rc2"  # pinned image for tenants
    caddy_container_name: str = "klai-core-caddy-1"  # Docker container name for Caddy reload

    # Internal service-to-service secret (used by klai-mailer → portal)
    # Generate with: openssl rand -hex 32
    internal_secret: str = ""

    # klai-docs internal secret (used by portal → klai-docs for KB provisioning)
    docs_internal_secret: str = ""

    # Mock mode — disables real Moneybird calls for pre-launch testing
    mock_billing: bool = False
    frontend_url: str = ""  # e.g. http://localhost:5174 in dev; empty = same origin as API in prod

    # Knowledge / Qdrant
    qdrant_url: str = "http://qdrant:6333"
    qdrant_api_key: str = ""

    # Vexa meeting bot manager
    vexa_bot_manager_url: str = "http://vexa-bot-manager:8056"
    vexa_api_key: str = ""
    vexa_webhook_secret: str = ""

    # Vexa API-gateway (port 8123) — transcript segments with speaker labels
    vexa_api_gateway_url: str = "http://vexa-api-gateway:8123"

    # LiteLLM (for summarization)
    litellm_base_url: str = "http://litellm:4000"
    summarize_model: str = "klai-primary"

    # Whisper server (internal -- for direct post-meeting transcription)
    whisper_server_url: str = "http://whisper-server:8000"

    # Dev mode — enables Swagger UI and /openapi.json; NEVER enable in production
    debug: bool = False

    # CORS — static origins + wildcard regex for tenant subdomains
    cors_origins: str = "http://localhost:5174"
    cors_allow_origin_regex: str = r"https://[a-z0-9-]+\.getklai\.com"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",")]


settings = Settings()  # type: ignore[call-arg]  # pydantic-settings reads required fields from env
