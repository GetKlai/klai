# Backend Patterns

## Stack (all Python services)
- **Framework:** FastAPI with async/await throughout
- **ORM:** SQLAlchemy 2.x (mapped_column, Mapped[] type annotations)
- **Migrations:** Alembic
- **DB:** PostgreSQL via asyncpg DSN (`postgresql+asyncpg://...`)
- **Config:** pydantic-settings (BaseSettings), reads from `.env` file
- **Linting:** ruff + black

## Service Structure Pattern
All Python services follow the same layout:
```
app/
  main.py          # FastAPI factory + lifespan
  api/             # Route handlers (routers)
  core/
    config.py      # Settings (pydantic-settings)
    database.py    # Engine/session setup
  models/          # SQLAlchemy models
  services/        # Business logic
  schemas/         # Pydantic request/response models
alembic/           # DB migrations
pyproject.toml
```

## Auth Pattern (portal)
- Zitadel validates tokens; startup lifespan validates the PAT on boot and crashes if invalid
- `PORTAL_API_ZITADEL_PAT` must never be corrupted (shell $ truncation risk — see server-secrets rule)
- SSO cookie encrypted with Fernet key (`PORTAL_API_SSO_COOKIE_KEY`)

## Connector Auth
- `AuthMiddleware` validates internal service-to-service calls (excludes /health)
- Connector secrets stored with AES-GCM encryption in Postgres via `PostgresSecretsStore`

## API Routers (klai-portal)
- `auth` — OIDC callbacks, SSO
- `signup` — new org registration
- `me` — current user info
- `admin` — org admin operations
- `billing` — Moneybird integration
- `knowledge` — Qdrant knowledge base
- `meetings` — Vexa meeting bots
- `webhooks` — Moneybird + Vexa webhooks
- `internal` — service-to-service (klai-mailer, klai-docs)

## Key External Integrations
- `services/zitadel.py` — user/org management via Zitadel API
- `services/vexa.py` — meeting bot lifecycle
- `services/moneybird.py` — invoicing + subscriptions
- `services/provisioning.py` — tenant container spin-up (Docker + Caddy + LibreChat)
