# Research — SPEC-SEC-ENVFILE-SCOPE-001

## Finding context: internal-wave scribe audit

From `.moai/audit/` internal-wave (2026-04-24) via the
`klai-security-audit` agent:

The scribe-api service (`deploy/docker-compose.yml:758`) declares
`env_file: .env`. `.env` on core-01 is the merged output of
`klai-infra/core-01/.env.sops` decrypted to `/opt/klai/.env` by the
SOPS pipeline. It contains every Klai secret — ~120 env vars across
portal-api, LibreChat, Vexa, monitoring, mailer, knowledge-ingest,
retrieval, connector, docs, and more.

When scribe-api boots, Docker Compose injects the **entire** file into
the container's process env. `docker exec klai-core-scribe-api-1
printenv | wc -l` on a pre-SPEC core-01 returns >130 lines; scribe's
`pydantic-settings` class reads only 12. The other ~120 vars are
inheritance-by-accident.

Severity: MEDIUM in isolation. CRITICAL chained with any RCE in
scribe — the adjacent SPEC `SPEC-SEC-MAILER-INJECTION-001` already
identified `str.format(**variables)` RCE in klai-mailer, which shares
the same blast-radius shape. Scribe does media parsing (audio upload
pipeline), which has its own RCE surface.

## Full audit of `deploy/docker-compose.yml`

The shared `env_file: .env` anti-pattern is present on more services
than the stub listed. Full audit (2026-04-24, line numbers against
working-copy `deploy/docker-compose.yml` @ 1252 lines):

| Service | Line | Uses `env_file: .env` (shared global) | Uses per-service `env_file: ./<svc>/.env` | Has explicit `environment:` block today |
|---|---|---|---|---|
| caddy | 69 | — | — | YES (lines 83–93) |
| mongodb | 99 | — | — | YES (:104–106) |
| postgres | 116 | — | — | YES (:122–125) |
| redis | 135 | — | — | YES (:140–141) |
| meilisearch | 152 | — | — | YES (:157–160) |
| zitadel | 170 | — | — | YES (:179–200) |
| litellm | 206 | — | — | YES (:214–227) |
| librechat-getklai | 240 | — | `./librechat/getklai/.env` (:244–245) | YES (:246–248, supplemental) |
| ollama | 268 | — | — | — (no secrets) |
| docker-socket-proxy | 295 | — | — | YES (inline flags) |
| klai-mailer | 309 | — | `./klai-mailer/.env` (:314) | — (relies on per-service file) |
| **portal-api** | **319** | **YES (:322)** | — | YES (:326–362) — the largest block |
| glitchtip-web | 371 | — | — | YES (:379–401) |
| glitchtip-worker | 404 | — | — | YES (inline) |
| glitchtip-migrate | 428 | — | — | YES (inline) |
| victoriametrics | 445 | — | — | YES (flags only, no env vars beyond auth) |
| **victorialogs** | **468** | **YES (:479)** | — | YES (flags reference `${VICTORIALOGS_AUTH_*}`) |
| cadvisor | 501 | — | — | — (no secrets) |
| alloy | 523 | — | — | YES (:537–539) |
| grafana | 549 | — | — | YES (inline) |
| docling-serve | 614 | — | — | — (no secrets) |
| searxng | 621 | — | — | YES (:624–625) |
| **retrieval-api** | **632** | **YES (:635)** | — | YES (:636–658) |
| gitea | 670 | — | — | YES (inline) |
| docs-app | 707 | — | — | YES (:710–726) |
| klai-knowledge-mcp | 737 | — | — | YES (:740–747) |
| **scribe-api** | **755** | **YES (:758)** | — | YES (:759–762) |
| admin-api (Vexa) | 780 | — | — | YES (:783–792) |
| api-gateway (Vexa) | 812 | — | — | YES (:815–826) |
| meeting-api (Vexa) | 850 | — | — | YES (:853–887) |
| runtime-api-socket-proxy | 919 | — | — | — (command-only sidecar) |
| runtime-api (Vexa) | 940 | — | — | YES (:943–966) |
| vexa-redis | 998 | — | — | — (command-line only) |
| qdrant | 1018 | — | — | YES (:1023–1024) |
| falkordb | 1039 | — | — | — (no secrets) |
| knowledge-ingest | 1058 | — | — | YES (:1061–1091) |
| garage | 1105 | — | — | YES (env vars for S3 secret) |
| klai-connector | 1130 | — | `./klai-connector/.env` (:1133) | YES (:1134–1155, supplemental) |
| crawl4ai | 1168 | — | — | YES (:1171–1173) |
| firecrawl-postgres | 1190 | — | — | YES (:1193–1196) |
| firecrawl-rabbitmq | 1209 | — | — | — |
| firecrawl-api | 1221 | — | — | YES (:1224–1236) |

**Summary of current state:**

- **4 services use the forbidden bare `env_file: .env`**: portal-api,
  victorialogs, retrieval-api, scribe-api. Only these four need the
  REQ-1 treatment.
- **3 services use an acceptable per-service `env_file:` form**:
  librechat-getklai, klai-mailer, klai-connector. These are compliant
  with REQ-6 as-is, BUT their per-repo `.env` file content must be
  audited against REQ-6.3 (per-service .env should not mirror the
  global).
- **Everyone else is already compliant** with an explicit
  `environment:` block.

## Per-service required-secret inventory (for the four migrations)

Derived from pydantic-settings definitions + `os.getenv` grep across
the relevant service sources.

### portal-api (largest consumer, highest blast radius if misconfigured)

Source: `klai-portal/backend/app/core/config.py` (115+ fields).

The service ALREADY declares 36 env vars explicitly in its
`environment:` block (lines 326–362). The `env_file: .env` line
(:322) leaks an additional ~90 env vars that the code never reads.

**Keep in `environment:` (today, verified):** `DOMAIN`, `ZITADEL_PAT`,
`DATABASE_URL`, `DOCKER_HOST`, `INTERNAL_SECRET`, `SSO_COOKIE_KEY`,
`BFF_SESSION_KEY`, `ZITADEL_PORTAL_CLIENT_ID`,
`ZITADEL_PORTAL_CLIENT_SECRET`, `VEXA_API_KEY`, `VEXA_WEBHOOK_SECRET`,
`PORTAL_SECRETS_KEY`, `ENCRYPTION_KEY`, `IMAP_HOST`, `IMAP_PORT`,
`IMAP_USERNAME`, `IMAP_POLL_INTERVAL_SECONDS`, `IMAP_PASSWORD`,
`LIBRECHAT_MONGO_ROOT_URI`, `KLAI_CONNECTOR_SECRET`,
`GITHUB_ADMIN_PAT`, `GITHUB_ORG`, `REDIS_URL`, `QDRANT_URL`,
`QDRANT_API_KEY`, `ZITADEL_IDP_GOOGLE_ID`,
`ZITADEL_IDP_MICROSOFT_ID`, `FRONTEND_URL`, `GOOGLE_DRIVE_CLIENT_ID`,
`GOOGLE_DRIVE_CLIENT_SECRET`, `MS_DOCS_CLIENT_ID`,
`MS_DOCS_CLIENT_SECRET`, `MS_DOCS_TENANT_ID`, `MONGODB_CONTAINER_NAME`,
`MONGO_ROOT_USERNAME`.

**Additionally required** (reads in code but not yet in compose block —
must be added as part of this migration to keep behaviour identical
after removing `env_file: .env`):
- `MONGO_ROOT_PASSWORD` (used by portal infrastructure endpoints)
- `MEILI_MASTER_KEY` (reserved for future search-sync endpoint — optional empty default, keep declared)
- `LITELLM_MASTER_KEY` (portal emits via hook)
- `FIRECRAWL_INTERNAL_KEY` (web search API key)
- `REDIS_PASSWORD`, `REDIS_HOST`, `REDIS_PORT` (alt redis access path)
- `MONEYBIRD_API_TOKEN`, `MONEYBIRD_ADMIN_ID`,
  `MONEYBIRD_WEBHOOK_TOKEN`, plus the six `MONEYBIRD_PRODUCT_*` IDs
- `DOCS_INTERNAL_SECRET`
- `MAILER_URL` (internal service URL)
- `GARAGE_ACCESS_KEY`, `GARAGE_SECRET_KEY`, `GARAGE_BUCKET`, `GARAGE_REGION` (if portal uses Garage — confirm during migration; may be optional)
- `ZITADEL_ADMIN_PAT` (if used by portal runbook automations — otherwise skip)

**Surplus today (leaked in, NOT read):** Everything in `/opt/klai/.env`
that is Vexa-specific (`VEXA_DB_PASSWORD`, `VEXA_ADMIN_TOKEN`,
`VEXA_REDIS_PASSWORD`, `BOT_API_TOKEN`, `INTERNAL_API_SECRET`,
`WHISPER_SERVICE_TOKEN`); Monitoring-specific (`VICTORIALOGS_AUTH_*`,
`VICTORIALOGS_INGEST_TOKEN`, `VICTORIALOGS_BASIC_AUTH_B64`,
`HETZNER_AUTH_API_TOKEN`, `GRAFANA_CADDY_USER`, `GRAFANA_CADDY_HASH`);
GlitchTip (`GLITCHTIP_DB_PASSWORD`, `GLITCHTIP_EMAIL_URL`,
`GLITCHTIP_SECRET_KEY`, `GLITCHTIP_REDIS_URL`); Gitea
(`GITEA_ADMIN_TOKEN`, `GITEA_WEBHOOK_SECRET`); Vexa Meeting
(`SEARXNG_SECRET`, Zitadel DB secrets, Docs-specific secrets); and
the 29 `KUMA_TOKEN_*` tokens.

### retrieval-api

Source: `klai-retrieval-api/retrieval_api/config.py`.

**Required (from pydantic-settings fields, explicit in config.py):**
`QDRANT_URL`, `QDRANT_API_KEY`, `QDRANT_COLLECTION`,
`QDRANT_FOCUS_COLLECTION`, `TEI_URL`, `INFINITY_RERANKER_URL`,
`LITELLM_URL`, `LITELLM_API_KEY`, `SPARSE_SIDECAR_URL`,
`SPARSE_SIDECAR_TIMEOUT`, `FALKORDB_HOST`, `FALKORDB_PORT`,
`GRAPHITI_ENABLED`, `GRAPH_SEARCH_TIMEOUT`,
`GRAPHITI_LLM_MODEL`, `SYNTHESIS_MODEL`, `RETRIEVAL_GATE_ENABLED`,
`RETRIEVAL_GATE_THRESHOLD`, `RETRIEVAL_CANDIDATES`,
`RERANKER_CANDIDATES`, `RERANKER_ENABLED`, `RERANKER_TIMEOUT`,
`COREFERENCE_MODEL`, `COREFERENCE_TIMEOUT`, `LINK_EXPAND_*` (5 keys),
`LINK_AUTHORITY_BOOST`, `SOURCE_QUOTA_ENABLED`,
`SOURCE_QUOTA_MAX_PER_SOURCE`, `ROUTER_*` (7 keys),
`PORTAL_EVENTS_HOST`, `PORTAL_EVENTS_PORT`, `PORTAL_EVENTS_USER`,
`PORTAL_EVENTS_PASSWORD`, `PORTAL_EVENTS_DB`, `INTERNAL_SECRET`,
`ZITADEL_ISSUER`, `ZITADEL_API_AUDIENCE`, `RATE_LIMIT_RPM`,
`REDIS_URL`.

Plus `OPENAI_API_KEY` which is currently set to a dummy value in
compose (:649) — retained for SDK compatibility.

Compose already declares: QDRANT_*, TEI_URL, TEI_RERANKER_URL,
LITELLM_URL, LITELLM_API_KEY, FALKORDB_HOST, FALKORDB_PORT,
GRAPHITI_ENABLED, GRAPH_SEARCH_TIMEOUT, RERANKER_ENABLED,
RERANKER_TIMEOUT, OPENAI_API_KEY, SPARSE_SIDECAR_URL,
PORTAL_EVENTS_HOST, PORTAL_EVENTS_PASSWORD, INTERNAL_SECRET (as
`RETRIEVAL_API_INTERNAL_SECRET`), ZITADEL_ISSUER,
ZITADEL_API_AUDIENCE (as `RETRIEVAL_API_ZITADEL_AUDIENCE`),
REDIS_URL, RATE_LIMIT_RPM (as `RETRIEVAL_API_RATE_LIMIT_RPM`).

**Additionally required** (read by code, inherit from `.env` today,
must move to explicit block): nothing strictly required — retrieval-api
already declares everything the code reads. The `env_file: .env` line
exists as a defensive relic and can simply be **removed** with no
additional `environment:` lines needed.

**Surplus today (leaked):** Everything else in `/opt/klai/.env` —
portal-specific secrets (`PORTAL_API_INTERNAL_SECRET`,
`MONEYBIRD_*`, `ENCRYPTION_KEY`, `ZITADEL_PAT`), mailer, Vexa,
monitoring, etc.

### scribe-api

Source: `klai-scribe/scribe-api/app/core/config.py`.

**Required (from pydantic-settings):** `POSTGRES_DSN`,
`ZITADEL_ISSUER`, `WHISPER_SERVER_URL`, `STT_PROVIDER`,
`WHISPER_PROVIDER_NAME`, `MAX_UPLOAD_MB`, `AUDIO_STORAGE_DIR`,
`LITELLM_BASE_URL`, `LITELLM_MASTER_KEY`, `EXTRACTION_MODEL`,
`SYNTHESIS_MODEL`, `KNOWLEDGE_INGEST_URL`, `KNOWLEDGE_INGEST_SECRET`,
`LOG_LEVEL`.

Compose already declares: `POSTGRES_DSN`, `WHISPER_SERVER_URL`,
`ZITADEL_ISSUER`.

**Additionally required** (read by code, must be added when removing
`env_file: .env`): `LITELLM_BASE_URL` (=`http://litellm:4000`),
`LITELLM_MASTER_KEY`, `KNOWLEDGE_INGEST_URL`,
`KNOWLEDGE_INGEST_SECRET`. `LOG_LEVEL` has a safe default; adding it
explicitly is optional. `MAX_UPLOAD_MB`, `AUDIO_STORAGE_DIR`,
`STT_PROVIDER`, `EXTRACTION_MODEL`, `SYNTHESIS_MODEL`,
`WHISPER_PROVIDER_NAME` all have code defaults that match production
— add explicitly only where production overrides the default.

**Surplus today (leaked — the headline smoking gun):**
`PORTAL_API_INTERNAL_SECRET`, `ENCRYPTION_KEY` (portal's KEK),
`ZITADEL_PAT`, `PORTAL_API_PORTAL_SECRETS_KEY`,
`VEXA_WEBHOOK_SECRET`, `MONEYBIRD_WEBHOOK_TOKEN`, every Vexa secret,
every monitoring secret, GlitchTip SDK key, Garage S3 credentials,
Firecrawl internal key, Zitadel admin PAT — all values scribe does
not touch.

### victorialogs

Source: upstream image — config entirely via command-line flags
(`-httpAuth.username`, `-httpAuth.password` referenced via
`${VICTORIALOGS_AUTH_USER}` and `${VICTORIALOGS_AUTH_PASSWORD}`).

**Required env vars injected into container:** `VICTORIALOGS_AUTH_USER`,
`VICTORIALOGS_AUTH_PASSWORD` — required by the container's healthcheck
shell-script (line 495) to build the auth header, and by the
command-line flag interpolation at deploy time.

**Additionally required:** nothing. VictoriaLogs reads nothing else
from the environment.

**Surplus today (leaked):** Every other secret in `/opt/klai/.env`.
This is strictly a trust-boundary concern; VictoriaLogs is a log
store, compromise means log access, but any cross-service secret
dumped here would be a pivot primitive.

**Action:** Replace `env_file: .env` with an explicit block listing
only `VICTORIALOGS_AUTH_USER` and `VICTORIALOGS_AUTH_PASSWORD`. The
healthcheck shell already reads them via `$$VICTORIALOGS_AUTH_*` so
shape is unchanged.

---

## Migration approach decision: inline `environment:` over per-service `env_file:`

Two patterns satisfy REQ-1:

1. **Inline `environment:` block** — every required var listed
   explicitly in docker-compose.yml, value sourced via `${VAR}` from
   `/opt/klai/.env`. Pattern already used by portal-api and most
   other services.
2. **Per-service `env_file:`** — a separate `.env` file for the
   service (decrypted from its own SOPS file) mounted via
   `env_file: ./<service>/.env`. Pattern used today by klai-mailer
   and klai-connector.

**Recommendation for the four services in this SPEC: inline `environment:`.**

Rationale:

- **Reviewability.** Reading `docker-compose.yml` tells you exactly
  what each service gets. No jumping between files. The portal-api
  block is already 36 lines long and easy to review.
- **No new SOPS files.** Creating per-service `.env.sops` for
  portal-api, retrieval-api, scribe-api, victorialogs is additional
  infra work (4 new SOPS files, each with its own set of SOPS
  recipients). Out of scope for this SPEC.
- **CI gate symmetry.** REQ-4's grep is simpler when the target
  pattern is the shared bare form. Per-service forms already exist
  and need to keep working; inline-for-new-migrations keeps the gate
  focused.
- **REQ-6 stays open.** A future refactor that splits portal-api's
  secrets into `klai-infra/core-01/portal-api/.env.sops` can migrate
  from the inline block to the per-service `env_file:` pattern
  without conflicting with this SPEC. REQ-6 explicitly permits that
  path.

The one case worth reconsidering is **portal-api**: its block will
grow from 36 to ~60 env vars after the migration. If reviewer fatigue
becomes real, a follow-up SPEC can introduce
`klai-infra/core-01/portal-api/.env.sops` and switch portal-api to
the per-service pattern. That is explicitly NOT in scope here, to
keep this SPEC tractable.

---

## CI implementation

Lightweight grep-based check, added to an existing workflow (prefer
`.github/workflows/deploy-compose.yml` or a new
`.github/workflows/env-scope-guard.yml`). Both options are acceptable
during implementation — the workflow name matters less than the
enforcement.

```yaml
# .github/workflows/env-scope-guard.yml
name: env-scope-guard (SPEC-SEC-ENVFILE-SCOPE-001)

on:
  pull_request:
    paths:
      - "deploy/docker-compose.yml"

jobs:
  no-shared-env-file:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Forbid bare env_file: .env in deploy/docker-compose.yml
        run: |
          set -u
          # Match:   env_file: .env           (same-line form)
          # Match:   env_file:                (followed by)
          #            - .env                 (list-item form)
          # DO NOT match: env_file: ./klai-mailer/.env
          # DO NOT match: env_file: ./klai-connector/.env
          # DO NOT match: env_file: ./librechat/getklai/.env
          if grep -nE '^\s*env_file:\s*\.env\s*$' deploy/docker-compose.yml; then
            echo "::error file=deploy/docker-compose.yml::Bare 'env_file: .env' is forbidden by SPEC-SEC-ENVFILE-SCOPE-001. Use an explicit environment: block or a per-service env_file: ./<svc>/.env path. See deploy/SECRETS_MATRIX.md."
            exit 1
          fi
          if grep -nzE 'env_file:\s*\n\s*-\s*\.env\s*\n' deploy/docker-compose.yml; then
            echo "::error file=deploy/docker-compose.yml::Multi-line 'env_file:\n  - .env' is forbidden by SPEC-SEC-ENVFILE-SCOPE-001."
            exit 1
          fi
          echo "env-scope-guard: OK"
```

Notes:

- Two patterns cover both YAML forms (scalar and single-item list).
- Matches `env_file: .env` at end of line, tolerates trailing
  whitespace.
- Relative paths (`./svc/.env`) are allowed because the first `\s*`
  after `env_file:` would be followed by `./` or any non-`.env`
  token.
- The `grep -nzE` variant reads the file as null-delimited so `\n`
  matches real newlines. Relying on `-z` rather than `-P` keeps the
  check portable across ubuntu-latest shells.
- The check runs only when `deploy/docker-compose.yml` changes. Fast
  — a single grep — so no concern about overhead.

**Activation timing (REQ-5.5):** only add the workflow after all four
migrations have landed on main. Landing it earlier blocks the
in-progress migration PRs on their own introduction.

---

## Reference: why `env_file:` leaks more than expected

Docker Compose's `env_file:` directive injects every key/value pair
from the referenced file into the container as an environment
variable. There is no filter, no mask, no "declare what you need".
The spec is at
<https://docs.docker.com/compose/compose-file/05-services/#env_file>.

By contrast, `environment:` entries (either the map form `KEY: value`
or the list form `- KEY=value`) create one env var per entry and
only those entries. Interpolation `${VAR}` is evaluated against the
`.env` file that Compose itself reads for variable substitution
(which is a separate concern from what ends up inside the container).

This asymmetry is the root of the anti-pattern. The fix — replace
`env_file: .env` with an explicit list — relies on the same shared
`.env` continuing to exist and continuing to supply values via
interpolation, but the container process only sees the keys the
compose file explicitly enumerates.

---

## Reference: existing per-service SOPS files and their state

From `klai-infra/core-01/`:

- `caddy/.env.sops` — used by `caddy` service via `deploy.sh caddy`. Compose loads it as `environment:` vars under caddy's explicit block (not via `env_file:`). Already compliant.
- `litellm/.env.sops` — similar pattern for litellm. Already compliant.
- `zitadel/.env.sops` — similar pattern for zitadel. Already compliant.
- `klai-mailer/.env.sops` — decrypted to `deploy/klai-mailer/.env`, which is what `env_file: ./klai-mailer/.env` reads. **Compliant** with REQ-6 pattern, but the .env file content should be audited to ensure it contains only mailer-relevant vars (REQ-6.3).

Per-service SOPS files DO NOT exist today for: portal-api,
retrieval-api, scribe-api, victorialogs, knowledge-ingest,
klai-connector (its `.env` file in repo is checked-in plaintext for
non-secrets; secrets still come from the global), klai-knowledge-mcp,
docs-app, and every Vexa service. Creating them is out of scope.

---

## Reference: why not rename env vars at the same time

Tempting, but high-risk. Several env vars are read under multiple
names today:

- `INTERNAL_SECRET` → portal-api reads it via `PORTAL_API_INTERNAL_SECRET`
  (compose maps `INTERNAL_SECRET: ${PORTAL_API_INTERNAL_SECRET}`).
- Retrieval-api reads `INTERNAL_SECRET` as `RETRIEVAL_API_INTERNAL_SECRET`.
- Multiple services read the same `KNOWLEDGE_INGEST_SECRET` under that
  literal name.
- Mailer reads `WEBHOOK_SECRET` (its own scope) NOT the portal
  `VEXA_WEBHOOK_SECRET`.

Renaming any of these during migration would require a coordinated
SOPS update + pipeline retest + rollback plan. Each rename is a
separate SPEC. This SPEC stays binary-compatible: same keys, same
values, narrower scope.

---

## Reference: interaction with `deploy.sh main` merge behaviour

`deploy.sh main` (documented in sops-env.md) decrypts
`klai-infra/core-01/.env.sops` and MERGES into `/opt/klai/.env`,
preserving server-only keys. This SPEC's change does not touch
`deploy.sh`. The shared `.env` continues to exist at
`/opt/klai/.env`; only the compose-level behaviour changes.

Put differently: `/opt/klai/.env` stays fat (it's the SOPS
projection), but containers no longer receive the whole file — they
only receive the subset the compose file enumerates.

---

## Open questions (tracked, not blocking)

- Should SECRETS_MATRIX.md also flag which secrets are rotation
  candidates under SPEC-SEC-005? Leaning yes — one column "rotation
  cadence" — but not a blocking requirement for v1 of the matrix.
- Should the CI gate also enforce that every env var in an
  `environment:` block has a matching SECRETS_MATRIX.md row? Possible
  follow-up SPEC; out of scope for this one to keep the CI gate
  grep-simple.
- Does Docker Compose ever read `environment:` vars from a file
  BEFORE interpolation happens? The relevant behaviour is documented:
  Compose reads the working-directory `.env` for interpolation at
  `docker compose up` time, separately from the container's runtime
  env. Both mechanisms coexist without leaking into the container
  unless `env_file:` is present. Confirmed against Compose v2
  reference and `docker compose config portal-api` output on a
  staging host.
- Should the rollout include a "measure env-var count before/after"
  check to make the impact visible? Yes — suggested for the runbook,
  not a requirement. `docker exec <ctr> printenv | wc -l` before and
  after each migration is a nice operational signal.
- Should librechat-getklai, klai-mailer, klai-connector's per-repo
  `.env` files be audited in this SPEC (REQ-6.3)? Only if a concrete
  drift is found. Default: leave alone; document in a follow-up issue
  if the audit surfaces a leak.
