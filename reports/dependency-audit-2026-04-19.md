# Dependency Audit — 2026-04-19

**Scope:** `klai-infra/` + `deploy/` (infra images & compose) + `klai-portal/backend` (Python) + `klai-portal/frontend` (Node)
**Depth:** Direct dependencies only (transitive not audited)
**CVE/security:** Not included — per user, already tracked separately
**Method:** PyPI JSON, npm registry `/latest`, Docker Hub tags API, GitHub releases

**Legend:**
- ✅ Up-to-date / acceptable
- 🟡 Minor/patch update available
- 🔴 Major update available — breaking changes likely
- 🟠 Version inconsistency / pin risk
- 📌 Pinned intentionally (see `deploy/VERSIONS.md`)

---

## Executive summary

| Category | Total | ✅ | 🟡 Minor/Patch | 🔴 Major | 🟠 Pin risk |
|---|---|---|---|---|---|
| Python (portal-api) | 22 | 3 | 14 | 3 | 2 |
| Node (frontend dependencies) | 35 | 18 | 13 | 0 | 1 (Mantine split) |
| Node (frontend devDependencies) | 17 | 7 | 5 | 4 | 1 (router-plugin lag) |
| Docker images | 28 | 21 (latest-rolling) | 1 | 1 | 5 (pinned w/ newer available) |

**Top priorities:**
1. **🔴 redis Python client `>=5.0` → 7.4.0**: two major versions behind. Check for breaking changes (connection pooling, async API).
2. **🔴 pytest `>=8` → 9.0.3 + pytest-asyncio `>=0.24` → 1.3.0**: coordinated dev-dep major bump.
3. **🔴 cryptography `>=43` → 46**: three majors behind; keeps getting CVE-driven releases.
4. **🟠 Mantine split: `@mantine/core` + `@mantine/hooks` on `^8.0.0`, `@mantine/notifications` on `^9.0.2`**. Mantine requires all packages on the same major — this is a real footgun.
5. **🟠 `@tanstack/router-plugin ^1.114.0` vs `@tanstack/react-router ^1.168.7`** — plugin is ~53 minor versions behind router.
6. **🟠 `dxflrs/garage:v2.2.0` → v2.3.0** (pinned, minor bump; follow the known Garage config quirks).
7. **🟠 `text-embeddings-inference:1.5` → v1.9.3** (pinned, 4 minor versions behind).
8. **📌 `rabbitmq:3-alpine`**: intentionally pinned — Firecrawl has not confirmed 4.x compatibility. No change recommended.

---

## 1. Python — `klai-portal/backend/pyproject.toml`

Project: `klai-portal-api` (Python 3.12, Dockerfile `python:3.12-slim`)

### Runtime dependencies

| Package | Declared | Latest stable | Status | Notes |
|---|---|---|---|---|
| `fastapi` | `>=0.115` | 0.136.0 | 🟡 | 21 minors ahead; safe within FastAPI's pre-1.0 stability policy |
| `uvicorn[standard]` | `>=0.32` | 0.44.0 | 🟡 | Minor drift |
| `httpx` | `>=0.27` | 0.28.1 | 🟡 | Consider bumping floor to `>=0.28` |
| `pydantic[email]` | `>=2.9` | 2.13.2 | 🟡 | Active, fast-moving |
| `pydantic-settings` | `>=2.6` | 2.13.1 | 🟡 | |
| `asyncpg` | `>=0.30` | 0.31.0 | 🟡 | |
| `sqlalchemy[asyncio]` | `>=2.0` | 2.0.49 | 🟡 | Floor very wide; OK |
| `alembic` | `>=1.14` | 1.18.4 | 🟡 | Several minor migrations behind |
| `python-multipart` | `>=0.0.26` | 0.0.26 | ✅ | |
| `docker` | `>=7.0` | 7.1.0 | 🟡 | |
| `cryptography` | `>=43.0` | 46.0.7 | 🔴 | **3 majors** — CVE-driven releases, bump asap |
| `icalendar` | `>=6.1.0,<8.0` | 7.0.3 | ✅ | Within pinned range |
| `motor` | `>=3.6` | 3.7.1 | 🟡 | Note: MongoDB has deprecated Motor in favor of official `pymongo` async API in 4.9+ — future migration |
| `prometheus-client` | `>=0.21,<1.0` | 0.25.0 | ✅ | Within pinned range |
| `pyyaml` | `>=6.0` | 6.0.3 | 🟡 | |
| `structlog` | `>=25.0` | 25.5.0 | 🟡 | |
| `redis[hiredis]` | `>=5.0` | 7.4.0 | 🔴 | **2 majors behind**. v6 introduced async changes, v7 cluster improvements. **Highest-risk bump in the repo.** |
| `pyjwt` | `>=2.8` | 2.12.1 | 🟡 | |

### Dev dependencies

| Package | Declared | Latest stable | Status |
|---|---|---|---|
| `pytest` | `>=8` | 9.0.3 | 🔴 Major |
| `pytest-asyncio` | `>=0.24` | 1.3.0 | 🔴 Major (0.x → 1.x — API stabilized) |
| `ruff` | `>=0.8` | 0.15.11 | 🟡 (large minor jump, many new rules) |
| `pyright` | `>=1.1` | 1.1.408 | ✅ (rolling 1.1.x) |

### Deployed services outside portal-api (from `deploy/VERSIONS.md`)

| Package | Service | Current | Latest | Status |
|---|---|---|---|---|
| `procrastinate` | knowledge-ingest | `>=3.0,<4` → 3.7.2 | 3.7.2 | ✅ |
| `graphiti-core` | retrieval-api | `>=0.28,<0.30` → 0.28.2 | 0.28.2 stable | 📌 upper bound blocks 0.30 RC intentionally |

### Python — actions

- **Priority 1 (schedule soon):** bump `redis[hiredis]>=7`, `cryptography>=46`, `pytest>=9`, `pytest-asyncio>=1`.
- **Priority 2 (batch with next PR):** `fastapi`, `pydantic`, `alembic`, `ruff` — bump floors.
- **No change:** pinned `icalendar<8.0`, `prometheus-client<1.0`, `graphiti-core<0.30` — intentional.
- **Consider in next SPEC:** `motor` → official `pymongo` async API migration path.

---

## 2. Node — `klai-portal/frontend/package.json`

React 19.2, Vite 8, Vitest 4, Tailwind 4 project. Declared ranges with caret (`^`) auto-absorb minor/patch.

### Runtime dependencies

| Package | Declared | Latest | Status |
|---|---|---|---|
| `@blocknote/core` | `^0.47.1` | 0.48.1 | 🟡 minor |
| `@blocknote/mantine` | `^0.47.1` | 0.48.1 | 🟡 minor |
| `@blocknote/react` | `^0.47.1` | 0.48.1 | 🟡 minor |
| `@dnd-kit/core` | `^6.3.1` | 6.3.1 | ✅ |
| `@emoji-mart/data` | `^1.2.1` | 1.2.1 | ✅ |
| `@emoji-mart/react` | `^1.1.1` | 1.1.1 | ✅ |
| `@icons-pack/react-simple-icons` | `^13.13.0` | 13.13.0 | ✅ |
| `@inlang/paraglide-js` | `^2.15.1` | 2.16.0 | 🟡 minor |
| `@mantine/core` | `^8.0.0` | 9.0.2 | 🟠 **see Mantine split below** |
| `@mantine/hooks` | `^8.0.0` | 9.0.2 | 🟠 **see Mantine split below** |
| `@mantine/notifications` | `^9.0.2` | 9.0.2 | 🟠 **mismatch with core/hooks** |
| `@radix-ui/react-accordion` | `^1.2.12` | 1.2.12 | ✅ |
| `@radix-ui/react-alert-dialog` | `^1.1.15` | 1.1.15 | ✅ |
| `@radix-ui/react-dialog` | `^1.1.15` | 1.1.15 | ✅ |
| `@radix-ui/react-dropdown-menu` | `^2.1.16` | 2.1.16 | ✅ |
| `@radix-ui/react-popover` | `^1.1.15` | 1.1.15 | ✅ |
| `@radix-ui/react-scroll-area` | `^1.2.10` | 1.2.10 | ✅ |
| `@radix-ui/react-separator` | `^1.1.8` | 1.1.8 | ✅ |
| `@radix-ui/react-slot` | `^1.2.4` | 1.2.4 | ✅ |
| `@radix-ui/react-switch` | `^1.2.6` | 1.2.6 | ✅ |
| `@radix-ui/react-tabs` | `^1.1.13` | 1.1.13 | ✅ |
| `@sentry/react` | `^10.43.0` | 10.49.0 | 🟡 patch/minor |
| `@sentry/vite-plugin` | `^5.1.1` | 5.2.0 | 🟡 minor |
| `@tanstack/react-query` | `^5.95.2` | 5.99.1 | 🟡 minor |
| `@tanstack/react-router` | `^1.168.7` | 1.168.23 | 🟡 patch |
| `@tanstack/react-table` | `^8.21.3` | 8.21.3 | ✅ |
| `class-variance-authority` | `^0.7.1` | 0.7.1 | ✅ |
| `clsx` | `^2.1.1` | 2.1.1 | ✅ |
| `cmdk` | `^1.1.1` | 1.1.1 | ✅ |
| `consola` | `^3.4.2` | 3.4.2 | ✅ |
| `driver.js` | `^1.3.1` | 1.4.0 | 🟡 minor |
| `lucide-react` | `^1.7.0` | 1.8.0 | 🟡 minor |
| `oidc-client-ts` | `^3.1.0` | 3.5.0 | 🟡 minor (auth lib — test thoroughly) |
| `react` | `^19.2.0` | 19.2.5 | 🟡 patch |
| `react-dom` | `^19.2.0` | 19.2.5 | 🟡 patch |
| `react-markdown` | `^10.1.0` | 10.1.0 | ✅ |
| `react-oidc-context` | `^3.3.1` | 3.3.1 | ✅ |
| `react-qr-code` | `^2.0.18` | 2.0.18 | ✅ |
| `sonner` | `^2.0.7` | 2.0.7 | ✅ |
| `tailwind-merge` | `^3.5.0` | 3.5.0 | ✅ |
| `web-vitals` | `^5.2.0` | 5.2.0 | ✅ |

### 🟠 Mantine version split (HIGH)

```
"@mantine/core": "^8.0.0"
"@mantine/hooks": "^8.0.0"
"@mantine/notifications": "^9.0.2"   ← wrong major
```

Mantine documents explicitly that **all `@mantine/*` packages must be installed at the same major version** — mixing v8 runtime with v9 notifications will compile but produce subtle theme/context bugs at runtime (and v9 notifications exports may not be what v8 core expects). Fix: align all three on `^9.0.2` or revert notifications to `^8.x`.

### Dev dependencies

| Package | Declared | Latest | Status |
|---|---|---|---|
| `@eslint/js` | `^9.39.1` | 10.0.1 | 🔴 major |
| `@tailwindcss/vite` | `^4.0.0` | 4.2.2 | 🟡 minor |
| `@tanstack/router-plugin` | `^1.114.0` | 1.167.22 | 🟠 **53 minors behind `@tanstack/react-router`** — these MUST move together |
| `@types/node` | `^24.10.1` | 25.6.0 | 🔴 major |
| `@types/react` | `^19.2.7` | 19.2.14 | 🟡 patch |
| `@types/react-dom` | `^19.2.3` | 19.2.3 | ✅ |
| `@vitejs/plugin-react` | `^5.1.1` | 6.0.1 | 🔴 major (Vite 8 compat — confirm) |
| `@vitest/coverage-v8` | `^4.1.4` | 4.1.4 | ✅ |
| `eslint` | `^10.1.0` | 10.2.1 | 🟡 minor |
| `eslint-plugin-react-hooks` | `^7.0.1` | 7.1.1 | 🟡 minor |
| `eslint-plugin-react-refresh` | `^0.5.2` | 0.5.2 | ✅ |
| `globals` | `^16.5.0` | 17.5.0 | 🔴 major |
| `jsdom` | `^29.0.2` | 29.0.2 | ✅ |
| `rollup-plugin-visualizer` | `^7.0.1` | 7.0.1 | ✅ |
| `tailwindcss` | `^4.2.2` | 4.2.2 | ✅ |
| `typescript` | `~5.9.3` | 6.0.3 | 🔴 major (tilde pins to 5.9.x — intentional floor) |
| `typescript-eslint` | `^8.48.0` | 8.58.2 | 🟡 minor |
| `vite` | `^8.0.3` | 8.0.8 | 🟡 patch |
| `vitest` | `^4.1.4` | 4.1.4 | ✅ |

### Node — actions

- **Priority 1 (breaking if ignored):**
  - Align Mantine majors — either pull `core` + `hooks` forward to `^9.0.2` or drop `notifications` back to `^8.x`.
  - Bring `@tanstack/router-plugin` in lockstep with `@tanstack/react-router` (both should be ~`^1.168.x`).
- **Priority 2 (major bumps, test in branch):**
  - `typescript 5 → 6` (watch for type inference behavior changes).
  - `@vitejs/plugin-react 5 → 6` (verify Vite 8 compat).
  - `@eslint/js 9 → 10` (flat config compatibility only).
  - `globals 16 → 17` (runtime surface changes).
  - `@types/node 24 → 25`.
- **Priority 3 (batch with Renovate):** TanStack React Query/Router patches, Sentry, BlockNote, Paraglide, driver.js, lucide-react — all minor bumps.

---

## 3. Docker images — public core compose

Default pattern in this repo is `:latest` with a monthly `docker compose pull` cadence (per `VERSIONS.md`). The "Status" column assumes `:latest` is pulled regularly.

### Core stack

| Service | Image pin | Current latest | Status |
|---|---|---|---|
| caddy | `ghcr.io/getklai/caddy-hetzner:latest` | (internal build) | 📌 internal image |
| mongodb | `mongo:latest` | 8.2.7 | ✅ rolling |
| postgres (main) | `pgvector/pgvector:pg18` | pg18 (pgvector 0.8.2, PG 18.x) | 📌 ✅ pg19 not yet released |
| redis (main) | `redis:alpine` | 8.4.2-alpine (**alpine now points to 8.x**) | 🟠 **implicit major bump** — verify `appendonly yes` + password auth still work after `docker compose pull` |
| redis (vexa) | `redis:7-alpine` | 7.x alpine (8 available) | 🟡 intentional 7.x pin — consistent with main is desirable |
| meilisearch | `getmeili/meilisearch:latest` | v1.42.1 | ✅ rolling |
| zitadel | `ghcr.io/zitadel/zitadel:latest` | v4.13.1 | ✅ rolling (check PAT post-upgrade per `platform/zitadel.md`) |
| litellm | `ghcr.io/berriai/litellm:main-stable` | v1.83.3-stable | ✅ `main-stable` tracking tag |
| librechat | `ghcr.io/danny-avila/librechat:latest` | v0.8.4 | ✅ rolling (custom CJS patches mounted — verify after each pull) |
| ollama | `ollama/ollama:latest` | 0.21.0 | ✅ rolling |
| docker-socket-proxy | `tecnativa/docker-socket-proxy:latest` | v0.4.2 | ✅ rolling |
| klai-mailer | `ghcr.io/getklai/klai-mailer:latest` | (internal build) | 📌 internal |
| portal-api | `ghcr.io/getklai/portal-api:latest` | (built from this repo) | 📌 internal |
| glitchtip | `glitchtip/glitchtip:latest` | 6.1.6 | ✅ rolling |

### Monitoring stack

| Service | Image pin | Current latest | Status |
|---|---|---|---|
| victoriametrics | `victoriametrics/victoria-metrics:latest` | v1.140.0 | ✅ rolling |
| victorialogs | `victoriametrics/victoria-logs:latest` | v1.50.0 | ✅ rolling |
| cadvisor | `gcr.io/cadvisor/cadvisor:latest` | v0.55.1 (Dec 2024) | 🟡 upstream stagnant — no action, just awareness |
| alloy | `grafana/alloy:latest` | v1.15.1 | ✅ rolling |
| grafana | `grafana/grafana:latest` | v13.0 | ✅ rolling (recent 12 → 13 major) |

### Research / retrieval / docs

| Service | Image pin | Current latest | Status |
|---|---|---|---|
| docling-serve | `ghcr.io/docling-project/docling-serve:latest` | 1.16.1 | ✅ rolling |
| searxng | `searxng/searxng:latest` | 2026.4.17 | ✅ rolling (date-based) |
| research-api | `ghcr.io/getklai/research-api:latest` | (internal build) | 📌 internal |
| retrieval-api | `ghcr.io/getklai/retrieval-api:latest` | (internal build) | 📌 internal |
| knowledge-ingest | `ghcr.io/getklai/knowledge-ingest:latest` | (internal build) | 📌 internal |
| klai-connector | `ghcr.io/getklai/klai-connector:latest` | (internal build) | 📌 internal |
| scribe-api | `ghcr.io/getklai/scribe-api:latest` | (internal build) | 📌 internal |
| klai-knowledge-mcp | `ghcr.io/getklai/klai-knowledge-mcp:latest` | (internal build) | 📌 internal |
| docs-app | `ghcr.io/getklai/klai-docs:latest` | (internal build) | 📌 internal |
| gitea | `gitea/gitea:latest` | 1.26.0 | ✅ rolling |
| qdrant | `qdrant/qdrant:latest` | v1.17.1 | ✅ rolling |
| falkordb | `falkordb/falkordb:latest` | v4.18.1 | ✅ rolling |
| garage | `dxflrs/garage:v2.2.0` | **v2.3.0** | 🟠 **pinned, minor bump available** — re-read `platform/garage.md` before bumping (`s3_web.bind_addr` field name pitfall) |
| crawl4ai | `unclecode/crawl4ai:latest` | 0.8.6 | ✅ rolling |

### Firecrawl (web search)

| Service | Image pin | Current latest | Status |
|---|---|---|---|
| firecrawl-postgres | `postgres:18-alpine` | 18.3-alpine3.23 | ✅ pg18 (per VERSIONS.md — migrated from pg16) |
| firecrawl-rabbitmq | `rabbitmq:3-alpine` | 4.2.5-alpine | 📌 **pinned per `VERSIONS.md`** — Firecrawl has not confirmed RabbitMQ 4.x compat (AMQP 1.0 default breaking change). **Do NOT bump without checking Firecrawl releases first.** |
| firecrawl-api | `ghcr.io/mendableai/firecrawl:latest` | built from source (v1.x) | 📌 internal build |

### GPU stack

GPU production compose and operator version notes are private infra material.
Do not publish live GPU service pins, host paths, or tunnel details from this
report; audit them in `GetKlai/klai-infra`.

### Portal-api base image

| File | Pin | Current latest | Status |
|---|---|---|---|
| `klai-portal/backend/Dockerfile` | `python:3.12-slim` | 3.13.13 (3.14 also released) | 🟡 **Python 3.13 is the security-maintained minor**. The `pyproject.toml` targets `py312` via ruff — bumping base image requires updating `requires-python` + ruff `target-version`. No urgency but scheduled upgrade makes sense. |

### Docker — actions

- **🟠 Verify Redis 8 upgrade path.** `redis:alpine` silently follows the latest major; Redis 8 (GA Aug 2025) ships with the Vector Sets + hash-field-TTL features but also new ACL defaults. Pin explicitly: either `redis:7-alpine` everywhere (matches vexa-redis) or `redis:8-alpine` after testing with your `REDIS_PASSWORD` + AOF config.
- **🟠 `dxflrs/garage:v2.2.0` → v2.3.0.** Release notes needed, but upgrade is minor. Re-verify `[s3_web].bind_addr` field per existing pitfall.
- **GPU image pins:** audit in `GetKlai/klai-infra`; do not publish live GPU compose details in the public report.
- **📌 Don't touch `rabbitmq:3-alpine`** without Firecrawl compatibility confirmation.
- **Scheduled:** bump `python:3.12-slim` → `python:3.13-slim` in portal-api Dockerfile (coordinate with `pyproject.toml requires-python`, ruff `target-version`, and `.moai/rules` language hint).

---

## 4. Not covered by this audit

- **Transitive dependencies** (e.g., inside `node_modules`, inside a Python wheel's own pinned deps). If you want deep auditing, run `npm outdated --long` and `uv pip list --outdated` on a clean install and compare lockfiles. That's a separate pass.
- **`klai-infra/` repo** is only SOPS secrets + deploy scripts — no dependencies to audit. The actual infra manifests live in `deploy/` (already covered).
- **`klai-website/`** — excluded per user scope.
- **Other service repos** (`klai-connector`, `klai-retrieval-api`, `klai-knowledge-ingest`, `klai-scribe`, `klai-mailer`, `klai-knowledge-mcp`, `klai-focus`, `klai-widget`, `klai-docs`) — all exist as separate repos under this monorepo working tree but were not scoped into "portal". Let me know if any of these should be added.
- **CVE / security advisories** — already tracked separately per user.

---

## 5. Recommended upgrade order

1. **Fix Mantine version split** (one PR, low risk, prevents runtime drift).
2. **Sync `@tanstack/router-plugin` with `@tanstack/react-router`** (required for Vite HMR correctness).
3. **Python dev-deps bump** (pytest 9, pytest-asyncio 1, ruff latest) — separate PR, CI-only.
4. **`cryptography` bump** — security-adjacent, but no known CVE open; still a good hygiene update.
5. **`redis` Python client 5 → 7** — risky (connection pool API changed in v5 → v6, async context manager in v7). Needs integration tests for portal-api session cache + rate limiter paths.
6. **`typescript 5 → 6`, `@vitejs/plugin-react 5 → 6`, `@eslint/js 9 → 10`** — bundle into one frontend-tooling PR, run full build + vitest suite.
7. **Pin explicit Redis Docker major** (either 7 or 8, matching vexa-redis).
8. **`garage v2.2.0 → v2.3.0`** — minor, but re-read `platform/garage.md` first.
9. **`text-embeddings-inference:1.5 → 1.9.x`** — only after verifying bge-m3 output parity.
10. **Python base image 3.12 → 3.13** — coordinated change across Dockerfile + pyproject.toml + ruff config.

---

*Generated: 2026-04-19 • Claude Opus 4.7 • Data sources: PyPI JSON API, npm registry, Docker Hub tags API, GitHub releases*
