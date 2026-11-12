# Klai Platform: What Runs Today

**Verified on 12 November 2026 against commit `3987ee2a9` of `main`, with the two later changes of 12 November (#2340, #2341) folded in and the anchors rechecked at `631b5a82f`.** This document describes the platform as it runs now: the services, how tenants are kept apart, the portal, how people and services authenticate, the models, the other product surfaces, how a change reaches production, and how the platform is observed and backed up. It says nothing about how the platform came to be; that is in [evolution.md](evolution.md) (Dutch), which also holds the decisions, the alternatives that were rejected and the plans that were never built.

Every statement carries an anchor (`path:line`). Anchors are relative to the repository root. Where a paragraph names a base directory, anchors in that paragraph are relative to it. The detail of the knowledge stack and the chat lives in its own documents, linked in section 6. This document is public: it describes service contracts and host roles, not Klai's live production topology (see [the infrastructure boundary](../contributing/infrastructure-boundary.md)). Host roles used here: `core-01` runs the main compose file, `gpu-01` runs the inference compose file, `public-01` runs the website and the status page and has no compose file in this repository.

## 1. Service map

The compose files are the source of truth for what runs and for which image version. This document deliberately copies no image versions: a hand-kept version table drifts from the compose files within weeks, and several have. Versions are read from the files below, and pins are checked mechanically (`deploy/check-image-tags.sh:30-35` rejects mutable tags for third-party images, `deploy/check-version-pin-agreement.py` ties compose pins to the CI workflow pins). `deploy/VERSIONS.md` records the reason for individual pins.

| Compose file | Host role | What it holds |
|---|---|---|
| `deploy/docker-compose.yml` (project `klai-core`) | core-01 | The production stack and the self-hosting template (`deploy/docker-compose.yml:1`) |
| `deploy/docker-compose.override.yml` | core-01 | One override: `retrieval-api` runs under the local tag that CI sets before `compose up` (`deploy/docker-compose.override.yml:1-4`) |
| `deploy/docker-compose.gpu.yml` | gpu-01 | Embeddings, reranker, sparse vectors and transcription. Public CI only validates it (`.github/workflows/validate-gpu-compose.yml:40-49`) |
| `docker-compose.dev.yml` | a developer machine | Local development: postgres, redis, mongodb, meilisearch, litellm |

The per-tenant LibreChat containers are in no compose file: portal-api creates them at provisioning time through the Docker socket proxy chain (`klai-portal/backend/app/services/provisioning/orchestrator.py:5,228-236`). Everything on the core host shares one reverse proxy, Caddy, which terminates TLS for the wildcard domain; "published" below means reachable through a route in `deploy/caddy/Caddyfile`. Services without a route are reachable only from other containers.

### Product services built from this repository

All run on core-01 from `deploy/docker-compose.yml`. Images are built by the workflow of the same name in `.github/workflows/`.

| Service (compose line) | Source | Purpose | Published |
|---|---|---|---|
| portal-api (1053) | `klai-portal/backend` | Portal backend: sessions, workspaces, provisioning, Partner API, Shield API, meeting integration | Yes: the portal API, `/partner/*`, `/kb-images/*`, the MCP OAuth endpoints, `/static/*` (`deploy/caddy/Caddyfile:338,349,432,473,495`) |
| portal frontend | `klai-portal/frontend` | Static files built by CI and served by Caddy, no container (`deploy/docker-compose.yml:195`) | Yes, as the catch-all with SPA fallback (`deploy/caddy/Caddyfile:554-565`) |
| retrieval-api (1603) | `klai-retrieval-api` | Hybrid retrieval: `POST /retrieve`, `POST /chat`, taxonomy routes (`klai-retrieval-api/retrieval_api/api/retrieve.py:1412`, `klai-retrieval-api/retrieval_api/api/chat.py:24`) | No |
| knowledge-ingest (2188) | `klai-knowledge-ingest` | Ingest, enrichment, graph extraction, crawl orchestration, job queue on Procrastinate | No |
| klai-connector (2303) | `klai-connector` | Connector scheduler and source adapters | Yes, own host (`deploy/caddy/Caddyfile:245-254`) |
| scribe-api (1794) | `klai-scribe/scribe-api` | Audio upload, transcription, summaries, hand-off to a knowledge base | No; reached through the portal proxy (`deploy/caddy/Caddyfile:524-531`) |
| docs-app (1733) | `klai-docs` | Docs reader and editor, `basePath /docs`, port 3010 | Reader only; `/docs/api/*` answers 404 (`deploy/caddy/Caddyfile:507-518`) |
| klai-knowledge-mcp (1764) | `klai-knowledge-mcp` | MCP server | Yes, own host (`deploy/caddy/Caddyfile:306-315`) |
| klai-mailer (825) | `klai-mailer` | Zitadel notification provider and internal template sender | No |
| klai-slack (937) | `klai-slack` | Slack app | Only `/slack/*` on the API host (`deploy/caddy/Caddyfile:373-378`) |
| klai-docker-authz (801) | `klai-docker-authz` | Inspects container-create requests in front of the Docker socket proxy | No |
| caddy (182) | `deploy/caddy` | Reverse proxy, built with a DNS-provider plugin and a rate-limit module for wildcard TLS (`deploy/caddy/Dockerfile:41-43`) | Is the edge |

`klai-libs/` holds thirteen shared Python libraries (chat prompts, citations, connector credentials, identity assertion, image storage, knowledge-base slugs, LLM safety and throttling, log utilities, retrieval telemetry, service auth, webhook replay, graph compatibility) that the service images install by relative path.

### Data stores

All on core-01. Each application database has its own network; the main Postgres instance is shared by the Klai services.

| Service | Holds |
|---|---|
| postgres (232) | One instance, several databases: the portal (`klai`, with schemas `knowledge`, `docs`, `scribe` and `connector` next to the portal tables), `zitadel`, `litellm`, `glitchtip`, `gitea`, the Vexa database, `klai_slack` (`deploy/postgres/init.sql:4-45`; schemas: `klai-knowledge-ingest/alembic/versions/0001_baseline.py:52`, `klai-docs/migrations/001_docs_schema.sql:4`, `klai-scribe/scribe-api/alembic/env.py:49`, `klai-connector/alembic/versions/001_initial.py:25`) |
| mongodb (210) | LibreChat users and conversations, one database per workspace |
| redis (252) | Portal sessions and rate limits, LiteLLM budgets and router counters, mailer rate limit, retrieval-api, GlitchTip |
| qdrant (2120) | One collection `klai_knowledge` with named vectors `vector_chunk`, `vector_questions`, `vector_sparse` (`klai-retrieval-api/retrieval_api/config.py:12`, `klai-retrieval-api/retrieval_api/services/search.py:1-4`) |
| falkordb (2142) | The Graphiti knowledge graph; `GRAPHITI_ENABLED` is true on retrieval-api and knowledge-ingest (`deploy/docker-compose.yml:1627,2221`) |
| meilisearch (292) | LibreChat conversation search, with workspace-scoped keys (`klai-portal/backend/app/services/provisioning/orchestrator.py:157,196-207`) |
| garage (2276) | S3-compatible object storage: knowledge-base images, Scribe artifacts, Vexa recordings |
| gitea (1684) | Git repositories behind the docs knowledge bases |
| victoriametrics, victorialogs, victorialogs-content | Metrics, 30-day logs, 7-day content-class logs (section 9) |
| listmonk-db, firecrawl-postgres, crm-postgres, umami-postgres, fider-postgres, crm-redis, vexa12-redis, firecrawl-rabbitmq | One store per third-party application |

pgvector is in the Postgres image name only; the ingest code does not use it for vector search (no `pgvector` or `vector(` match in `klai-knowledge-ingest`).

### AI and inference

| Service | Host role | Purpose |
|---|---|---|
| litellm (383) | core-01 | The only LLM gateway. Config and hooks in `deploy/litellm/`; the admin UI is off (`deploy/docker-compose.yml:473`) |
| presidio-analyzer (545), presidio-anonymizer (600) | core-01 | PII detection and masking on the internal `inference` network, no route |
| docling-serve (1577) | core-01 | File to markdown; the caller is portal-api (`klai-portal/backend/app/core/config.py:259-262`) |
| searxng (1591) | core-01 | Web search for portal-api, LiteLLM (`deploy/litellm/config.yaml:327`) and the workspace LibreChat |
| crawl4ai (2374) | core-01 | Crawler used by knowledge-ingest (`deploy/docker-compose.yml:2329`) |
| firecrawl-api (2463) | core-01 | Crawler used by the workspace LibreChat (`klai-portal/backend/app/services/provisioning/generators.py:338-339`) |
| ollama (690) | core-01 | Runs, but no model alias or fallback in LiteLLM uses it (`deploy/litellm/config.yaml:363-366`) |
| librechat-getklai (616) | core-01 | The one workspace LibreChat container kept in compose; Klai builds the image from upstream with patches (`deploy/librechat/Dockerfile.klai:23`) |
| tei, infinity, bge-m3-sparse | gpu-01 | Dense embeddings (BGE-M3), reranker (`bge-reranker-v2-m3`), sparse embeddings (`deploy/docker-compose.gpu.yml:11,37,69`) |
| transcription-worker-1, transcription-worker-2, transcription-api | gpu-01 | faster-whisper `large-v3-turbo` on CUDA, behind an nginx least-connections balancer with an OpenAI-compatible `/v1/audio/transcriptions` (`deploy/docker-compose.gpu.yml:110,146,181`) |

Every gpu-01 service binds to loopback and is reached from core-01 over a tunnel (`deploy/docker-compose.gpu.yml:3`). The tunnel and the host itself are operated from the private infrastructure repository. Consumers address the tunnel endpoint through configuration (`klai-retrieval-api/retrieval_api/config.py:15-17`, `klai-scribe/scribe-api/app/core/config.py:40`).

### Identity, meetings, observability, applications, supporting

| Kind | Services (compose line) | Notes |
|---|---|---|
| Identity | zitadel (321) | Identity provider; own host through Caddy over h2c (`deploy/caddy/Caddyfile:121-134`) |
| Meeting capture | vexa12-admin-api (1949), vexa12-runtime (1990), vexa12-meeting-api (2025), vexa12-redis (1899), vexa12-transcription-proxy (1923) | The Vexa stack. The meeting API listens only on the network shared with portal-api; the proxy forwards the bots' transcription calls to gpu-01 (`deploy/docker-compose.yml:1932-1934`) |
| Docker access | docker-socket-proxy (719), docker-socket-proxy-ro (748), runtime-api-socket-proxy (1838) | The write proxy allows only containers and networks with create and delete, behind klai-docker-authz; the read-only proxy serves Alloy (`deploy/docker-compose.yml:709-718`, `deploy/alloy/config.alloy:15-27`) |
| Observability | alloy (1463), grafana (1494), cadvisor (1436), glitchtip-web, glitchtip-worker, glitchtip-migrate (1247, 1281, 1306) | See section 9 |
| Third-party applications | crm and crm-worker, umami, fider, listmonk, vaultwarden, calnode | CRM, web analytics, feedback board, newsletter, password manager, scheduling. Each has its own host in Caddy (`deploy/caddy/Caddyfile:207-216,568-649`) |
| Supporting | rollback-canary (901) | Behind the profile `rollback-rehearsal`; used only by the rehearsal workflows (`deploy/docker-compose.yml:901-902`) |

Two other published hosts serve operations: a bearer-gated log-ingest endpoint that remote agents push to, and Grafana (`deploy/caddy/Caddyfile:103-119,152-190`). Caddy finally imports per-tenant site files from a shared volume; docs-app contains a writer for per-organisation docs host files (`klai-docs/lib/caddy.ts:13,24`) that nothing calls yet, and portal-api writes the LibreChat host files (`deploy/caddy/Caddyfile:659-666`).

### Directories that are not services

`klai-widget` (the embeddable chat bundle, built inside the portal frontend workflow), `klai-shield-extension` (browser extension), `klai-hubspot` (HubSpot app), `deploy` (compose, Caddy, Alloy, Grafana provisioning, LiteLLM config, scripts), `rules` (ast-grep rules), `scripts` (repository guards and audits). `klai-infra` and `klai-website` are private repositories that appear as submodules (`.gitmodules`).

## 2. Tenancy and data

**Identifying the org.** One Zitadel organisation per tenant is created at signup (`klai-portal/backend/app/api/signup.py:346,720`) and recorded in `portal_orgs` (integer `id`, string `zitadel_org_id`, `slug`). A user belongs to an org through `portal_users(zitadel_user_id, org_id)`; one user can have several rows (`klai-portal/backend/app/api/auth_bff.py:229-257`). Per request, `get_caller` resolves the org from the session's user and calls `set_tenant` (`klai-portal/backend/app/core/permissions.py:235-336`). The host must also match: `KlaiTenantHostMiddleware` compares the first label of the request host with the slug of the session's org and redirects (HTML) or answers 409 with a target (XHR) on a mismatch (`klai-portal/backend/app/middleware/tenant_host.py:1-60`).

**Row-level security in the portal database.** An `after_begin` listener runs at the start of every transaction and sets four transaction-local settings: `app.current_org_id`, `app.cross_org_admin`, `klai.changed_by_user_id` and `app.is_platform_admin` (`klai-portal/backend/app/core/database.py:35-39,73-113`). The values come from the session (`set_tenant`) and from context variables for the acting user (`klai-portal/backend/app/core/database.py:43-70`). Background work opens its own sessions with `tenant_scoped_session(org_id)`, and operations that must cross orgs use `cross_org_session()` (`klai-portal/backend/app/core/database.py:497,525`). The policy helper `_rls_current_org_id()` returns the org id, returns NULL when the cross-org flag is true, and raises an error when neither is set, so a query without tenant context fails instead of returning rows (`klai-portal/backend/alembic/versions/post_deploy_rls_raise_on_missing_context.sql:73-99`). That file applies the policies to the knowledge-base, group, group-access, tombstone, taxonomy, retrieval-gap, user-product, MCP-token, meeting, partner-key and widget tables (same file, `:118-319`); other `post_deploy_*.sql` files that use the helper add further tables, among them widget conversations and product updates. The tables added for the chat and gap work all take the strict category (category D in `.claude/rules/klai/projects/portal-security.md:94-105`: `FORCE ROW LEVEL SECURITY` and a `tenant_isolation` policy with `USING (_rls_current_org_id() IS NULL OR org_id = _rls_current_org_id())` and `WITH CHECK (org_id = _rls_current_org_id())`): `internal_chat_cases` (`klai-portal/backend/alembic/versions/post_deploy_f3a9c6e2b4d1_internal_chat_cases_rls.sql:20-31`), `internal_chat_turns` (`post_deploy_i5c6t7u8r9n0_internal_chat_turns_rls.sql:20-31`), `portal_support_cases` (`post_deploy_s1p2c3a4s5e6_support_cases_rls.sql:15-26`), `portal_gap_proposals` (`post_deploy_77d01eafb800_gap_proposals_rls.sql:22-33`), `answer_reviews` (`post_deploy_c2a7e9d4b1f6_answer_reviews_rls.sql:91-96`) and `kb_uploads`, which despite the name is a portal table (`post_deploy_85e5d0a7cb98_kb_uploads_rls.sql:16-27`). `portal_retrieval_gaps` has had the same policy since the raise-on-missing-context file (`post_deploy_rls_raise_on_missing_context.sql:163-165`). `portal_users` is the exception by design: the lookup that finds the user runs before the org is known (`klai-portal/backend/app/core/permissions.py:179-184`). A guard logs or raises when DML on a protected table touches zero rows (`klai-portal/backend/app/core/rls_guard.py:55-93`). RLS DDL lives in `post_deploy_*.sql` files that the deploy applies after the migrations (section 8).

**Per-tenant resources.** Provisioning creates, in order, with a compensating action for each step (`klai-portal/backend/app/services/provisioning/orchestrator.py:309-689`): a Zitadel OIDC application for LibreChat, a LiteLLM team and key, a MongoDB user and database, a Meilisearch runtime key with `{slug}_messages` and `{slug}_convos` indexes (the same key and the same two index names for every new tenant, `klai-portal/backend/app/services/provisioning/orchestrator.py:195-224` and `generators.py:315-323`; provisioning does not switch `SEARCH` on, and the container entrypoint refuses to start with `SEARCH=true` unless both index names are set, `deploy/librechat/klai-entrypoint.sh:38-47`; the runbook for switching search on per tenant is `docs/runbooks/meili-tenant-search-rollout.md`), the LibreChat environment file with an internal-chat key, a personal docs repository in Gitea through docs-app, the portal knowledge bases (`org` and `personal-<user>`), the LibreChat container, a Caddy site block, the Caddy reload and the system groups (`klai-portal/backend/app/services/provisioning/orchestrator.py:617,632,646`). A failed run rolls the steps back in reverse order. Deprovisioning runs 21 steps in order with three attempts each, ending in a hard delete of the portal rows (`klai-portal/backend/app/services/provisioning/deprovisioning_steps.py:993-1018`, `klai-portal/backend/app/services/provisioning/deprovisioning_orchestrator.py:161-162`).

**Carrying the tenant into the other stores.** The key is the Zitadel org id string.

| Store | Mechanism |
|---|---|
| Qdrant | Every point carries `org_id` in its payload and every retrieval filter starts with an `org_id` condition (`klai-retrieval-api/retrieval_api/services/search.py:131-139`) |
| Graph | Each organisation is a Graphiti group: searches pass `group_ids=[org_id]` (`klai-retrieval-api/retrieval_api/services/graph_search.py:264`) |
| Object storage | Knowledge-base images use the key `{org_id}/images/{kb_slug}/{sha256}.{ext}` (`klai-libs/image-storage/klai_image_storage/storage.py:100-107`); Scribe artifacts live under a prefix per slug (`klai-portal/backend/app/services/provisioning/deprovisioning_steps.py:562`) |
| MongoDB, Meilisearch | A database per workspace; indexes and keys scoped per workspace (`klai-portal/backend/app/services/provisioning/orchestrator.py:461-483`) |
| Downstream calls | portal-api passes the Zitadel organisation id to knowledge-ingest and retrieval-api (`klai-portal/backend/app/services/knowledge_ingest_client.py:56,98`, `klai-portal/backend/app/services/retrieval_context.py:837,895-898`) |

## 3. Portal

The portal is a FastAPI backend (Python 3.13, `klai-portal/backend/Dockerfile:8,44`) and a React single-page app (React 19, Vite, TanStack Router and Query, Mantine, Tailwind; `klai-portal/frontend/package.json:36-108`) with Dutch and English messages through Paraglide (`klai-portal/frontend/vite.config.ts:5`). Besides the product it hosts the Partner API, the Shield API, the meeting integration and the proxy to scribe-api and docs-app. The other Klai services use Python 3.12, except `bge-m3-sparse` on 3.11 (the `FROM` line of each Dockerfile).

### API surface

Routers are registered in `klai-portal/backend/app/main.py:404-457`. Modules are in `klai-portal/backend/app/api/`.

| Group | Modules | What they serve |
|---|---|---|
| auth | `auth`, `auth_bff`, `auth_password_policy`, `auth_join`, `auth_select`, `signup`, `me`, `me_mcp_tokens`, `mcp_oauth` | Login, session and logout, MFA enrolment, password reset, workspace choice and join requests, signup, the current user, organisation telemetry and PII settings, MCP token management, the OAuth server for MCP clients |
| admin | `admin/` (users, products, settings, audit, join requests, retry provisioning, deprovision, platform unlocks, extensions, billing, platform pages, widget retention), `groups`, `knowledge_bases`, `admin_api_keys`, `admin_widgets`, `admin_slack` | Workspace administration and Klai platform administration under `/api/admin` and `/api/admin/platform` |
| app | `app_*` (account, activity, assistant, chat, gap clusters, gaps, knowledge bases, knowledge sources, product updates, support cases, templates), `connectors`, `taxonomy`, `kb_images`, `knowledge`, `meetings`, `billing`, `mcp_servers`, `oauth`, `shield`, `proxy`, `vitals` | End-user features under `/api/app`, `/api/knowledge`, `/api/bots`, `/api/billing`; the BFF proxy `/api/scribe/*` and `/api/docs/*`; web-vitals and Prometheus metrics |
| partner | `partner`, `partner_openai_compat`, `partner_feedback`, `partner_widget_config`, support sessions, HubSpot handoffs | `/partner/v1`: knowledge bases, `chat/completions`, `models`, `responses`, `feedback`, `knowledge`, `widget-config`, `public-bot-config`, `support-sessions` |
| internal | `internal`, `internal_connectors`, `internal_support_cases`, `internal_slack` | Service-to-service routes under `/internal`: identity verification, permissions, connector status, retrieval log, onboarding, template resolution, Slack linking |
| webhooks | `webhooks`, `meetings` | `/api/webhooks/moneybird`, `/api/webhooks/hubspot/custom-channel`, `/api/bots/internal/webhook` for Vexa (Caddy answers 404 for that last path from outside, `deploy/caddy/Caddyfile:413`) |

The module `orgs.py` under `/api/orgs` serves the telemetry level and the PII entity and allow-list settings of the organisation (`klai-portal/backend/app/api/orgs.py:68,120,222`).

### Frontend areas

Routes are in `klai-portal/frontend/src/routes/`. Public and onboarding pages: start, login, callback, logged-out, signup, password, verify, MFA setup, workspace selection, join request, provisioning, tenant-deleted, the widget host page. `/app` holds chat (an iframe of the tenant's LibreChat, `klai-portal/frontend/src/routes/app/chat.tsx:241`), knowledge, gaps, meetings and transcription, docs, integrations, account and instructions. `/admin` holds users, groups, profiles, API keys, widgets, Slack, MCP servers, billing, settings, join requests, danger zone and the platform area. `/app` and `/admin` use `useProtectedRoute` (unauthenticated users go to the start, MFA setup is forced when the org requires it); `/admin` needs at least `kb_manager` (`klai-portal/frontend/src/hooks/useProtectedRoute.ts:110-130`, `klai-portal/frontend/src/routes/admin/route.tsx:18-21`).

### Roles and accounts

| Axis | Values | Anchor |
|---|---|---|
| Profile role (`portal_users.role`) | `personal` < `company` < `kb_manager` < `group_manager` < `admin`; default `company` | `klai-portal/backend/app/core/profiles.py:50-63` |
| Capabilities | Connectors for everyone; external connectors, organisation knowledge bases, members, taxonomy, gaps, activity and organisation templates from `kb_manager` | `klai-portal/backend/app/core/profiles.py:31-38,92-112` |
| Seat type | `chat` or `knowledge`, derived from the role | `klai-portal/backend/app/core/seats.py:42-110` |
| Products | `chat` and `knowledge` from the seat; `scribe` and `docs` need a platform unlock and at least `company` | `klai-portal/backend/app/core/features.py:37-113` |
| Platform unlocks | Per-organisation features such as `scribe`, `docs`, `partner_api`, `widgets`, `custom_mcps`, `slack`, set only by a platform admin | `klai-portal/backend/app/models/portal.py:120-133`, `klai-portal/backend/app/core/permissions.py:456-498` |
| Platform admin | An admin of the organisation whose slug is the configured platform organisation | `klai-portal/backend/app/core/permissions.py:427-453` |
| User status | `active`, `suspended`, `offboarded` | `klai-portal/backend/app/models/portal.py:214` |

Only the `admin` role is granted `org:owner` in Zitadel; the other roles live in the portal database (`klai-portal/backend/app/api/admin/users.py:81-87`). Groups exist for organisation and knowledge-base access; they carry no product grants (`klai-portal/backend/app/api/groups.py:428-447`). Detail on roles, invitations and offboarding: [klai-portal/docs/admin-roles.md](../../klai-portal/docs/admin-roles.md).

### Background loops

All are asyncio tasks started in the portal lifespan (`klai-portal/backend/app/main.py:284-327`), plus a stuck-provisioning check at boot (`klai-portal/backend/app/main.py:76-90`).

| Loop | Cadence | Does |
|---|---|---|
| Meeting poller | 10 s | Vexa meeting status, join and processing timeouts (`klai-portal/backend/app/services/bot_poller.py:37`) |
| Upload poller | 5 s, 3 h deadline per upload | Polls docling tasks and forwards the markdown to knowledge-ingest (`klai-portal/backend/app/services/kb_upload_poller.py:48,57`) |
| Recording cleanup | 300 s | Deletes Vexa recordings of finished meetings older than 30 minutes (`klai-portal/backend/app/services/recording_cleanup.py:20-24`) |
| Telemetry purge | 24 h | Removes shadow queries and raw retrieval gaps older than 7 days (`klai-portal/backend/app/services/telemetry_purge.py:45-46`) |
| Widget message retention | 24 h | Deletes widget messages past the per-organisation or default 90 days, and clears the visitor's name, e-mail, first question and IP and browser hashes of conversations, and the judge's reasoning of judgments, past that term (`klai-portal/backend/app/services/widget_messages_retention.py:46`; rule in [conversation-retention-and-learning.md](conversation-retention-and-learning.md)) |
| Widget outcome | 15 min | Labels conversations quiet for 60 minutes (`klai-portal/backend/app/services/widget_outcome.py:74`) |
| Conversation judge | 30 min, only 01:00 to 06:00 UTC | LLM judge (`klai-judge`) on finished conversations (`klai-portal/backend/app/services/conversation_judge.py:64`) |
| Support reanalysis | 60 s, debounced per organisation | Re-analyses support cases after a connector sync (`klai-portal/backend/app/services/gap_rescorer.py:362`) |
| Mail listener | 60 s, only when a mailbox is configured | Meeting invitations (`klai-portal/backend/app/services/imap_listener.py:29-44`) |

### Billing

Billing runs through Moneybird. The portal shows the billing status and a link to the Moneybird invoice portal (`klai-portal/backend/app/api/billing.py:26-60`), and the Moneybird webhook sets the status to `cancelled` or `payment_failed`; the token is compared in constant time and an empty token is refused at startup (`klai-portal/backend/app/api/webhooks.py:151-185`, `klai-portal/backend/app/core/config.py:513-521`). Deprovisioning stops the subscription and archives the contact (`klai-portal/backend/app/services/provisioning/deprovisioning_steps.py:670-700`). The portal does not create subscriptions or contacts: the creation methods have no caller, the switch to per-seat billing answers 501, and the breakdown pages are display only (`klai-portal/backend/app/api/admin/billing.py:74-223`). Seat changes are recorded in `portal_user_seat_history` by a database trigger (`klai-portal/backend/app/core/permissions.py:290-298`).

## 4. Login and service authentication

### Browser session

The portal is a backend-for-frontend: the browser holds an opaque session cookie and never a Zitadel token. Details and endpoint reference: [klai-portal/docs/auth-flows.md](../../klai-portal/docs/auth-flows.md) and [klai-portal/docs/api-auth.md](../../klai-portal/docs/api-auth.md). Anchors below are relative to `klai-portal/backend/` (backend) or `klai-portal/frontend/` (frontend).

1. The SPA asks `GET /api/auth/session`; a 401 `cookie_required` means no session (`frontend/src/lib/auth.tsx:70-101`, `backend/app/api/session_deps.py:16-24`).
2. The SPA sends the browser to `/api/auth/oidc/start`. The backend stores `state`, a PKCE verifier and the return path in Redis for 600 s and redirects to Zitadel's authorize endpoint (`backend/app/api/auth_bff.py:151-178`, `backend/app/services/oidc_pending.py:24,60`).
3. Zitadel sends the user to the portal's own login page. The page first tries silent completion with the `klai_sso` cookie (`POST /api/auth/sso-complete`); without a valid one it shows the form (`frontend/src/routes/login.tsx:42-88`, `backend/app/api/auth.py:1702-1750`).
4. `POST /api/auth/login` finds the user, creates a Zitadel session with the password, and applies the org's MFA policy (`optional`, `recommended` or `required`). Bad credentials give a uniform 401 and a Zitadel outage gives 503 (`backend/app/api/auth.py:1409-1566`, `backend/app/models/portal.py:108`).
5. A user with TOTP gets a pending state in Redis (300 s, five failures delete it) and completes with `POST /api/auth/totp-login` (`backend/app/api/auth.py:216,1569-1699`, `backend/app/core/config.py:81`).
6. A user without a workspace whose verified email matches a workspace's primary domain is offered that workspace or can file a join request (`backend/app/api/auth_select.py:188`, `backend/app/models/portal.py:113-119`).
7. The backend finalises the Zitadel auth request, sets the `klai_sso` cookie (encrypted, 90 days) and returns a callback URL checked against an allowlist of hosts (`backend/app/api/auth.py:545-689`, `backend/app/core/config.py:75-76`).
8. The browser follows the callback to `GET /api/auth/oidc/callback`: the backend consumes `state`, exchanges the code with the verifier, verifies the ID token, resolves the org from `portal_users`, stores the session in Redis (encrypted JSON, 30 days) and sets two cookies, one HttpOnly session cookie and one readable CSRF cookie, both for the parent domain (`backend/app/api/auth_bff.py:186-326`, `backend/app/services/bff_session.py:210-246`, `backend/app/core/config.py:89-90`).
9. On each request the session middleware loads the session, refreshes the access token shortly before it expires (one refresh at a time per session) and requires the `X-CSRF-Token` header to equal the session's token on unsafe methods, except on listed prefixes such as the login finishers, `/internal/`, `/partner/` and `/oauth/` (`backend/app/middleware/session.py:32-247`, `backend/app/services/bff_session.py:287-372`). Route handlers receive a bearer derived from the cookie; a bearer header from the client is not accepted (`backend/app/api/bearer.py:21-32`).
10. Logout is `POST /api/auth/bff/logout`: the backend revokes the session and the refresh token, clears the cookies and returns the Zitadel end-session URL, which the SPA follows to `/logged-out` (`backend/app/api/auth_bff.py:105-143`, `frontend/src/lib/auth.tsx:127-155`).

Social login (Google and Microsoft, instance-level identity providers) runs through Zitadel identity-provider intents (`backend/app/api/auth.py:1093-1102,2192-2671`). MFA enrolment endpoints exist for TOTP, passkey and email OTP (`backend/app/api/auth.py:1753-2142`). The portal fails to start when its required secrets, the Zitadel token probe or the RLS readiness checks are not in order (`backend/app/main.py:167-280`).

### Other surfaces

| Surface | Credential | Checked in | Workspace comes from |
|---|---|---|---|
| Portal SPA to portal-api | Session cookie and CSRF header | `klai-portal/backend/app/middleware/session.py:145-247` | `portal_users` by `sub` and the session organisation |
| Portal proxy to scribe-api and docs-app | The session's access token as bearer, an internal secret, and verified-identity headers (client copies are stripped) | `klai-portal/backend/app/api/proxy.py:1-12,152-189` | The verified session identity |
| Partner API (`/partner/v1/*`) | `Bearer pk_live_<40 hex>`, SHA-256 lookup in `partner_api_keys`; the organisation needs the `partner_api` unlock (`slack` for Slack keys) | `klai-portal/backend/app/api/partner_dependencies.py:248-297`, `klai-portal/backend/app/services/partner_keys.py:21-23` | The key row |
| Widget on a client site | `GET /partner/v1/widget-config` checks the page origin against the widget's allowed origins and issues an HS256 JWT valid for one hour, signed with a key derived per workspace; later calls send the JWT; needs the `widgets` unlock | `klai-portal/backend/app/api/partner_widget_config.py:207-261`, `klai-portal/backend/app/services/widget_auth.py:35-170` | The JWT's widget and organisation claims |
| Workspace LibreChat | A per-workspace internal-chat key against the portal's partner endpoint | `klai-portal/backend/app/services/provisioning/generators.py:36-38,325-334` | The key row |
| Internal service calls (`/internal/*`) | `Authorization: Bearer <internal secret>` (some routes a second, narrower secret), constant-time comparison, per-IP rate limit, audit row | `klai-portal/backend/app/api/internal.py:307-345` | The route's path or body, then `set_tenant` |
| Identity checks by other services | `POST /internal/identity/verify` and `/internal/mcp-token/verify` (plus a variant that verifies the workspace), called through `klai-libs/identity-assert` (60 s cache for positive answers only, refuses when the portal is unreachable) | `klai-portal/backend/app/api/internal.py:2116,2408,2800`, `klai-libs/identity-assert/klai_identity_assert/client.py:1-18` | Portal membership |
| portal-api to knowledge-ingest and retrieval-api | `X-Internal-Secret`, a separate secret per target | `klai-portal/backend/app/services/knowledge_ingest_client.py:56`, `klai-portal/backend/app/services/retrieval_context.py:837` | The Zitadel organisation id |
| docs-app | A Zitadel bearer verified against the issuer's published keys, or the internal secret with a user header; the organisation is resolved from the slug in the path and compared with the caller's organisation claim. The public reader needs no login and answers 404 for personal and private knowledge bases | `klai-docs/lib/auth.ts:13-20,34-85,111-131`, `klai-docs/app/(reader)/[...path]/page.tsx:116` | The organisation slug in the path |
| MCP clients | The portal is the OAuth 2.1 authorization server: discovery, dynamic client registration, authorize, token. Tokens are listed and revoked under `/api/me/mcp-tokens`; klai-knowledge-mcp asks the portal to verify each caller's identity | `klai-portal/backend/app/api/mcp_oauth.py:1-12,110-486`, `klai-knowledge-mcp/main.py:1-15` | The token row |
| Slack | Slack's signing secret on events; the link between workspace and organisation goes through internal routes; Slack then calls the Partner API with a managed key stored AES-256-GCM in klai-slack's own database | `klai-slack/app/main.py:75-92`, `klai-slack/app/crypto.py:1-23`, `klai-portal/backend/app/api/internal_slack.py:115-182` | The partner key |
| Browser extension | `ks_live_` tokens (stored as SHA-256), obtained by exchanging a short-lived `ks_code_` | `klai-portal/backend/app/services/shield_tokens.py:9-37`, `klai-portal/backend/app/api/shield.py:309-596` | The token row |
| Zitadel to klai-mailer; portal to klai-mailer | HMAC-signed `POST /notify`; `POST /internal/send` with a shared secret, recipient binding and a per-recipient rate limit | `klai-mailer/app/main.py:164,323-340` | Not applicable |
| Moneybird webhook | Token in the body, constant-time comparison | `klai-portal/backend/app/api/webhooks.py:151-185` | The Moneybird contact |
| LibreChat sign-in | A separate OIDC client per workspace; its login lands on the same portal login page and completes silently | `klai-portal/backend/app/services/provisioning/generators.py:264-303` | The workspace host |

## 5. Models

LiteLLM is the only gateway to language models (`deploy/litellm/config.yaml`). Every text-generation alias is a Mistral model reached through the Mistral API. Each chat alias has three deployments ranked by `order`: two API keys, and a third deployment on a second kind of key that serves as a bridge (`deploy/litellm/config.yaml:38-212`). The callback `klai_mistral_pool` keeps an alias on the lowest-ranked key that Mistral does not report as full (`deploy/litellm/klai_mistral_pool.py:1-7`). Every deployment has a daily spend ceiling that LiteLLM keeps in Redis across restarts (`deploy/litellm/config.yaml:14-31,342-353`), and a spent ceiling raises the `litellm_budget_exhausted` alert (`deploy/grafana/provisioning/alerting/litellm-rules.yaml:10`).

| Alias | Model | Fallback | Used by |
|---|---|---|---|
| `klai-primary` | `mistral-small-2603` | `klai-large` | Chat answers: the Partner API, the workspace LibreChat, the widget and Slack; scribe-api synthesis; portal-api synthesis; retrieval-api synthesis |
| `klai-fast` | `mistral-small-2603` | `klai-large` | Extraction and feedback triage in portal-api and scribe-api; coreference and graph calls in retrieval-api; interactive calls in knowledge-ingest |
| `klai-large` | `mistral-large-2512` | none | Fallback target; tool-call and long-input routing |
| `klai-medium` | `mistral-medium-3.5` | none | Answer grounding and retrieval paraphrase in portal-api; faithfulness checks in knowledge-ingest |
| `klai-judge` | `mistral-vibe-cli-latest`, reasoning off | none, on purpose | Conversation judge and gap proposals in portal-api |
| `klai-ingest` | `mistral-vibe-cli-fast` | none, on purpose | Enrichment, graph extraction, taxonomy and the retrieval-evaluation judge in knowledge-ingest |
| `klai-bge-m3` | `bge-m3` on TEI at gpu-01 | none | Embeddings for evaluation |

Anchors: aliases `deploy/litellm/config.yaml:38,76,105,137,268,300,319`; fallbacks `deploy/litellm/config.yaml:365`; callers `klai-portal/backend/app/core/config.py:306-326`, `klai-portal/backend/app/services/gap_proposals.py:27`, `klai-retrieval-api/retrieval_api/config.py:42,51,55`, `klai-knowledge-ingest/knowledge_ingest/config.py:345-350,390,433,552-554`, `klai-scribe/scribe-api/app/core/config.py:74-75`, `klai-slack/app/clients/partner.py:155`. Only the Partner API's OpenAI-compatible endpoint accepts `klai-primary`, `klai-fast` and `klai-large`, and maps `gpt-4o` and `gpt-4.1` to `klai-primary` and `gpt-4o-mini` and `gpt-4.1-mini` to `klai-fast` for clients that hard-code those names (`klai-portal/backend/app/api/partner_openai_compat.py:34-39`).

**Routing.** The routing strategy is `simple-shuffle` (`deploy/litellm/config.yaml:340`). The callback `custom_router.token_router` re-routes only requests for `klai-primary`: tool-call history or a long last user message goes to `klai-large`, web-search content or a very long context goes to `klai-fast` (`deploy/litellm/custom_router.py:1-15,182-214`). The other callbacks, in order, are `klai_mistral_pool`, `custom_router.token_router`, `klai_pii_observe`, `klai_pii_enforce` and `klai_prompt_cache` (`deploy/litellm/config.yaml:385-400`). PII enforcement applies to every org by default (`deploy/docker-compose.yml:492,519`).

**Self-hosted and external.** Embeddings (dense BGE-M3 on TEI, a sparse sidecar), the reranker (`bge-reranker-v2-m3` on Infinity) and transcription (faster-whisper) run on gpu-01. PII detection runs on core-01. Every generation alias is the external Mistral API (`deploy/litellm/config.yaml:38-300`, `deploy/docker-compose.gpu.yml:11-181`).

**The privacy principle in configuration.** Data stays in the EU and no model call goes to a provider outside the EU. In configuration that means: the production model list contains Mistral deployments and the self-hosted BGE-M3 and nothing else; no alias has a deployment on another provider's API; LibreChat's own OpenAI, Google and Anthropic endpoints are disabled (`deploy/librechat/librechat.yaml:170-175`); and the tenant LibreChat's only model endpoint is "Klai AI", which points at the portal's partner endpoint and not at a provider (`deploy/librechat/librechat.yaml:176-195`). The local development config `dev/litellm-config.yaml:8-27` uses other providers' models and never runs in production. The principle applies to the model operator, not to hosting: an EU provider's API counts, and full self-hosting remains an option for when its cost makes sense (that decision is in [evolution.md](evolution.md)).

## 6. Knowledge stack in one screen

**Ingest.** Content arrives from connectors (klai-connector adapters for Airtable, Confluence, GitHub, Google Drive, a JSON feed, Microsoft documents and Notion), from crawls (crawl4ai), from uploads (docling converts files, portal-api forwards the markdown), from the docs knowledge bases (Gitea) and from the MCP and Scribe save paths. knowledge-ingest enriches with `klai-ingest`, chunks, embeds and writes to Qdrant and the graph on a Procrastinate queue. Detail: [knowledge-ingest-flow.md](knowledge-ingest-flow.md) and [knowledge-base-ingestion-and-retrieval-plan.md](knowledge-base-ingestion-and-retrieval-plan.md). Adapters: `klai-connector/app/adapters/`.

**Stores.** One Qdrant collection with dense, question and sparse vectors per chunk and an `org_id` payload; FalkorDB for the graph; the `knowledge` schema in Postgres for artifacts and job state; Garage for images; Gitea for docs sources. See section 1 and [klai-knowledge-architecture.md](klai-knowledge-architecture.md).

**Retrieval.** retrieval-api serves `POST /retrieve`: hybrid search over the vectors, a graph leg, a reranker on gpu-01, and scope and visibility filters (organisation, personal, private); `POST /chat` adds synthesis. Callers: portal-api (chat and internal routes), klai-knowledge-mcp and the nightly evaluation in knowledge-ingest (`klai-knowledge-ingest/knowledge_ingest/eval/retrieval_client.py:89`). Detail: [knowledge-retrieval-flow.md](knowledge-retrieval-flow.md).

**Chat.** There is one chat path: `POST /partner/v1/chat/completions` on portal-api, which retrieves, decides answer, clarify or refuse, and calls LiteLLM. The tenant LibreChat, the widget and Slack all use it. LibreChat remains the interface of the internal chat and sends its requests to that endpoint (`deploy/librechat/librechat.yaml:176-195`). Detail: [chat-system.md](chat-system.md) and, for the quality work, [chat-quality-history-and-plan.md](chat-quality-history-and-plan.md).

## 7. Other product surfaces

- **Widget.** An embeddable chat bundle (`klai-chat.js`, SolidJS, a gzip limit of 200 kB enforced in the build: `klai-widget/scripts/check-bundle-size.mjs:27`). A site embeds it with one script tag that loads `https://my.getklai.com/widget/klai-chat.js` with a `data-widget-id` attribute (`klai-portal/frontend/src/features/widgets/embed/snippet.ts:1`); a contract test keeps this document and the product snippet on the same URL (`rules/tests/test_widget_distribution_contract.py:57-59`). The portal frontend workflow builds it and ships it with the SPA (`.github/workflows/portal-frontend.yml:136-143`). It reaches the platform through `widget-config` and the Partner API, authenticated as in section 4. Code: `klai-widget/`; server side in `klai-portal/backend/app/api/partner_widget_config.py`.
- **Partner API.** OpenAI-compatible chat and responses endpoints, knowledge, feedback, models and support sessions under `/partner/v1`, with per-org keys and the `partner_api` unlock. Code: `klai-portal/backend/app/api/partner*.py`.
- **Slack.** A FastAPI app with Bolt mounted on `/slack/*`: events, the `/klai` command, interactivity, install and OAuth redirect. It answers through the Partner API with `klai-primary` and keeps its own database role and encrypted tokens (`klai-slack/app/main.py:75-92`, `klai-slack/app/clients/partner.py:140-197`). Code: `klai-slack/`.
- **Browser extension.** A Chrome extension (manifest v3) with a side panel; it logs in through the portal's extension endpoints and calls `/api/shield/config`, `/check`, `/log` and `/query`. The portal builds the downloadable zip from its own copy of the extension (`klai-portal/backend/app/api/shield.py:52-53,223`, `klai-shield-extension/src/background/service-worker.js:131-314`). Two MCP tools expose the compliance check to platform admins (`klai-knowledge-mcp/main.py:1713,1742`). Code: `klai-shield-extension/`, `klai-portal/backend/app/api/shield.py`.
- **HubSpot app.** A HubSpot Projects app with a help-desk card and a function that calls the Partner API (`support-sessions` and `chat/completions`). It is uploaded by hand to a HubSpot sandbox account and has no production deployment (`klai-hubspot/README.md`, `klai-hubspot/klai-email-support/src/app/functions/NewFunction.js:452,484,602`). Code: `klai-hubspot/`.
- **Scribe and meeting transcription.** Two paths. Uploaded audio goes to scribe-api, which normalises it, queues a row in the `scribe` schema, calls the OpenAI-compatible transcription endpoint with `transcription_tier=deferred`, and summarises with `klai-fast` and `klai-primary` (`klai-scribe/scribe-api/app/api/transcribe.py:158-477`, `klai-scribe/scribe-api/app/services/providers.py:126-200`, `klai-scribe/scribe-api/app/services/summarizer.py:29,173`). Meetings are joined by bots of the Vexa stack, controlled by portal-api (`klai-portal/backend/app/services/vexa.py:101`, `klai-portal/backend/app/services/bot_poller.py`). Both paths use the transcription workers on gpu-01. Recordings are deleted after transcription (section 3). A data subject can export their data with `POST /api/me/sar-export` (`klai-portal/backend/app/api/me.py:532`). Contract and contributor model: [scribe-transcription.md](scribe-transcription.md). Code: `klai-scribe/scribe-api/`.
- **Docs app.** A Next.js reader and editor for the docs knowledge bases, with Gitea as the content store, under `basePath /docs` on port 3010 (`klai-docs/next.config.ts:5`). Its editor API is not published; portal users reach it through the portal proxy. Code: `klai-docs/`.
- **MCP server.** A streamable-HTTP MCP server at `/mcp` with ten tools: save to personal or organisation knowledge, list, read, create and update docs pages, search knowledge, and the two compliance tools (`klai-knowledge-mcp/main.py:891,960,1028,1058,1115,1195,1350,1546,1728,1757`). Tenant LibreChat containers reach it inside the network (`deploy/librechat/librechat.yaml:140-143`); external MCP clients reach it through its own host and the portal's OAuth endpoints. Code: `klai-knowledge-mcp/`.
- **Mailer.** Receives Zitadel's signed notifications, wraps the message texts in one HTML theme and sends them over SMTP; portal-api uses `POST /internal/send` for its own templates, with the recipient's language from `/internal/user-language` (`klai-mailer/app/main.py:164,323`, `klai-mailer/app/portal_client.py:22-45`). Code: `klai-mailer/`.

## 8. Deployment

Public artifacts are built and tested in this repository; production deployment is operated from private infrastructure automation, and the host-level inputs (server setup, the secret store, the tunnel to gpu-01) are outside this repository. What follows is the contract between a merge and running containers, using the workflow and script names in this repository (`.github/workflows/`, `deploy/scripts/`).

1. **Gates on the pull request.** Per-area workflows test each service, and repository guards run on every change: `quality.yml`, `audit-compose.yml`, `env-scope-guard.yml`, `public-tenant-data.yml`, `semgrep.yml`, `measurement-contract.yml`. Deploy jobs only run on `main`.
2. **Build.** A push to `main` that touches a service builds its image, tags it with the git sha and `latest`, pushes it to GHCR and scans it (for example `.github/workflows/knowledge-ingest.yml:227-229`).
3. **Deploy by exact sha.** The workflow runs `deploy/scripts/deploy-with-rollback.sh <service> <image@sha> --probe <health url>` on the host. It deploys the sha, never the registry's `latest` (`deploy/scripts/deploy-with-rollback.sh:1-30`). It pulls, recreates the service through `deploy/scripts/compose-up.sh`, and waits for an HTTP 2xx on the health URL for up to 60 s.
4. **Proof.** For services with an error watch, `deploy/scripts/post-deploy-error-watch.sh` compares error lines in the logs after the deploy with the window before it and fails on more than ten lines and more than twice the earlier rate (`deploy/scripts/post-deploy-error-watch.sh:1-40`).
5. **Automatic rollback.** A failed probe or error watch restores the previous image (exit codes distinguish rolled back from rollback failed). The rollback report is stored as an artifact and feeds `ci-failure-notice.yml`, which keeps one issue per failing workflow on `main`.
6. **The portal as the special case.** `deploy/scripts/deploy-portal-api.sh` first runs the Alembic migrations and the `post_deploy_*.sql` files (the RLS policies) from the new image, then swaps the container through the same rollback script. Rollback is image-only and never downgrades the database; destructive migrations are blocked in pull requests by `scripts/check-expand-contract.py` (`deploy/scripts/deploy-portal-api.sh:1-24,123-157`, `.github/workflows/portal-api.yml:123-131`). The portal frontend is built by `portal-frontend.yml` (widget first), synced to the host and served by Caddy (`.github/workflows/portal-frontend.yml:169-170`).
7. **Compose and configuration sync.** `deploy-compose.yml` runs on changes to the compose files, the Caddyfile, Alloy, SearXNG, LibreChat entry points and Grafana provisioning. It validates the pins and the pullability of every image, then in one session replaces the compose files, installs the scripts and systemd units, syncs Grafana provisioning, recreates every service whose declaration changed (`deploy/scripts/compose-env-drift-recreate.sh`; LiteLLM is excluded on purpose) and runs the gates. A data store whose image changes is first snapshotted and restored into a shadow copy of the new version, and cut over only when the shadow passes (`deploy/scripts/datastore-rollback.sh`). Each gate is wrapped so that a red gate restores the previous files and recreates (`deploy/scripts/compose-rollback.sh:1-35`, `.github/workflows/deploy-compose.yml:145-525`). One run at a time, queued and not cancelled (`.github/workflows/deploy-compose.yml:49-51`). LiteLLM hooks and config go out through `litellm-hook-deploy.yml`; the LibreChat and Presidio images are built by `librechat-image-build.yml` and `presidio-analyzer-image-build.yml` and pinned by digest.
8. **After a deploy.** `e2e-prod-tenant.yml` runs against a production test tenant. When it ends red on `main`, `auto-rollback.yml` finds the last green run, runs one confirmation E2E, and only if that is red as well replays the deploy ledger backwards with `deploy/scripts/rollback-since.sh` (`.github/workflows/auto-rollback.yml:1-35,145-160`).
9. **Rehearsals.** `rollback-rehearsal.yml` deploys a good and a bad version of `rollback-canary` every Monday and asserts that the bad one is rolled back (`deploy/scripts/rollback-rehearsal-assert.sh:1-25`); the Grafana rule `rollback_rehearsal_missing` fires after eight days of silence. `datastore-restore-rehearsal.yml` runs on the 1st and 15th and restores the newest deploy snapshot of every data store into a throwaway container without network access, requiring the restored facts to equal the baseline (`deploy/scripts/datastore-restore-rehearsal.sh:1-30`).
10. **Versions.** Renovate runs self-hosted and merges updates automatically after a three-day release age (`renovate.json5:13,86,102`); `version-drift.yml` checks daily for stuck update pull requests and locally built images, and `scan-pinned-images.yml` scans the pinned images weekly.

Not in this repository: the host `.env` and the encrypted secrets (the inventory of who reads which variable is `deploy/SECRETS_MATRIX.md`), the Docker daemon configuration (`deploy/docker/daemon.json`) and the firewall hardening script (`deploy/scripts/harden-docker-user.sh`) are templates, applied on the host.

## 9. Observability and backup

**Logs.** Alloy discovers containers through the read-only Docker proxy, labels each line with `container`, `service` and `host`, extracts the level, and routes by `retention_class`: lines of class `content` go to the 7-day VictoriaLogs instance, everything else to the 30-day instance (`deploy/alloy/config.alloy:15-103,168-222`, `deploy/docker-compose.yml:1332,1356,1408`). It also reads three host log files written by the probes below. Caddy sets `X-Request-ID` when it is missing and writes a JSON access log with the `token` query parameter removed (`deploy/caddy/Caddyfile:19-51`). Remote hosts push logs through the bearer-gated ingest endpoint (`deploy/caddy/Caddyfile:103-119`).

**Metrics.** Alloy collects node metrics, cAdvisor, and the `/metrics` endpoints of retrieval-api and portal-api, and writes to VictoriaMetrics (`deploy/alloy/config.alloy:225-291`).

**Dashboards and data sources.** Grafana provisions its data sources (VictoriaMetrics, both VictoriaLogs instances and the portal database through a read-only role) and dashboards for logs, containers and nodes, health, the ingest pipeline, retrieval quality, privacy, product, business, provisioning and proxy denials (`deploy/grafana/provisioning/datasources/datasources.yaml`, `deploy/grafana/provisioning/datasources/portal-postgres.yaml`, `deploy/grafana/provisioning/dashboards/`). Product events are rows in `product_events` in the portal database (`klai-portal/backend/app/models/events.py:10-11`).

**Alerting.** Alert rules are YAML files per area in `deploy/grafana/provisioning/alerting/` (Caddy, ingest, LiteLLM, Mistral API, persistence, auto-rollback, portal auth and MFA, privacy, mail chain, meetings, widget probe and more), routed by `policies.yaml` to the contact points in `contact-points.yaml`; `alerting-check.yml` validates them in CI. Host timers run the probes that feed the rules: persistence every 10 minutes, the Mistral key probe every 2 minutes, the widget probe every 30 minutes, an orphan audit weekly and a Docker cleanup daily (`deploy/systemd/`). Errors from the applications go to GlitchTip, which is Sentry-compatible (`deploy/docker-compose.yml:1247`).

**Backup.** `deploy/scripts/backup.sh` is the single nightly backup script (the schedule is a host setting). In order it dumps the main Postgres instance, Gitea, MongoDB, the Redis instances, Meilisearch, Qdrant, FalkorDB, Garage (metadata and blobs), the Postgres databases of the third-party applications, and the Scribe audio and research upload volumes. Each artifact is encrypted with age to two recipients and copied to an off-host storage box (`deploy/scripts/backup.sh:756-795,876-896`). A failed step does not stop the later steps (`deploy/scripts/backup.sh:200`) and the result is pushed to the status page. Locally the newest seven completed day-sets are kept, and pruning only happens after a night without failed steps (`deploy/scripts/backup.sh:48,797-831`). VictoriaMetrics and both VictoriaLogs instances are deliberately not backed up. `deploy/volume-mounts.yaml` lists every volume with its backup class, and `scripts/audit-compose-volumes.sh` enforces that every mount has an entry.

**Restore rehearsal.** The deploy snapshots, which are taken only when a data store's image changes, are restored into throwaway containers twice a month (section 8, step 9).

## 10. Where the rest is

- How the platform came to be, the decisions with their reasons, the plans that were never built and what no longer exists: [evolution.md](evolution.md) (Dutch).
- Ingest: [knowledge-ingest-flow.md](knowledge-ingest-flow.md). Retrieval: [knowledge-retrieval-flow.md](knowledge-retrieval-flow.md). Chat: [chat-system.md](chat-system.md). Knowledge model and target design: [klai-knowledge-architecture.md](klai-knowledge-architecture.md).
- Transcription contract: [scribe-transcription.md](scribe-transcription.md). Open gaps between design and code: [product-gaps-backlog.md](product-gaps-backlog.md).
- Pinned versions and the reason for each pin: `deploy/VERSIONS.md`. Secrets per service: `deploy/SECRETS_MATRIX.md`. Self-hosting: [deploy/README.md](../../deploy/README.md).
- Runbooks: `docs/runbooks/`. Local development: `GETTING_STARTED.md`. The public and private boundary: [infrastructure-boundary.md](../contributing/infrastructure-boundary.md).
