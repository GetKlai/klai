# SPEC-SEC-ENVFILE-SCOPE-001 — Migration Progress

## Phase 1 (merged, commit 1ff65d1b / d6fcdb7f / 58e11c30)

Services migrated from global `env_file: .env` to explicit `environment:` blocks:
- scribe-api
- retrieval-api
- victorialogs
- portal-api

Reverse-check post-deploy found three divergences (see `env-file-migration-reverse-check`
pitfall in `.claude/rules/klai/pitfalls/process-rules.md`):

- `VEXA_MEETING_API_URL` on portal-api: prod set `http://api-gateway:8000`, code default
  was `http://vexa-meeting-api:8080`. Fixed by explicit declaration.
- `GRAPHITI_LLM_MODEL` on retrieval-api: prod set `klai-pipeline`, code default was
  `klai-fast`. Fixed by explicit declaration.
- `VEXA_ADMIN_TOKEN` on portal-api: prod set a real token, code default was `""`. Fixed
  by explicit declaration.

---

## Phase 2 — SPEC-SEC-AUDIT-2026-04 B3 (2026-04-29)

Services migrated from per-service `env_file: ./klai-{svc}/.env` to explicit
`environment:` blocks: **klai-mailer** and **klai-connector**.

### Methodology

Applied `env-file-migration-reverse-check` protocol from process-rules.md:

1. Enumerated all pydantic `Settings` fields from each service's config module.
2. Identified fields with in-code defaults that the service-local `.env` might override.
3. Compared against compose environment block to find any divergences.

Since the service-local `.env` files (`/opt/klai/klai-mailer/.env` and
`/opt/klai/klai-connector/.env`) are generated from SOPS
(`klai-infra/core-01/klai-mailer/.env.sops`) and are not accessible from the repo,
the reverse-check was performed via static analysis: code default vs what would
reasonably differ in production.

---

### klai-mailer — env var inventory

| pydantic field | env var name | code default | compose declaration | divergence? |
|---|---|---|---|---|
| `smtp_host` | `SMTP_HOST` | required (no default) | `${SMTP_HOST}` | N/A |
| `smtp_port` | `SMTP_PORT` | 587 | `${SMTP_PORT:-587}` | no |
| `smtp_username` | `SMTP_USERNAME` | required | `${SMTP_USERNAME}` | N/A |
| `smtp_password` | `SMTP_PASSWORD` | required | `${SMTP_PASSWORD}` | N/A |
| `smtp_from` | `SMTP_FROM` | `noreply@example.com` | `${SMTP_FROM:-noreply@getklai.com}` | **YES** — prod uses real address |
| `smtp_from_name` | `SMTP_FROM_NAME` | `Klai` | `${SMTP_FROM_NAME:-Klai}` | no |
| `smtp_tls` | `SMTP_TLS` | `True` | `${SMTP_TLS:-true}` | no |
| `smtp_ssl` | `SMTP_SSL` | `False` | `${SMTP_SSL:-false}` | no |
| `webhook_secret` | `WEBHOOK_SECRET` | required (validator) | `${MAILER_WEBHOOK_SECRET}` | N/A |
| `logo_url` | `LOGO_URL` | `https://www.example.com/klai-logo.png` | `${LOGO_URL:-https://cdn.getklai.com/klai-logo.png}` | **YES** — prod uses real URL |
| `logo_width` | `LOGO_WIDTH` | 61 | `${LOGO_WIDTH:-61}` | no |
| `brand_url` | `BRAND_URL` | `https://www.example.com` | `${BRAND_URL:-https://www.getklai.com}` | **YES** — prod uses real URL |
| `theme_dir` | `THEME_DIR` | `theme` | `${THEME_DIR:-theme}` | no |
| `portal_api_url` | `PORTAL_API_URL` | `http://portal-api:8010` | `${MAILER_PORTAL_API_URL:-http://portal-api:8010}` | no |
| `portal_internal_secret` | `PORTAL_INTERNAL_SECRET` | `""` | `${PORTAL_API_INTERNAL_SECRET}` | no (was already in compose) |
| `internal_secret` | `INTERNAL_SECRET` | required (validator) | `${PORTAL_API_INTERNAL_SECRET}` | N/A (was already in compose) |
| `redis_url` | `REDIS_URL` | `redis://redis:6379/0` | `redis://:${REDIS_PASSWORD}@redis:6379/0` | **YES** — prod uses auth (was already in compose) |
| `mailer_rate_limit_per_recipient` | `MAILER_RATE_LIMIT_PER_RECIPIENT` | 10 | not declared (code default) | no |
| `mailer_rate_limit_window_seconds` | `MAILER_RATE_LIMIT_WINDOW_SECONDS` | 86400 | not declared (code default) | no |
| `debug` | `DEBUG` | `False` | `${MAILER_DEBUG:-false}` | no |
| `portal_env` | `PORTAL_ENV` | `development` | `${PORTAL_ENV:-production}` | **YES** — prod MUST be `production` to suppress /debug route |

**Divergences found: 4**
- `SMTP_FROM`: example.com default vs production real address → declared with `:-noreply@getklai.com`
- `LOGO_URL`: example.com default vs production CDN URL → declared with `:-https://cdn.getklai.com/klai-logo.png`
- `BRAND_URL`: example.com default vs production domain → declared with `:-https://www.getklai.com`
- `PORTAL_ENV`: `development` default vs `production` required → declared with `:-production` (CRITICAL — would expose /debug route in production if not set)

---

### klai-connector — env var inventory

| pydantic field | env var name | code default | compose declaration | divergence? |
|---|---|---|---|---|
| `database_url` | `DATABASE_URL` | required | `postgresql+asyncpg://klai:${POSTGRES_PASSWORD}@postgres:5432/klai` | N/A |
| `zitadel_introspection_url` | `ZITADEL_INTROSPECTION_URL` | required | `https://auth.${DOMAIN}/oauth/v2/introspect` | N/A |
| `zitadel_client_id` | `ZITADEL_CLIENT_ID` | required | `${CONNECTOR_ZITADEL_CLIENT_ID}` | N/A (was missing — added) |
| `zitadel_client_secret` | `ZITADEL_CLIENT_SECRET` | required | `${CONNECTOR_ZITADEL_CLIENT_SECRET}` | N/A (was missing — added) |
| `zitadel_api_audience` | `ZITADEL_API_AUDIENCE` | `""` | `${KLAI_CONNECTOR_ZITADEL_AUDIENCE:-}` | no |
| `github_app_id` | `GITHUB_APP_ID` | required | `${GITHUB_APP_ID}` | N/A (was missing — added) |
| `github_app_private_key` | `GITHUB_APP_PRIVATE_KEY` | required | `${GITHUB_APP_PRIVATE_KEY}` | N/A (was missing — added) |
| `encryption_key` | `ENCRYPTION_KEY` | required | `${CONNECTOR_ENCRYPTION_KEY}` | N/A (was missing — added) |
| `knowledge_ingest_url` | `KNOWLEDGE_INGEST_URL` | required (was `""` + validator) | `http://knowledge-ingest:8000` | no |
| `knowledge_ingest_secret` | `KNOWLEDGE_INGEST_SECRET` | `""` but validator fails | `${KNOWLEDGE_INGEST_SECRET}` | no (was already in compose) |
| `cors_origins` | `CORS_ORIGINS` | `""` | `${CONNECTOR_CORS_ORIGINS:-}` | no (safe empty default) |
| `crawl4ai_api_url` | `CRAWL4AI_API_URL` | `http://crawl4ai:11235` | `http://crawl4ai:11235` | no |
| `crawl4ai_internal_key` | `CRAWL4AI_INTERNAL_KEY` | `""` | `${CRAWL4AI_INTERNAL_KEY}` | no (was already in compose) |
| `portal_api_url` | `PORTAL_API_URL` | `http://portal-api:8100` | `http://portal-api:8010` | **YES** — code default 8100 vs compose 8010 (was already overridden, now explicit) |
| `portal_internal_secret` | `PORTAL_INTERNAL_SECRET` | `""` but validator fails | `${PORTAL_API_INTERNAL_SECRET}` | no (was already in compose) |
| `portal_caller_secret` | `PORTAL_CALLER_SECRET` | `""` | `${PORTAL_API_KLAI_CONNECTOR_SECRET}` | no (was already in compose) |
| `sync_require_org_id` | `SYNC_REQUIRE_ORG_ID` | `False` | `${SYNC_REQUIRE_ORG_ID:-false}` | no |
| `google_drive_client_id` | `GOOGLE_DRIVE_CLIENT_ID` | `""` | `${GOOGLE_DRIVE_CLIENT_ID:-}` | no (added) |
| `google_drive_client_secret` | `GOOGLE_DRIVE_CLIENT_SECRET` | `""` | `${GOOGLE_DRIVE_CLIENT_SECRET:-}` | no (added) |
| `ms_docs_client_id` | `MS_DOCS_CLIENT_ID` | `""` | `${MS_DOCS_CLIENT_ID:-}` | no (was already in compose) |
| `ms_docs_client_secret` | `MS_DOCS_CLIENT_SECRET` | `""` | `${MS_DOCS_CLIENT_SECRET:-}` | no |
| `ms_docs_tenant_id` | `MS_DOCS_TENANT_ID` | `common` | `${MS_DOCS_TENANT_ID:-common}` | no |
| `garage_s3_endpoint` | `GARAGE_S3_ENDPOINT` | `""` | `garage:3900` | **YES** — was already correct in old compose, now explicit without env_file |
| `garage_access_key` | `GARAGE_ACCESS_KEY` | `""` | `${GARAGE_ACCESS_KEY}` | no (was already in compose) |
| `garage_secret_key` | `GARAGE_SECRET_KEY` | `""` | `${GARAGE_SECRET_KEY}` | no (was already in compose) |
| `garage_bucket` | `GARAGE_BUCKET` | `klai-images` | `${GARAGE_BUCKET:-klai-images}` | no |
| `garage_region` | `GARAGE_REGION` | `garage` | `garage` | no |
| `redis_url` | `REDIS_URL` | `""` | `${CONNECTOR_REDIS_URL:-}` | no (empty = rate limiting disabled, acceptable) |
| `connector_rl_read_per_min` | `CONNECTOR_RL_READ_PER_MIN` | 120 | not declared (code default) | no |
| `connector_rl_write_per_min` | `CONNECTOR_RL_WRITE_PER_MIN` | 30 | not declared (code default) | no |
| `log_level` | `LOG_LEVEL` | `INFO` | `INFO` | no |

**Divergences found: 2**
- `PORTAL_API_URL`: code default 8100 vs correct prod value 8010 — already correct in old compose via explicit override, now stays explicit without relying on env_file.
- `GARAGE_S3_ENDPOINT`: code default `""` vs prod value `garage:3900` — already correct in old compose via explicit override, now stays explicit.

---

### Service-local .env files — vestigial status

After this migration:

- `/opt/klai/klai-mailer/.env` is NO LONGER read by the container (env_file removed).
  The file on the server is vestigial. It can be removed once the SOPS source
  (`klai-infra/core-01/klai-mailer/.env.sops`) is confirmed to have all values
  migrated to the global SOPS file or the new explicit compose vars.

- `/opt/klai/klai-connector/.env` is NO LONGER read by the container (env_file removed).
  Same cleanup applies.

**Before removing the files on the server, verify:**
```bash
# Both should return the new explicit vars, NOT from the .env file:
docker compose config klai-mailer | grep environment -A 60
docker compose config klai-connector | grep environment -A 60
```

---

### validator-env-parity check

The following required vars (pydantic validators that fail-closed) MUST exist in SOPS
before this PR is merged and deployed:

| Var | Service | SOPS location |
|---|---|---|
| `MAILER_WEBHOOK_SECRET` (→ `WEBHOOK_SECRET`) | klai-mailer | klai-infra/core-01/klai-mailer/.env.sops |
| `PORTAL_API_INTERNAL_SECRET` (→ `INTERNAL_SECRET`) | klai-mailer | klai-infra/core-01/.env.sops (already present) |
| `SMTP_HOST`, `SMTP_USERNAME`, `SMTP_PASSWORD` | klai-mailer | klai-infra/core-01/klai-mailer/.env.sops |
| `CONNECTOR_ZITADEL_CLIENT_ID` / `CLIENT_SECRET` | klai-connector | klai-infra/core-01/.env.sops (was in klai-connector/.env) |
| `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY` | klai-connector | klai-infra/core-01/.env.sops (was in klai-connector/.env) |
| `CONNECTOR_ENCRYPTION_KEY` | klai-connector | klai-infra/core-01/.env.sops (was in klai-connector/.env) |
| `KNOWLEDGE_INGEST_SECRET` (→ `KNOWLEDGE_INGEST_SECRET`) | klai-connector | klai-infra/core-01/.env.sops (already present) |
| `PORTAL_API_INTERNAL_SECRET` (→ `PORTAL_INTERNAL_SECRET`) | klai-connector | klai-infra/core-01/.env.sops (already present) |

**NOTE:** The vars that were previously only in the service-local `.env.sops`
(`CONNECTOR_ZITADEL_CLIENT_ID`, `CONNECTOR_ZITADEL_CLIENT_SECRET`,
`GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY`, `CONNECTOR_ENCRYPTION_KEY`) are now
referenced from the global compose file as `${VAR_NAME}`. The deploy person MUST
verify these exist in `klai-infra/core-01/.env.sops` before deploying. If they only
exist in `klai-infra/core-01/klai-connector/.env.sops` (per-service), they need to
be added to the global SOPS file OR the compose `environment:` interpolation must
reference the per-service SOPS-sourced values.

**Recommended deploy order:**
1. Verify all required vars are in the global SOPS (or confirm the per-service .env
   will still be present on the server during the transition).
2. Deploy docker-compose.yml change.
3. Verify services start: `docker logs --tail 30 klai-core-klai-mailer-1` and
   `docker logs --tail 30 klai-core-klai-connector-1`.
4. Mark vestigial .env files for cleanup in a follow-up klai-infra commit.
