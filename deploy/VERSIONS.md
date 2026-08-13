# Explicit Version Pins

This file documents public-repo external image pins for the core stack and dev stack, plus the rationale for each pin. **Every external image in `docker-compose.yml` and `docker-compose.dev.yml` is pinned to an explicit version tag.** No `:latest` on external services.

Automated dependency updates are handled by Dependabot / Renovate. Upgrades follow `docs/runbooks/version-management.md`.

**Exception — internal CI-deployed services:** images under `ghcr.io/getklai/*` (portal-api, retrieval-api, knowledge-ingest, klai-connector, klai-mailer, klai-docs, klai-knowledge-mcp, scribe-api, caddy-hetzner, whisper-server) use `:latest` because GitHub Actions rebuild and re-push on every commit to `main` in their respective repos. (research-api removed in SPEC-PORTAL-UNIFY-KB-001.) Each CI workflow also tags the build with `:${github.sha}` so rollbacks are possible via explicit SHA pin. These are NOT production `:latest` anti-patterns — they are continuous-deployment rolling tags owned by our own CI pipelines. The self-host compose file expects every referenced `ghcr.io/getklai/*` image to be public and anonymously pullable; `deploy/check-image-pullable.sh` enforces that contract.

**Exception — local builds:** `klai/retrieval-api:local` and `ghcr.io/mendableai/firecrawl:latest` are built on-host from source and not pullable from a registry. Their "versions" are tracked by git SHAs recorded in docker-compose.yml comments.

**Exception — Vexa stack (upstream v0.10.6.3.14, 2026-06-07):** `vexaai/admin-api`, `vexaai/api-gateway`, `vexaai/meeting-api`, `vexaai/runtime-api`, `vexaai/vexa-bot` are currently on `0.10.6.3.14` — pulled directly from Docker Hub (since v0.10.4 upstream publishes pre-built images). `deploy/check-image-tags.sh` enforces upstream version or timestamped-version tag form (no `:latest` / `:dev` / `:staging`). Upgrade cadence: track upstream stable tags; bump for material fixes (chunk-leak, OOM, security, browser/admission fixes). See `https://github.com/Vexa-ai/vexa/releases` for changelog.

GPU production image pins are intentionally not listed in this public repo because the live GPU compose, operator runbooks, host paths, and tunnel details belong in `GetKlai/klai-infra`.

---

## Core stack — `deploy/docker-compose.yml`

### Database layer

| Service | Image | Rationale |
|---|---|---|
| `postgres` | `pgvector/pgvector:0.8.6-pg18` | pgvector 0.8.6 on pg18 (bumped 2026-08-13, same Postgres major — data-compatible). PostgreSQL major version upgrades require dump/restore. pg18 is the current stable (since Sept 2025). Upgrade path: `pg_dumpall` → stop services → change image → delete volume → restore dump. |
| `firecrawl-postgres` | `postgres:18.4-alpine` | Firecrawl-internal queue DB (NUQ schema). Pinned to match main postgres major. Data is transient (queue state), so cross-major migration is just a volume delete. |
| `listmonk-db` | `postgres:17.10-alpine` | Dedicated database for listmonk campaigns, subscribers, templates, and admin users. Kept on the upstream listmonk Docker Compose default; dump/restore before any major PostgreSQL bump. |
| `mongodb` | `mongo:8.2.12` | MongoDB 8 is the current stable major. LibreChat tenants depend on this. Major upgrades require replica-set-aware migration. |
| `redis` | `redis:8.10.0-alpine` | Bumped 2026-08-13: 8.8.1 was a security release on our 8.8.0 pin; 8.10.0 is current stable on the same major. Redis 8 (GA Aug 2025) ships Vector Sets + hash-field-TTL. |
| `vexa-redis` | `redis:8.10.0-alpine` | Aligned with main redis major. Isolated network; bot state + pub/sub + transcription streams. |
| `qdrant` | `qdrant/qdrant:v1.19.0` | Vector store for Klai Knowledge. Binary-incompatible on major bumps — pin explicitly. |
| `falkordb` | `falkordb/falkordb:v4.20.2` | Knowledge graph (Graphiti backend). v4.x has stable RediSearch + graph engine integration. |

### Auth + monitoring

| Service | Image | Rationale |
|---|---|---|
| `zitadel` | `ghcr.io/zitadel/zitadel:v4.17.0` | OIDC IdP. Bumped 2026-08-13 for the 4.16.x security batch: 2× critical (unauthenticated account takeover via passkey enrollment; account pre-hijacking via forged external-IdP callback) + MFA-bypass + Actions sandbox escape, all patched ≤4.16.2. [HIGH] Minor upgrades sometimes invalidate portal-api PAT — see `.claude/rules/klai/platform/zitadel.md`. Rotate PAT after each bump. |
| `victoriametrics` | `victoriametrics/victoria-metrics:v1.149.0` | Metrics TSDB. Bumped 2026-08-13 (includes the vmrestore path-traversal fix, CVE-2026-61625). |
| `victorialogs` | `victoriametrics/victoria-logs:v1.52.0` | Log aggregation (replaces Loki). LogsQL syntax differs from LogQL. |
| `cadvisor` | `ghcr.io/google/cadvisor:v0.60.5` | Container metrics. Registry moved from `gcr.io` to `ghcr.io`; verify dashboards that depend on container start/creation timestamps after this bump. |
| `alloy` | `grafana/alloy:v1.18.1` | Log and metric collection. Config format stable on minor bumps. |
| `grafana` | `grafana/grafana:13.1.3` | Dashboard UI. v12 → v13 had breaking dashboard JSON changes — verify dashboards after any major bump. |
| `glitchtip-web`, `glitchtip-worker`, `glitchtip-migrate` | `glitchtip/glitchtip:6.2.6` | Error tracking. All three share the same image (different commands). |

### Inference + AI

| Service | Image | Rationale |
|---|---|---|
| `litellm` | `ghcr.io/berriai/litellm:v1.96.2` | Pinned explicitly (moved from rolling `:main-stable` on 2026-04-19). Re-assess monthly. Note: upstream discontinued the `-stable` tag suffix family — plain tags on non-prerelease GitHub releases are the stable line now (verified 2026-08-13). |
| `ollama` | `ollama/ollama:0.32.9` | CPU fallback for LLM inference. |
| `librechat` | `ghcr.io/danny-avila/librechat:v0.8.6` | LibreChat UI for all tenants. Compose-managed `librechat-getklai` and provisioning-managed tenant containers are pinned to the same image. Mounted v0.8.6 patches live under `deploy/librechat/patches/`; getklai keeps an identical canary copy under `deploy/librechat/getklai/patches/` until the separate compose service is folded back into provisioning. The entrypoints also patch LibreChat Meili runtime paths to tenant-scoped indexes when tenant index envs are configured; verify this block against the image on every LibreChat upgrade. |

### Document + search

| Service | Image | Rationale |
|---|---|---|
| `meilisearch` | `getmeili/meilisearch:v1.45.2` | Search index for LibreChat conversations. **Data migration required on minor bumps** — v1.42.1 refused to boot directly on v1.45.2 and required dump/import. Pin explicitly and keep `MEILI_DB_PATH=/meili_data`; v1.45.2 otherwise starts on `./data.ms` and ignores the mounted volume. |
| `docling-serve` | `ghcr.io/docling-project/docling-serve:v1.30.0` | Document parsing (PDF, DOCX → structured). |
| `searxng` | `searxng/searxng:2026.8.12-cdfdaa5a8` | Meta-search aggregator for LibreChat web mode. Date-based versioning. |
| `gitea` | `gitea/gitea:1.27.1` | Self-hosted git for klai-docs. Bumped 2026-08-13: 1.27.1 patches CVE-2026-59774 (critical, unauthenticated file read → RCE) + CVE-2026-60004 (critical, RCE via diffpatch hooks); 1.27.0 patches the high SSRF + PAT-scope CVEs. 1.27.0 breaking changes (Actions reusable workflows, CSP script nonce) do not affect us — Gitea is git-hosting only here. |
| `crawl4ai` | `unclecode/crawl4ai:0.8.9` | Web crawler for klai-connector. Hooks are explicitly disabled in compose; Klai uses crawl request config, not Crawl4AI server hooks. |

### Ops

| Service | Image | Rationale |
|---|---|---|
| `docker-socket-proxy` | `tecnativa/docker-socket-proxy:v0.5.0` | Limits portal-api to specific Docker API verbs (CONTAINERS, NETWORKS, POST, DELETE). Stable; rare releases. |
| `runtime-api-socket-proxy` | `alpine/socat:1.8.0.3` | Bridges Vexa runtime-api's Unix Docker socket expectation to docker-socket-proxy TCP. Keep current with Alpine socat releases because old tags carry many fixed base-package CVEs. |
| `garage` | `dxflrs/garage:v2.3.0` | S3-compatible object storage. Config field names change between minor releases — re-verify `garage.toml` after each bump. See `.claude/rules/klai/platform/garage.md`. |
| `listmonk` | `listmonk/listmonk:v6.2.0` | Self-hosted mailing platform at `mailing.getklai.com` for campaign templates, lists, and Twenty-selected audiences. Upgrade after checking listmonk release notes and database migrations. |

### Pinned with known upstream gap

| Service | Image | Why stuck |
|---|---|---|
| `firecrawl-rabbitmq` | `rabbitmq:3-alpine` | RabbitMQ 4.0 made AMQP 1.0 the default protocol (breaking change from 3.x). [Firecrawl](https://github.com/firecrawl/firecrawl) has not published confirmed RabbitMQ 4.x support. Upgrade only after Firecrawl releases a compatibility statement. Current latest is 4.3.2-alpine. |

---

## Dev stack — `docker-compose.dev.yml`

Use the same versions as your deployed environment to catch version-related
issues locally.

| Service | Image | Notes |
|---|---|---|
| `postgres` | `pgvector/pgvector:0.8.6-pg18` | Same as prod. |
| `redis` | `redis:8.10.0-alpine` | Aligned with prod (was `redis:alpine`). |
| `mongodb` | `mongo:8.2.12` | Same as prod. |
| `meilisearch` | `getmeili/meilisearch:v1.45.2` | Aligned with prod; keep `MEILI_DB_PATH=/meili_data` in dev as well. |
| `litellm` | `ghcr.io/berriai/litellm:v1.96.2` | Same as prod. |

---

## Application dependencies

### Python packages with version upper bounds

| Package | Service | Constraint | Why |
|---|---|---|---|
| `procrastinate>=3.0,<4` | knowledge-ingest | `<4` | Major version bump would require DB schema migration. API unchanged between 2.x and 3.x (PsycopgConnector, open_async(), run_worker_async()). |
| `graphiti-core>=0.28,<0.30` | retrieval-api | `<0.30` | 0.30.x is pre-release with breaking API changes. When 0.30.x stabilises on PyPI, test retrieval-api against it and remove the upper bound. |
| `icalendar>=6.1,<8.0` | portal-api | `<8.0` | Defensive — calendar parsing is brittle. Upper bound prevents surprise 8.0 breakage until we can validate. |
| `prometheus-client>=0.21,<1.0` | portal-api | `<1.0` | 1.0 release expected to change metric registry semantics. |

### Python runtime

`python:3.13-slim` across all internal services. See `docs/runbooks/version-management.md` §3.6 for the upgrade procedure (5 files must change in lock-step).

---

## Verification

To audit drift between this file and a running server, run:

```bash
ssh <server> "docker ps --format '{{.Names}}\t{{.Image}}' | sort"
```

Every row must match an entry in this file. New services must be added here **before** they ship.

---

## Automated CVE scanning

Every image in this file is scanned weekly for CRITICAL/HIGH CVEs by `.github/workflows/scan-pinned-images.yml`. Findings land in the [Security tab → Code scanning](https://github.com/GetKlai/klai/security/code-scanning). Renovate opens PRs when newer upstream image tags exist; Trivy findings in third-party images that are already on the latest stable tag require upstream rebuilds, Klai-owned derived images, or documented temporary acceptance.

See `docs/runbooks/version-management.md` §9 for the full CVE detection stack.

---

*Last repo pin sync: 2026-07-08 — external compose pins refreshed for patch/minor image updates. Meilisearch, LibreChat, Crawl4AI, RabbitMQ, Cal.com, Garage, Qdrant, Vaultwarden, and docker-socket-proxy intentionally left on existing pins because the latest available tag either requires a separate migration/compatibility check, is already current, or has no clean security-risk reduction.*
