# Explicit Version Pins

This file documents every external image version running on core-01 and gpu-01, plus the rationale for each pin. **Every external image in `docker-compose.yml`, `docker-compose.gpu.yml`, and `docker-compose.dev.yml` is pinned to an explicit version tag.** No `:latest` on external services.

Automated dependency updates are handled by Dependabot / Renovate. Upgrades follow `docs/runbooks/version-management.md`.

**Exception — internal CI-deployed services:** images under `ghcr.io/getklai/*` (portal-api, research-api, retrieval-api, knowledge-ingest, klai-connector, klai-mailer, klai-docs, klai-knowledge-mcp, scribe-api, caddy-hetzner, whisper-server) use `:latest` because GitHub Actions rebuild and re-push on every commit to `main` in their respective repos. Each CI workflow also tags the build with `:${github.sha}` so rollbacks are possible via explicit SHA pin. These are NOT production `:latest` anti-patterns — they are continuous-deployment rolling tags owned by our own CI pipelines.

**Exception — local builds:** `vexa-runtime-api:klai`, `vexa-meeting-api:klai`, `klai/retrieval-api:local`, and `ghcr.io/mendableai/firecrawl:latest` are built on-host from source and not pullable from a registry. Their "versions" are tracked by git SHAs recorded in docker-compose.yml comments.

---

## Core stack — `deploy/docker-compose.yml`

### Database layer

| Service | Image | Rationale |
|---|---|---|
| `postgres` | `pgvector/pgvector:pg18` | PostgreSQL major version upgrades require dump/restore. pg18 is the current stable (since Sept 2025). Upgrade path: `pg_dumpall` → stop services → change image → delete volume → restore dump. |
| `firecrawl-postgres` | `postgres:18-alpine` | Firecrawl-internal queue DB (NUQ schema). Pinned to match main postgres major. Data is transient (queue state), so cross-major migration is just a volume delete. |
| `mongodb` | `mongo:8.2.7` | MongoDB 8 is the current stable major. LibreChat tenants depend on this. Major upgrades require replica-set-aware migration. |
| `redis` | `redis:8-alpine` | Redis 8 (GA Aug 2025) ships Vector Sets + hash-field-TTL. Previously on `redis:alpine` which silently rolled to 8 anyway — now explicit. |
| `vexa-redis` | `redis:8-alpine` | Aligned with main redis major. Isolated network; bot state + pub/sub + transcription streams. |
| `qdrant` | `qdrant/qdrant:v1.17.1` | Vector store for Klai Knowledge. Binary-incompatible on major bumps — pin explicitly. |
| `falkordb` | `falkordb/falkordb:v4.18.1` | Knowledge graph (Graphiti backend). v4.x has stable RediSearch + graph engine integration. |

### Auth + monitoring

| Service | Image | Rationale |
|---|---|---|
| `zitadel` | `ghcr.io/zitadel/zitadel:v4.13.1` | OIDC IdP. [HIGH] Minor upgrades sometimes invalidate portal-api PAT — see `.claude/rules/klai/platform/zitadel.md`. Rotate PAT after each bump. |
| `victoriametrics` | `victoriametrics/victoria-metrics:v1.140.0` | Metrics TSDB. |
| `victorialogs` | `victoriametrics/victoria-logs:v1.50.0` | Log aggregation (replaces Loki). LogsQL syntax differs from LogQL. |
| `cadvisor` | `gcr.io/cadvisor/cadvisor:v0.55.1` | Container metrics. **Upstream is stagnant** (last release Dec 2024). Acceptable; no alternative has equivalent Docker metric granularity. |
| `alloy` | `grafana/alloy:v1.15.1` | Log and metric collection. Config format stable on minor bumps. |
| `grafana` | `grafana/grafana:13.0.1` | Dashboard UI. v12 → v13 had breaking dashboard JSON changes — verify dashboards after any major bump. |
| `glitchtip-web`, `glitchtip-worker`, `glitchtip-migrate` | `glitchtip/glitchtip:6.1.6` | Error tracking. All three share the same image (different commands). |

### Inference + AI

| Service | Image | Rationale |
|---|---|---|
| `litellm` | `ghcr.io/berriai/litellm:main-stable` | **Rolling tag** maintained upstream as `main-stable` (curated stable releases). LiteLLM's fastest-moving project — we trade reproducibility for keeping up with provider SDK changes. Pin to explicit `v1.x.x` if LiteLLM stability becomes an issue. |
| `ollama` | `ollama/ollama:0.21.0` | CPU fallback for LLM inference. |
| `librechat-getklai` | `ghcr.io/danny-avila/librechat:v0.8.5-rc1` | Multi-tenant chat UI. **Currently on RC1** — rolled in from `:latest` during 2026-04-19 audit. 3 mounted CJS patches (`format.cjs`, `stream.cjs`, `search.cjs`) target internal paths in `@librechat/agents` — any LibreChat upgrade must re-verify those patches against the new bundle. Goal: move to stable v0.8.5 when released. |

### Document + search

| Service | Image | Rationale |
|---|---|---|
| `meilisearch` | `getmeili/meilisearch:v1.42.1` | Search index for LibreChat conversations. **Data migration required on minor bumps** — breaking v1.40 → v1.42 schema change. Pin explicitly to control migration timing. |
| `docling-serve` | `ghcr.io/docling-project/docling-serve:v1.16.1` | Document parsing (PDF, DOCX → structured). |
| `searxng` | `searxng/searxng:2026.4.17-e8299a4c3` | Meta-search aggregator for LibreChat web mode. Date-based versioning. |
| `gitea` | `gitea/gitea:1.26.0` | Self-hosted git for klai-docs. |
| `crawl4ai` | `unclecode/crawl4ai:0.8.6` | Web crawler for klai-connector. |

### Ops

| Service | Image | Rationale |
|---|---|---|
| `docker-socket-proxy` | `tecnativa/docker-socket-proxy:v0.4.2` | Limits portal-api to specific Docker API verbs (CONTAINERS, NETWORKS, POST, DELETE). Stable; rare releases. |
| `garage` | `dxflrs/garage:v2.3.0` | S3-compatible object storage. Config field names change between minor releases — re-verify `garage.toml` after each bump. See `.claude/rules/klai/platform/garage.md`. |

### Pinned with known upstream gap

| Service | Image | Why stuck |
|---|---|---|
| `firecrawl-rabbitmq` | `rabbitmq:3-alpine` | RabbitMQ 4.0 made AMQP 1.0 the default protocol (breaking change from 3.x). [Firecrawl](https://github.com/firecrawl/firecrawl) has not published confirmed RabbitMQ 4.x support. Upgrade only after Firecrawl releases a compatibility statement. Current latest is 4.2.5-alpine. |

---

## GPU stack — `deploy/docker-compose.gpu.yml`

| Service | Image | Rationale |
|---|---|---|
| `tei` | `ghcr.io/huggingface/text-embeddings-inference:1.9` | BGE-M3 dense embeddings. **Output-dimension critical** — verify bge-m3 embedding parity (same vector output for same input) before any upgrade, otherwise retrieval scores silently drift. |
| `infinity` | `michaelf34/infinity:0.0.77` | BGE reranker-v2-m3. Upstream slowing (last release Aug 2025). |
| `whisper-server` | `ghcr.io/getklai/whisper-server:latest` | Internal build. `faster-whisper` requires CUDA 12 + cuDNN 9. |
| `bge-m3-sparse` | built from `./bge-m3-sparse` | Local build. Sparse embeddings sidecar for hybrid retrieval. |

---

## Dev stack — `docker-compose.dev.yml`

Uses the same versions as production core-01 to catch version-related issues locally.

| Service | Image | Notes |
|---|---|---|
| `postgres` | `pgvector/pgvector:pg18` | Same as prod. |
| `redis` | `redis:8-alpine` | Aligned with prod (was `redis:alpine`). |
| `mongodb` | `mongo:8.2.7` | Same as prod. |
| `meilisearch` | `getmeili/meilisearch:v1.42.1` | Aligned with prod (was v1.13 — multi-major skew). |
| `litellm` | `ghcr.io/berriai/litellm:main-stable` | Same rolling tag as prod. |

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

`python:3.13-slim` across all internal services. See `docs/runbooks/version-management.md` §3.5 for the upgrade procedure (5 files must change in lock-step).

---

## Verification

To audit drift between this file and the running server, run:

```bash
ssh core-01 "docker ps --format '{{.Names}}\t{{.Image}}' | sort"
```

Every row must match an entry in this file. New services must be added here **before** they ship.

---

## Automated CVE scanning

Every image in this file is scanned weekly for CRITICAL/HIGH CVEs by `.github/workflows/scan-pinned-images.yml`. Findings land in the [Security tab → Code scanning](https://github.com/GetKlai/klai/security/code-scanning). When a CVE-fixed version is available, Dependabot raises a PR automatically via GitHub's built-in security updates (enabled at the repo level).

See `docs/runbooks/version-management.md` §9 for the full CVE detection stack.

---

*Last verified: 2026-04-19 — all images in core-01 `docker ps` match this file.*
