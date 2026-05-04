# Secrets Matrix

Per-service env-var inventory for `deploy/docker-compose.yml`.
Required by [SPEC-SEC-ENVFILE-SCOPE-001](../.moai/specs/SPEC-SEC-ENVFILE-SCOPE-001/spec.md)
REQ-2 — the authoritative source of truth for "which service
legitimately reads which secret". Keep in lock-step with
`deploy/docker-compose.yml`: any PR that adds an env var to a service's
`environment:` block MUST add the matching row here.

Non-secret values (URLs to sibling services, feature flags, log levels,
model names, timeouts) are NOT listed — this matrix covers only
credentials, keys, tokens, and passwords. The rule of thumb: if the
value is in `/opt/klai/.env` / a SOPS file or looks like a secret, it
belongs here.

## Migration status

Services previously using `env_file: .env` (the shared global file at
`/opt/klai/.env`):

| Service | Migrated to explicit `environment:` | Commit |
|---|---|---|
| scribe-api | YES | `1ff65d1b` |
| retrieval-api | YES | `d6fcdb7f` |
| victorialogs | YES | `58e11c30` |
| portal-api | YES | (this commit) |

Services migrated off per-service `env_file: ./<svc>/.env` (SPEC-SEC-AUDIT-2026-04 B3):

| Service | Migrated | Commit |
|---|---|---|
| klai-mailer | YES | (this commit) |
| klai-connector | YES | (this commit) |

Services still using per-service `env_file` pattern (REQ-6 — compliant as-is):
librechat-getklai.

## Matrix

Rows sorted by secret name, then by service.

Source column: all rows below reference `/opt/klai/.env` (rendered
from SOPS `klai-infra/core-01/.env.sops`) unless noted otherwise.

| Secret | Service | Purpose |
|---|---|---|
| `CONNECTOR_CORS_ORIGINS` | klai-connector | Comma-separated allowed CORS origins. Empty disables CORS (code default). Mapped to `CORS_ORIGINS` in-container. |
| `SMTP_HOST` | klai-mailer | SMTP server hostname. Source: klai-infra/core-01/klai-mailer/.env.sops. |
| `SMTP_PASSWORD` | klai-mailer | SMTP auth password. Source: klai-infra/core-01/klai-mailer/.env.sops. |
| `SMTP_USERNAME` | klai-mailer | SMTP auth username. Source: klai-infra/core-01/klai-mailer/.env.sops. |
| `CONNECTOR_ENCRYPTION_KEY` | klai-connector | KEK for connector credential storage (two-tier hierarchy). Mapped to `ENCRYPTION_KEY` in-container. |
| `CONNECTOR_REDIS_URL` | klai-connector | Redis URL for per-org rate limiting (SPEC-SEC-HYGIENE-001 HY-32). Empty disables rate limiting (code default). Mapped to `REDIS_URL` in-container. |
| `CONNECTOR_ZITADEL_CLIENT_ID` | klai-connector | Zitadel OIDC client_id for connector token introspection. Mapped to `ZITADEL_CLIENT_ID` in-container. |
| `CONNECTOR_ZITADEL_CLIENT_SECRET` | klai-connector | Zitadel OIDC client_secret for connector introspection. Mapped to `ZITADEL_CLIENT_SECRET` in-container. |
| `DOCS_INTERNAL_SECRET` | portal-api | Shared secret portal-api → klai-docs for KB provisioning calls. |
| `GRAPHITI_LLM_MODEL` | retrieval-api | LLM model name used by Graphiti for knowledge-graph extraction. Prod uses `klai-pipeline`; config.py default is `klai-fast`. Not a secret — declared here because the prod value differs from the code default. |
| `FIRECRAWL_INTERNAL_KEY` | portal-api | Shared web-search API key (portal re-uses the Firecrawl internal key for URL extraction). |
| `GITHUB_ADMIN_PAT` | portal-api | GitHub PAT with `admin:org` scope — used during offboarding to remove members from the GetKlai org. |
| `KLAI_CONNECTOR_SECRET` | portal-api | Shared secret for portal → klai-connector orchestration calls (SOPS key: `PORTAL_API_KLAI_CONNECTOR_SECRET`, mapped inline). |
| `KNOWLEDGE_INGEST_SECRET` | portal-api | Shared secret portal-api → knowledge-ingest for tenant KB mutations. |
| `KNOWLEDGE_INGEST_SECRET` | scribe-api | HMAC auth when scribe pushes transcripts to knowledge-ingest. |
| `KNOWLEDGE_RETRIEVE_URL` | portal-api | URL of retrieval-api used for gap re-scoring (value, not secret — kept here because it crosses a trust boundary). |
| `LIBRECHAT_MONGO_ROOT_URI` | portal-api | MongoDB root URI with multi-DB read access for lazy LibreChat user mapping (KB-010). |
| `LOGO_URL` | klai-mailer | Brand logo URL for email templates. Overrides code default (example.com). Mapped to `LOGO_URL` in-container. Source: klai-infra/core-01/klai-mailer/.env.sops. |
| `BRAND_URL` | klai-mailer | Brand homepage URL for email templates. Overrides code default (example.com). Mapped to `BRAND_URL` in-container. Source: klai-infra/core-01/klai-mailer/.env.sops. |
| `MAILER_WEBHOOK_SECRET` | klai-mailer | HMAC secret for Zitadel webhook signature verification. Validator fails closed on empty (SPEC-SEC-MAILER-INJECTION-001 REQ-9.1). Mapped to `WEBHOOK_SECRET` in-container. Source: klai-infra/core-01/klai-mailer/.env.sops. |
| `LITELLM_MASTER_KEY` | portal-api | Master key for the LiteLLM gateway — portal writes this when provisioning per-tenant LibreChat containers. |
| `LITELLM_MASTER_KEY` | retrieval-api | Bearer token for the LiteLLM gateway (re-exposed as `LITELLM_API_KEY` in-container). |
| `LITELLM_MASTER_KEY` | scribe-api | Bearer token for the LiteLLM gateway (AI summarization of transcripts). |
| `MEILI_MASTER_KEY` | portal-api | Meilisearch master key — portal provisions Meili indexes per tenant. |
| `MONEYBIRD_WEBHOOK_TOKEN` | portal-api | Signs Moneybird billing webhooks; portal's config validator fails closed on empty/whitespace (SPEC-SEC-WEBHOOK-001 REQ-3). |
| `MAILER_PORTAL_API_URL` | klai-mailer | Portal API base URL for locale lookup. Falls back to code default http://portal-api:8010 when unset. Mapped to `PORTAL_API_URL` in-container. |
| `MONGO_ROOT_PASSWORD` | portal-api | MongoDB root password for per-tenant LibreChat database provisioning. |
| `MONGO_ROOT_USERNAME` | portal-api | MongoDB root username (non-secret but kept here for pairing with the password). |
| `PORTAL_API_BFF_SESSION_KEY` | portal-api | Fernet key for BFF session records at rest in Redis (SPEC-AUTH-008). Mapped to `BFF_SESSION_KEY` in-container. |
| `PORTAL_API_DB_PASSWORD` | portal-api | Portal's PostgreSQL password; interpolated into `DATABASE_URL`. |
| `PORTAL_API_ENCRYPTION_KEY` | portal-api | KEK for the two-tier connector credential hierarchy (SPEC-KB-020). Mapped to `ENCRYPTION_KEY` in-container. |
| `PORTAL_API_IMAP_PASSWORD` | portal-api | IMAP password for the meeting-invite listener (`meet@getklai.com`). Mapped to `IMAP_PASSWORD` in-container. |
| `PORTAL_API_INTERNAL_SECRET` | portal-api | Shared secret used by klai-mailer → portal for webhook callbacks. Mapped to `INTERNAL_SECRET` in-container. |
| `PORTAL_API_KLAI_CONNECTOR_SECRET` | portal-api | See `KLAI_CONNECTOR_SECRET` row — this is the SOPS-side name. |
| `PORTAL_API_PORTAL_SECRETS_KEY` | portal-api | Application-level key encrypting per-tenant secrets (zitadel_librechat_client_secret, litellm_team_key). Mapped to `PORTAL_SECRETS_KEY` in-container. |
| `PORTAL_API_SSO_COOKIE_KEY` | portal-api | Fernet key for SSO session cookies. Mapped to `SSO_COOKIE_KEY` in-container. |
| `PORTAL_API_ZITADEL_PAT` | portal-api | Zitadel admin PAT for provisioning portal users/orgs. Mapped to `ZITADEL_PAT` in-container. |
| `PORTAL_API_ZITADEL_PORTAL_CLIENT_SECRET` | portal-api | OIDC confidential-client secret for BFF code-exchange (SPEC-AUTH-008). Mapped to `ZITADEL_PORTAL_CLIENT_SECRET` in-container. |
| `POSTGRES_PASSWORD` | retrieval-api | Portal-events write path — retrieval-api pushes `knowledge.queried` events to the portal `product_events` table. Interpolated into `PORTAL_EVENTS_PASSWORD`. |
| `POSTGRES_PASSWORD` | scribe-api | PostgreSQL password; interpolated into `POSTGRES_DSN` for the scribe schema. |
| `QDRANT_API_KEY` | portal-api | Qdrant vector-store API key (portal runs embedding-write paths for demo content). |
| `QDRANT_API_KEY` | retrieval-api | Qdrant vector-store API key (dense retrieval). |
| `REDIS_PASSWORD` | portal-api | Redis password; interpolated into `REDIS_URL` for the BFF session store + rate limiter + LibreChat provisioning. |
| `REDIS_PASSWORD` | retrieval-api | Interpolated into `REDIS_URL` for the rate-limiter (SPEC-SEC-010). |
| `RETRIEVAL_API_INTERNAL_SECRET` | portal-api | Shared secret portal-api → retrieval-api for `/retrieve` calls. Kept separate from `INTERNAL_SECRET` so the two trust boundaries can rotate independently (SPEC-SEC-010 REQ-6.1). |
| `RETRIEVAL_API_INTERNAL_SECRET` | retrieval-api | Shared secret for internal callers (portal-api, LiteLLM hook). Mapped to `INTERNAL_SECRET` in-container (SPEC-SEC-010). |
| `RETRIEVAL_API_RATE_LIMIT_RPM` | retrieval-api | Sliding-window rate-limit threshold per caller identity (SPEC-SEC-010). |
| `RETRIEVAL_API_ZITADEL_AUDIENCE` | retrieval-api | Zitadel audience for JWT validation. Mapped to `ZITADEL_API_AUDIENCE` in-container. |
| `VEXA_ADMIN_TOKEN` | portal-api | Reserved for Vexa admin-API calls (tenant provisioning, quota inspection). @MX:NOTE in config.py — no runtime reader yet; declared to preserve pre-migration env-shape. |
| `VEXA_BOT_MANAGER_API_KEY` | portal-api | Vexa bot-manager API key. Mapped to `VEXA_API_KEY` in-container. |
| `VEXA_MEETING_API_URL` | portal-api | Base URL for Vexa meeting-api (prod routes through `api-gateway:8000`, not the raw meeting-api). Overrides the config.py default. |
| `VEXA_WEBHOOK_SECRET` | portal-api | Signs Vexa webhook deliveries to portal; config.py validator fails closed on empty/whitespace (SEC-013 F-033). |
| `VICTORIALOGS_AUTH_PASSWORD` | victorialogs | HTTP basic-auth password (set via `-httpAuth.password` cmdline flag). Also needed inside the container so the busybox-wget healthcheck can build the auth header. |
| `VICTORIALOGS_AUTH_USER` | victorialogs | HTTP basic-auth username (set via `-httpAuth.username` cmdline flag). Also needed inside the container for the healthcheck. |
| `WIDGET_JWT_SECRET` | portal-api | Signs widget JWTs (SPEC-WIDGET-001). Empty value causes widget endpoints to return 503 — not a validator-blocked field. |
| `ZITADEL_IDP_GOOGLE_ID` | portal-api | Instance-level Zitadel IDP id for Google social login (non-secret). |
| `ZITADEL_IDP_MICROSOFT_ID` | portal-api | Instance-level Zitadel IDP id for Microsoft social login (non-secret). |
| `ZITADEL_PORTAL_CLIENT_ID` | portal-api | OIDC client_id for the BFF confidential WEB app (non-secret). |

## Rotation coupling

See [SPEC-SEC-005](../.moai/specs/SPEC-SEC-005/spec.md) and
`klai-infra/INTERNAL_SECRET_ROTATION.md` — narrower per-service scope
means a secret rotation now touches only the services listed in its
column of this table. When this matrix changes, cross-check the
rotation runbooks.

## How to update this file

1. When a PR adds a new key to a service's `environment:` block, add a
   row here in the same PR. Reviewer rejects PR without the row.
2. When a PR removes a key from a service, remove the matching row.
3. Keep the table sorted (alphabetical by secret name, then service).
4. Use `/opt/klai/.env (SOPS core-01/.env.sops)` as the source for
   shared secrets. Per-service SOPS paths (e.g.
   `klai-infra/core-01/klai-mailer/.env.sops`) cite the per-service
   file.
