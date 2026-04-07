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

## Qdrant org_id vs PostgreSQL org_id are different types (CRIT)
Qdrant payload stores `org_id` as the **Zitadel string ID** (e.g. `"362757920133283846"`).
PostgreSQL portal DB uses the **integer surrogate key** (e.g. `1`).
Never use the integer PG `org_id` to filter or delete Qdrant records — it will silently match nothing or the wrong tenant.
Always verify which ID scope you are in before any cross-store delete or lookup.

## PostgreSQL artifact cascade delete order (CRIT)
When deleting artifacts by connector, this order is required to avoid FK violations:
1. Nullify `superseded_by` self-references in `artifacts` (self-referential FK)
2. Delete from `embedding_queue`
3. Delete from `artifact_entities`
4. Delete from `derivations` — join column is `child_id` / `parent_id`, NOT `source_artifact_id`
5. Delete from `artifacts`

Skipping step 1 or using the wrong column name on `derivations` causes FK constraint failures.

## structlog vs stdlib logging — keyword arguments (HIGH)
Services that use `structlog` must call logger methods with positional arguments only for the message, then keyword args for context. Stdlib `logging.Logger._log()` does NOT accept arbitrary keyword arguments — passing structlog-style kwargs (e.g. `logger.info("msg", path=x)`) raises `TypeError: _log() got unexpected keyword argument`.

Fix: use positional string interpolation for stdlib loggers:
```python
logger.info("msg (path=%s)", ref.path)
```
Or switch the module to use `structlog.get_logger()` throughout.
