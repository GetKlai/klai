from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # SMTP — copy values from your mail provider
    smtp_host: str
    smtp_port: int = 587
    smtp_username: str
    smtp_password: str
    smtp_from: str = "noreply@example.com"
    smtp_from_name: str = "Klai"
    smtp_tls: bool = True          # STARTTLS on port 587
    smtp_ssl: bool = False         # Implicit TLS on port 465

    # Security — shared secret between Zitadel and this service.
    # Empty / whitespace-only values are rejected at startup (REQ-9.1).
    webhook_secret: str

    # Branding
    logo_url: str = "https://www.example.com/klai-logo.png"
    logo_width: int = 61
    brand_url: str = "https://www.example.com"

    # Theme
    theme_dir: str = "theme"

    # Portal internal API — used to look up user's preferred language for email links
    # URL of the klai-portal-api container (reachable via Docker network klai-net)
    portal_api_url: str = "http://portal-api:8010"
    # Shared secret — must match INTERNAL_SECRET in the portal .env
    portal_internal_secret: str = ""

    # Internal service-to-service secret for /internal/send (portal-api → mailer).
    # Empty / whitespace-only values are rejected at startup (REQ-9.2).
    internal_secret: str

    # Set DEBUG=true to enable /debug endpoint (logs raw Zitadel payloads)
    debug: bool = False

    # @MX:NOTE: required validator — empty secret is never a valid runtime state.
    # Mirrors SPEC-SEC-WEBHOOK-001 REQ-9 (_require_vexa_webhook_secret in
    # klai-portal/backend/app/core/config.py). Keeps the service from booting
    # with a silently-broken HMAC path.
    @field_validator("webhook_secret", mode="after")
    @classmethod
    def _require_webhook_secret(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Missing required: WEBHOOK_SECRET")
        return v

    @field_validator("internal_secret", mode="after")
    @classmethod
    def _require_internal_secret(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Missing required: INTERNAL_SECRET")
        return v


settings = Settings()
