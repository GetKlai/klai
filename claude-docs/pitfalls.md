# Pitfalls

> Mistakes we've made. Don't repeat them.

## Structure

Shared pitfall files cover mistakes relevant across all Klai projects (DevOps, infrastructure).
Project-specific pitfalls live in each project's own `docs/pitfalls/` directory.

## Categories (shared)

| Category | File | Entries |
|----------|------|---------|
| [Process](pitfalls/process.md) | AI dev workflow, testing discipline, minimal changes | 14 entries |
| [Git](pitfalls/git.md) | Destructive commands, secrets in commits | 4 entries |
| [DevOps](pitfalls/devops.md) | Coolify, Docker, deployments, services | 2 entries |
| [Infrastructure](pitfalls/infrastructure.md) | Hetzner, SOPS, env vars, DNS, SSH | 7 entries |
| [Platform](pitfalls/platform.md) | LiteLLM, vLLM, LibreChat, Zitadel, Caddy, Grafana, Vexa | 31 entries |
| [Docs-app](pitfalls/docs-app.md) | klai-docs (Next.js) integration from portal-api | 4 entries |

## Runbooks (emergency recovery procedures)

| Runbook | File | Contents |
|---------|------|---------|
| [Platform Recovery](runbooks/platform-recovery.md) | Zitadel Login V2 deadlock, PAT rotation, portal-api outage | 3 procedures |
| [Uptime Kuma](runbooks/uptime-kuma.md) | Adding a new monitor to status.getklai.com | 7-step procedure |

## Project pitfalls

Elk project kan eigen `docs/pitfalls/` bestanden hebben. Zie het CLAUDE.md van het betreffende project.

## How to use

**Before deploying**: Scan `pitfalls/devops.md` and `pitfalls/infrastructure.md`
**Before platform work**: Read `pitfalls/platform.md` for the relevant component
**After an incident**: Run `/retro "what happened"` to capture it immediately

## Quick Reference

### Process (14)

See `pitfalls/process-rules.md` (compact table, @imported in CLAUDE.md every session).
Full descriptions with examples: `pitfalls/process.md`.

### Git (4)

| ID | Sev | Rule |
|----|-----|------|
| `git-no-destructive-commands` | **CRIT** | Never reset --hard / clean -f without confirmation |
| `git-no-secrets-in-commits` | **CRIT** | Never commit .env or credentials |
| `git-commit-specific-files` | **HIGH** | Stage specific files, not git add . |
| `git-verify-before-commit` | **HIGH** | Always git diff --staged before committing |

### DevOps (2)

| ID | Sev | Trigger | Rule |
|----|-----|---------|------|
| `devops-image-versions-from-training-data` | **HIGH** | Writing compose with pinned versions | Never use version numbers from AI training data |
| `devops-compose-restart-does-not-reload-env` | **HIGH** | Updating .env then restarting | Use `docker compose up -d`, not `restart` |

### Infrastructure (7)

| ID | Sev | Trigger | Rule |
|----|-----|---------|------|
| `infra-env-not-synced` | **HIGH** | After adding to config.sops.env | Also update Coolify env vars manually |
| `infra-sops-missing-main-env` | **HIGH** | After setting up SOPS for services | Also encrypt the main docker-compose .env in SOPS |
| `infra-sops-dotenv-dollar-sign` | **HIGH** | Secret with $ in SOPS dotenv | Use $$ for literal $ in docker-compose .env |
| `infra-docker-user-container-ip-stale` | **CRIT** | DOCKER-USER iptables with container IPs | Use port-based rules, not container IPs |
| `infra-zitadel-console-http-api` | **CRIT** | Zitadel console broken, API calls fail | Remove --tlsMode disabled, use ZITADEL_TLS_ENABLED env |
| `infra-caddy-no-global-csp` | **HIGH** | Adding CSP to Caddy global header | No global CSP -- apps manage their own |
| `infra-never-modify-env-secrets` | **CRIT** | Modifying secrets in /opt/klai/.env | NEVER modify existing secrets via shell commands |

### Platform (31)

| ID | Sev | Trigger | Rule |
|----|-----|---------|------|
| `platform-litellm-vllm-provider-prefix` | **HIGH** | LiteLLM → vLLM config | Use `hosted_vllm/` prefix, not `openai/` |
| `platform-litellm-drop-params` | **HIGH** | LiteLLM → vLLM config | Add `drop_params: true` in litellm_settings |
| `platform-vllm-gpu-memory-utilization` | **CRIT** | vLLM startup on H100 | Use ~0.55 (32B) + ~0.40 (8B), not original values |
| `platform-vllm-sequential-startup` | **CRIT** | Starting vLLM instances | Start 32B first, wait healthy, then 8B |
| `platform-vllm-mps-enforce-eager` | **HIGH** | vLLM + MPS | Add --enforce-eager to 8B instance |
| `platform-librechat-oidc-reuse-tokens` | **CRIT** | LibreChat OIDC config | Never set OPENID_REUSE_TOKENS=true |
| `platform-librechat-username-claim` | **HIGH** | LibreChat OIDC config | Always set OPENID_USERNAME_CLAIM=preferred_username |
| `platform-grafana-victorialogs-loki-incompatible` | **HIGH** | Adding VictoriaLogs to Grafana | Use victoriametrics-logs-datasource plugin |
| `platform-caddy-not-auto-routing` | **HIGH** | Adding a new tenant | Caddy doesn't auto-discover containers |
| `platform-rag-api-non-lite-image` | **HIGH** | RAG deployment (Phase 2) | Use non-lite image for TEI embeddings |
| `platform-caddy-admin-off-reload` | **HIGH** | Reloading Caddy with admin off | Restart container via Docker SDK, not Admin API |
| `platform-whisper-cuda-version` | **HIGH** | Deploying faster-whisper | CTranslate2 requires CUDA 12 + cuDNN 9 |
| `platform-fastapi-background-tasks-db-session` | **CRIT** | Passing db session to BackgroundTasks | Open new session in task, don't pass request-scoped |
| `caddy-basicauth-monitoring-conflict` | **HIGH** | Adding basic_auth to monitored route | Add health path bypass BEFORE basic_auth |
| `caddy-log-not-in-handle` | **MED** | Putting log inside handle block | log is site-level, place outside handle |
| `platform-zitadel-project-grant-vs-user-grant` | **HIGH** | Assigning role to user in Zitadel | Use /users/{id}/grants, not /projects/{id}/grants |
| `platform-zitadel-resourceowner-claim-unreliable` | **HIGH** | Using resourceowner claim for org lookup | Use sub -> portal_users -> portal_orgs instead |
| `platform-sso-cache-single-instance` | **HIGH** | Scaling portal-api to multiple instances | In-memory SSO cache not shared across replicas |
| `caddy-permissions-policy-blocks-mediadevices` | **CRIT** | Browser API silently fails | Use microphone=self, not microphone=() |
| `platform-alembic-shared-postgres-schema-conflict` | **CRIT** | Two FastAPI services sharing Postgres | Scope alembic_version to service-specific schema |
| `platform-zitadel-login-v2-recovery` | **CRIT** | Login V2 breaks all OIDC flows | Delete login_v2 row from projections; full procedure in runbooks/ |
| `platform-zitadel-pat-invalid-after-upgrade` | **CRIT** | PAT invalid after Zitadel upgrade | Rotate PAT via Zitadel console; full procedure in runbooks/ |
| `platform-vexa-timeout-looks-like-bug` | **MED** | Meeting stuck in Recording after everyone leaves | 60s everyoneLeftTimeout is intentional — read process.py first |
| `platform-vexa-guard-breaks-stop-flow` | **HIGH** | Adding status guard to webhook handler mid-debug | Never guard webhook handler — completed webhook IS the transcription trigger |
| `platform-falkordb-sspLv1-license` | **MED** | Evaluating FalkorDB | SSPLv1 = fine for self-hosted internal use, not for SaaS |
| `platform-hipporag2-vs-graphiti-different-layers` | **HIGH** | Comparing HippoRAG2 vs Graphiti | Different layers: retrieval-only vs full ingestion+graph+retrieval |
| `platform-tei-embedding-timeout` | **HIGH** | knowledge-ingest 500 on large batches | Set timeout 120s + retry + batch of 32 |
| `platform-librechat-redis-config-cache` | **HIGH** | Changing librechat.yaml, restarting container | FLUSHALL Redis first — config is cached with no TTL |
| `platform-librechat-addparams-no-envvars` | **MED** | Using env vars in addParams | addParams is literal — use LiteLLM hook or MCP headers instead |
| `platform-librechat-dual-system-message` | **MED** | promptPrefix + LiteLLM hook both inject system messages | Append to existing system message, don't prepend a new one |
| `platform-portal-api-deploy-env-preflight` | **CRIT** | Deploying portal-api after new required field in config.py | Pre-flight check env vars; add to .env.sops BEFORE pushing config change |

### Docs-app (4)

| ID | Sev | Trigger | Rule |
|----|-----|---------|------|
| `platform-docs-app-port` | **HIGH** | Calling docs-app API from portal-api | Port is 3010, not 3000 |
| `platform-docs-app-basepath` | **HIGH** | Any docs-app API call | basePath `/docs` — all routes are `/docs/api/...` |
| `platform-docs-app-visibility-values` | **HIGH** | Creating KB with visibility=internal | Map portal `internal` → docs-app `private` |
| `platform-docs-app-error-logging` | **MED** | Debugging docs-app integration | Log status code + response text, catch ConnectError separately |

---

## Severity Levels

| Level | Meaning |
|-------|---------|
| **CRIT** | Can cause data loss, break production, or require major rework |
| **HIGH** | Causes significant problems or wasted time |
| **MED** | Causes minor issues, easy to fix |

## Adding new pitfalls

1. Run `/retro "description"` -- manager-learn handles everything
2. Or manually: add entry to the appropriate category file
3. If project-specific, add to `[project]/docs/pitfalls/` instead
4. Update quick reference table above with ID, severity, and one-line rule

## Context loading strategy

CLAUDE.md @imports are loaded every session and cost context tokens. Domain-specific files are loaded on-demand via `knowledge.md`.

| Type | Where it lives | How it loads | When to use |
|------|---------------|-------------|-------------|
| Universal process rules | `pitfalls/process-rules.md` | @import in CLAUDE.md (every session) | Rules that apply to ALL sessions regardless of domain |
| Domain pitfalls (infra, platform, devops, git) | `pitfalls/[category].md` | On-demand via `knowledge.md` reference | Domain-specific mistakes, read when working in that domain |

**Rules:**
- NEVER @import domain-specific pitfall files in CLAUDE.md -- use `knowledge.md` references instead
- Only `process-rules.md` (compact table, ~20 lines) is @imported -- it contains universal AI dev workflow rules
- The full `process.md` (with examples, do/don't sections) remains as detailed reference
- When adding a new process pitfall: update both `process.md` (full) and `process-rules.md` (compact table row)
