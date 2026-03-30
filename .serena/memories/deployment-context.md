# Deployment Context

## Servers
| Server | Role | SSH |
|--------|------|-----|
| core-01 | Portal API, LibreChat tenants, LiteLLM, Zitadel, Qdrant, Caddy, Redis | `ssh core-01` (klai user, id_ed25519) |
| gpu-01 | TEI (BGE-M3 dense, :7997), Infinity (reranker, :7998), bge-m3-sparse (:8001), whisper-server (:8000) — SSH tunnel from core-01 at 172.18.0.1 | `ssh gpu-01` |
| public-01 | Website, Twenty CRM, Fider, Uptime Kuma | `ssh -i ~/.ssh/klai_ed25519 root@65.109.237.64` |

## GPU Services (gpu-01 → core-01 SSH tunnel)
GPU inference services run on gpu-01 and are accessed from core-01 containers via SSH tunnel bound at 172.18.0.1 (Docker host gateway):
- `http://172.18.0.1:7997` — TEI (HuggingFace text-embeddings-inference:1.5, BAAI/bge-m3 dense embeddings)
- `http://172.18.0.1:7998` — Infinity (michaelf34/infinity, BAAI/bge-reranker-v2-m3 reranker)
- `http://172.18.0.1:8001` — bge-m3-sparse (FlagEmbedding sparse SPLADE sidecar)
- `http://172.18.0.1:8000` — whisper-server (STT for scribe-api)

Tunnel managed by: `systemctl status gpu-tunnel.service` on core-01
Health check: `/opt/klai/scripts/gpu-health.sh` (also called from push-health.sh)

## Deploy Workflows
- **portal frontend:** `git push` → GitHub Action `Build and deploy portal-frontend` auto-builds + rsyncs to core-01. Always verify: `gh run watch --exit-status`
- **klai-website:** Coolify on public-01. Push to main → Coolify auto-deploys
- **Backend services:** Docker on core-01, managed via Coolify or manual `docker compose up -d`

## gh CLI
On macOS: `gh` is available on PATH (installed via Homebrew). Just use `gh run watch --exit-status`.

## Tenant Provisioning
When a new org is created, portal backend auto-provisions:
1. Zitadel org + OIDC app for LibreChat
2. LibreChat Docker container (`librechat_image` from settings, default: ghcr.io/danny-avila/librechat:v0.8.3-rc2)
3. Caddy config for `{slug}.getklai.com`
4. LiteLLM team key

Container data: `/opt/klai/librechat/{slug}/`
Caddy tenants dir: `/caddy/tenants/`

## Environment Management
- Core env file: `/opt/klai/.env`
- NEVER modify existing secrets with sed/echo — shell `$` truncation corrupts values silently
- Add new vars: `echo 'NEW=value' >> /opt/klai/.env` (single quotes)
- Change existing: use SOPS (klai-infra submodule at `klai-infra/`)
- After change: verify with `docker exec <container> printenv VAR_NAME`

## Infrastructure Repos
- Secrets/SOPS: `klai-infra/` (git submodule, private)
- Deploy configs: `deploy/` (in monorepo)
- Claude assets: `.claude/` (in monorepo)

## Monorepo location
`/Users/mark/Server/projects/klai`

## LiteLLM API Keys (internal services)
- `LITELLM_MASTER_KEY` — used by internal services: research-api, retrieval-api, knowledge-ingest
- `LITELLM_LIBRECHAT_KEY` — virtual key for LibreChat containers (scoped per team via provisioning)
- knowledge-ingest requires `LITELLM_API_KEY: ${LITELLM_MASTER_KEY}` for LLM enrichment (contextual prefix + HyPE)
