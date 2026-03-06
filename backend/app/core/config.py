from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Zitadel
    zitadel_base_url: str = "https://auth.getklai.com"
    zitadel_pat: str  # PORTAL_API_ZITADEL_PAT — never exposed to frontend
    zitadel_project_id: str = "362771533686374406"

    # Database
    database_url: str  # asyncpg DSN: postgresql+asyncpg://...

    # CORS origins (comma-separated)
    cors_origins: str = "http://localhost:5174,https://my.getklai.com"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",")]


settings = Settings()
