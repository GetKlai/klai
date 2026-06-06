# Explicit Version Pins

This file documents every external image version running on core-01 and gpu-01, plus the rationale for each pin. **Every external image in `docker-compose.yml`, `docker-compose.gpu.yml`, and `docker-compose.dev.yml` is pinned to an explicit version tag.** No `:latest` on external services.

Automated dependency updates are handled by Dependabot / Renovate. Upgrades follow `docs/runbooks/version-management.md`.

**Exception — internal CI-deployed services:** images under `ghcr.io/getklai/*` (portal-api, retrieval-api, knowledge-ingest, klai-connector, klai-mailer, klai-docs, klai-knowledge-mcp, scribe-api, caddy-hetzner, whisper-server) use `:latest` because GitHub Actions rebuild and re-push on every commit to `main` in their respective repos. (research-api removed in SPEC-PORTAL-UNIFY-KB-001.) Each CI workflow also tags the build with `:${github.sha}` so rollbacks are possible via explicit SHA pin. These are NOT production `:latest` anti-patterns — they are continuous-deployment rolling tags owned by our own CI pipelines.

**Exception — local builds:** `klai/retrieval-api:local` and `ghcr.io/mendableai/firecrawl:latest` are built on-host from source and not pullable from a registry. Their "versions" are tracked by git SHAs recorded in docker-compose.yml comments.

**Exception — Vexa stack (upstream v0.10.6.3, 2026-06-06):** `vexaai/admin-api`, `vexaai/api-gateway`, `vexaai/meeting-api`, `vexaai/runtime-api`, `vexaai/vexa-bot` are currently on `0.10.6.3` — pulled directly from Docker Hub (since v0.10.4 upstream publishes pre-built images). `deploy/check-image-tags.sh` enforces upstream version or timestamped-version tag form (no `:latest` / `:dev` / `:staging`). Upgrade cadence: track upstream stable tags; bump for material fixes (chunk-leak, OOM, security). See `https://github.com/Vexa-ai/vexa/releases` for changelog. **Exception within the exception**: `vexaai/transcription-service` is NOT published to Docker Hub — it stays locally-built on gpu-01 from upstream source (CUDA + faster-whisper). Currently `0.10.6.2-local-260524-1610` (built from upstream `v0.10.6.2` tag, 2026-05-24). The `<semver>-local-YYMMDD-HHMM` convention is whitelisted by `deploy/check-image-pullable.sh` (no registry manifest exists). Bumping runbook: `docs/runbooks/transcription-service-bump.md`.

---

## Core stack — `deploy/docker-compose.yml`

### Database layer

| Service | Image | Rationale |
|---|---|---|
| `postgres` | `pgvector/pgvector:pg18` | PostgreSQL major version upgrades require dump/restore. pg18 is the current stable (since Sept 2025). Upgrade path: `pg_dumpall` → stop services → change image → delete volume → restore dump. |
| `firecrawl-postgres` | `postgres:18-alpine` | Firecrawl-internal queue DB (NUQ schema). Pinned to match main postgres major. Data is transient (queue state), so cross-major migration is just a volume delete. |
| `listmonk-db` | `postgres:17-alpine` | Dedicated database for listmonk campaigns, subscribers, templates, and admin users. Kept on the upstream listmonk Docker Compose default; dump/restore before any major PostgreSQL bump. |
| `mongodb` | `mongo:8.2.7` | MongoDB 8 is the current stable major. LibreChat tenants depend on this. Major upgrades require replica-set-aware migration. |
| `redis` | `redis:8-alpine` | Redis 8 (GA Aug 2025) ships Vector Sets + hash-field-TTL. Previously on `redis:alpine` which silently rolled to 8 anyway — now explicit. |
| `vexa-redis` | `redis:8-alpine` | Aligned with main redis major. Isolated network; bot state + pub/sub + transcription streams. |
| `qdrant` | `qdrant/qdrant:v1.18.2` | Vector store for Klai Knowledge. Binary-incompatible on major bumps — pin explicitly. |
| `falkordb` | `falkordb/falkordb:v4.18.9` | Knowledge graph (Graphiti backend). v4.x has stable RediSearch + graph engine integration. |

### Auth + monitoring

| Service | Image | Rationale |
|---|---|---|
| `zitadel` | `ghcr.io/zitadel/zitadel:v4.15.0` | OIDC IdP. [HIGH] Minor upgrades sometimes invalidate portal-api PAT — see `.claude/rules/klai/platform/zitadel.md`. Rotate PAT after each bump. |
| `victoriametrics` | `victoriametrics/victoria-metrics:v1.140.0` | Metrics TSDB. |
| `victorialogs` | `victoriametrics/victoria-logs:v1.50.0` | Log aggregation (replaces Loki). LogsQL syntax differs from LogQL. |
| `cadvisor` | `ghcr.io/google/cadvisor:v0.57.0` | Container metrics. Registry moved from `gcr.io` to `ghcr.io`; verify dashboards that depend on container start/creation timestamps after this bump. |
| `alloy` | `grafana/alloy:v1.16.2` | Log and metric collection. Config format stable on minor bumps. |
| `grafana` | `grafana/grafana:13.0.2` | Dashboard UI. v12 → v13 had breaking dashboard JSON changes — verify dashboards after any major bump. |
| `glitchtip-web`, `glitchtip-worker`, `glitchtip-migrate` | `glitchtip/glitchtip:6.1.8` | Error tracking. All three share the same image (different commands). |

### Inference + AI

| Service | Image | Rationale |
|---|---|---|
| `litellm` | `ghcr.io/berriai/litellm:v1.87.1` | Pinned explicitly (moved from rolling `:main-stable` on 2026-04-19). Re-assess monthly; LiteLLM ships stable tags frequently. |
| `ollama` | `ollama/ollama:0.30.6` | CPU fallback for LLM inference. |
| `librechat-getklai` | `ghcr.io/danny-avila/librechat:v0.8.6` | GetKlai canary chat UI. Provisioning-managed tenant LibreChat containers remain on the `LIBRECHAT_IMAGE` default until the canary is proven. Mounted canary patches live under `deploy/librechat/getklai/patches/` and are checked against `deploy/librechat/getklai/patch-manifest.txt`; tenant patches remain under `deploy/librechat/patches/`. The getklai entrypoint also patches LibreChat Meili runtime paths to tenant-scoped indexes; verify this block against the image on every LibreChat upgrade. |

### Document + search

| Service | Image | Rationale |
|---|---|---|
| `meilisearch` | `getmeili/meilisearch:v1.45.2` | Search index for LibreChat conversations. **Data migration required on minor bumps** — v1.42.1 refused to boot directly on v1.45.2 and required dump/import. Pin explicitly and keep `MEILI_DB_PATH=/meili_data`; v1.45.2 otherwise starts on `./data.ms` and ignores the mounted volume. |
| `docling-serve` | `ghcr.io/docling-project/docling-serve:v1.21.0` | Document parsing (PDF, DOCX → structured). |
| `searxng` | `searxng/searxng:2026.6.5-37187dc2d` | Meta-search aggregator for LibreChat web mode. Date-based versioning. |
| `gitea` | `gitea/gitea:1.26.2` | Self-hosted git for klai-docs. |
| `crawl4ai` | `unclecode/crawl4ai:0.8.9` | Web crawler for klai-connector. Hooks are explicitly disabled in compose; Klai uses crawl request config, not Crawl4AI server hooks. |

### Ops

| Service | Image | Rationale |
|---|---|---|
| `docker-socket-proxy` | `tecnativa/docker-socket-proxy:v0.4.2` | Limits portal-api to specific Docker API verbs (CONTAINERS, NETWORKS, POST, DELETE). Stable; rare releases. |
| `garage` | `dxflrs/garage:v2.3.0` | S3-compatible object storage. Config field names change between minor releases — re-verify `garage.toml` after each bump. See `.claude/rules/klai/platform/garage.md`. |
| `listmonk` | `listmonk/listmonk:v6.1.0` | Self-hosted mailing platform at `mailing.getklai.com` for campaign templates, lists, and Twenty-selected audiences. Upgrade after checking listmonk release notes and database migrations. |

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
| `transcription-worker-1`, `transcription-worker-2` | `vexaai/transcription-service:0.10.6.2-local-260524-1610` (locally built on gpu-01 — NOT on Docker Hub) | Vexa transcription-service (SPEC-VEXA-003 §3.4). Replaces the legacy custom `whisper-server` 146-line Python script. `faster-whisper` + Silero VAD + hallucination detection + two-tier admission (realtime/deferred) behind Nginx LB. CUDA 12.3.2 + cuDNN 9. Host port `127.0.0.1:8000` retained so `gpu-tunnel.service` and all consumer URLs stay unchanged. Bump procedure: `docs/runbooks/transcription-service-bump.md`. |
| `transcription-api` | `nginx:1.30.1-alpine` | Least-connections load balancer in front of the two CUDA workers. Config at `deploy/vexa-transcription/nginx.conf`. Bumped from 1.27 to pick up the CVE-2026-42945 fix; nginx marks 1.30.1+ as not vulnerable. |
| `bge-m3-sparse` | built from `./bge-m3-sparse` | Local build. Sparse embeddings sidecar for hybrid retrieval. |

---

## Dev stack — `docker-compose.dev.yml`

Uses the same versions as production core-01 to catch version-related issues locally.

| Service | Image | Notes |
|---|---|---|
| `postgres` | `pgvector/pgvector:pg18` | Same as prod. |
| `redis` | `redis:8-alpine` | Aligned with prod (was `redis:alpine`). |
| `mongodb` | `mongo:8.2.7` | Same as prod. |
| `meilisearch` | `getmeili/meilisearch:v1.45.2` | Aligned with prod; keep `MEILI_DB_PATH=/meili_data` in dev as well. |
| `litellm` | `ghcr.io/berriai/litellm:v1.87.1` | Same as prod. |

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

*Last repo pin sync: 2026-06-06 — Meilisearch and LibreChat intentionally left on their existing repo pins for the separate migration sessions.*
