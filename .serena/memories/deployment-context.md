# Deployment Context

Production deployment context is private. Live server inventory, SSH access,
host addresses, tunnel topology, and operator procedures belong in the private
`GetKlai/klai-infra` repository.

## Public Service Model

The public repo documents product services and self-hosting templates:

- portal API and frontend
- LiteLLM and LibreChat integration
- knowledge ingestion and retrieval
- Scribe transcription APIs
- optional self-hosted inference backends

Contributors do not need Klai production access to work on product behavior,
tests, or public build workflows.

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
