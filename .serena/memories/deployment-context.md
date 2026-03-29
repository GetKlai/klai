# Deployment Context

## Servers
| Server | Role | SSH |
|--------|------|-----|
| core-01 | Portal API, LibreChat tenants, LiteLLM, Zitadel, Qdrant, Caddy, Redis | `ssh core-01` (klai user, id_ed25519) |
| public-01 | Website, Twenty CRM, Fider, Uptime Kuma | `ssh -i ~/.ssh/klai_ed25519 root@65.109.237.64` |

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
