# Klai Self-Hosting Guide

This directory contains public self-hosting templates for the Klai platform on a
single server. It is not a copy of Klai's private production infrastructure.
Use your own host inventory, DNS targets, secrets, and deployment procedures.

---

## Prerequisites

- A Linux server (Ubuntu 22.04+ recommended) with at least 8 GB RAM and 4 CPU cores
- Docker 24+ and Docker Compose v2 (`docker compose`)
- A domain name with DNS managed by Hetzner (required for wildcard TLS via the Hetzner DNS plugin)
- [SOPS](https://github.com/getsops/sops) and [age](https://github.com/FiloSottile/age) for secret management
- An SSH key pair for server access
- Pull access to every image referenced by `docker-compose.yml`

Klai's own runtime images are published as `ghcr.io/getklai/*`. The public
self-host path expects these packages to be public and anonymously pullable
from GitHub Container Registry. Before starting the stack, verify from a fresh
Docker config so an existing local GHCR login cannot hide a private package:

```bash
tmp_docker_config="$(mktemp -d)"
DOCKER_CONFIG="$tmp_docker_config" sh check-image-pullable.sh
rm -rf "$tmp_docker_config"
```

For GetKlai maintainers: every package referenced by the public compose file
must be public before advertising this guide as unauthenticated self-hosting:

- `caddy-hetzner`
- `crawl4ai`
- `klai-connector`
- `klai-docker-authz`
- `klai-docs`
- `klai-knowledge-mcp`
- `klai-mailer`
- `klai-slack`
- `knowledge-ingest`
- `librechat`
- `portal-api`
- `presidio-analyzer`
- `retrieval-api`
- `scribe-api`

If `portal-api` still points at the archived `GetKlai/klai-portal` repository
in package settings, remove that repository source and grant Actions access to
`GetKlai/klai`. The current source lives in this monorepo at
`klai-portal/backend/Dockerfile`; the archived repository is no longer needed
for package publication.

---

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/GetKlai/klai.git
cd klai/deploy
```

### 2. Configure your instance

```bash
cp config.example.env config.env
```

Edit `config.env` and fill in your values:

```env
SERVER_HOST=app-server       # hostname for your server
SERVER_IP=1.2.3.4            # public IP address
SERVER_USER=deploy           # non-root deploy user (created by setup.sh)
SSH_PUBKEY="ssh-ed25519 ..." # your SSH public key
DOMAIN=example.com           # your domain
ADMIN_EMAIL=you@example.com  # used for TLS certificate notifications
```

Then create your secrets file (`.env.sops`) with all service credentials. Encrypt with SOPS before committing:

```bash
sops --encrypt config.env > config.sops.env
rm config.env
```

### 3. Run initial server setup

On first boot (as root):

```bash
sops --decrypt config.sops.env > config.env
source config.env
bash setup.sh
```

This will:
- Update the system and install Docker, UFW, and Fail2ban
- Create the deploy user with SSH key access
- Harden SSH (disable root and password login)
- Configure the UFW firewall (ports 22, 80, 443)

### 4. Build the custom Caddy image

```bash
# On the server, from /opt/klai/caddy/
bash build.sh
```

Caddy is built with the Hetzner DNS plugin (for wildcard TLS) and the rate-limit module.

### 5. Deploy secrets and start services

```bash
# From your local machine:
./deploy.sh all

# On the server:
ssh deploy@your-server
cd /opt/klai
docker compose up -d
```

If Compose fails with `unauthorized` for `ghcr.io/getklai/...`, stop there:
that is a registry visibility problem, not a database or service startup
problem. Publish the package, then retry. A wave of `context canceled` errors
after the first failed pull is the normal Docker Compose cascade.

### 6. Harden Docker networking

```bash
# On the server (after docker compose up -d):
sudo bash scripts/harden-docker-user.sh
```

---

## What Gets Deployed

`docker-compose.yml` is the source of truth for the services and their image
versions. The groups below are what it defines; the architecture overview in
[`docs/architecture/platform.md`](../docs/architecture/platform.md) has the
full map. GPU-bound inference runs from a second file, `docker-compose.gpu.yml`
(see the note after the table).

| Group | Services | Purpose |
|-------|----------|---------|
| Edge | **Caddy** | Reverse proxy with wildcard TLS (Hetzner DNS) |
| Identity | **Zitadel** | Identity provider (SSO, user management, OIDC) |
| Product | **Portal API**, **klai-connector**, **scribe-api**, **docs-app**, **klai-knowledge-mcp**, **klai-mailer**, **klai-slack** | Portal backend and workspace provisioning, connector sync, transcription API, Klai Docs (Next.js), MCP server, transactional email via Zitadel notifications, Slack app |
| Knowledge | **knowledge-ingest**, **retrieval-api**, **Qdrant**, **FalkorDB**, **Gitea**, **Garage**, **docling-serve**, **crawl4ai**, **SearXNG** | Ingestion, hybrid retrieval, vector store, knowledge graph, docs content repositories, S3 object storage, file conversion, crawling, web search |
| AI | **LiteLLM**, **Presidio** (analyzer and anonymizer), **LibreChat** | Model gateway (Mistral API), PII masking, chat interface for end users (the portal provisions one LibreChat container per workspace) |
| Meetings | **Vexa** services (`vexa12-*`) | Meeting bots and transcript collection |
| Data stores | **PostgreSQL**, **MongoDB**, **Redis**, **Meilisearch** | Relational data, LibreChat storage, caching and sessions, LibreChat search |
| Docker access | **docker-socket-proxy**, **klai-docker-authz** | Restricted access to the Docker API for provisioning and the meeting bots |
| Observability | **Grafana**, **VictoriaMetrics**, **VictoriaLogs**, **Grafana Alloy**, **cAdvisor**, **GlitchTip** | Dashboards, metrics, logs, collection, error tracking |
| Applications | **CRM**, **Umami**, **Fider**, **listmonk**, **Vaultwarden**, **Calnode** | Third-party applications the stack also hosts |

Embeddings, the reranker, sparse vectors and speech-to-text run from
`docker-compose.gpu.yml` on a separate GPU host that the main stack reaches
over a tunnel. The speech-to-text service there is the Vexa transcription
service (faster-whisper workers behind a balancer); the `whisper-server`
reference image is published but is not part of this stack. Ollama is in the
compose file but no LiteLLM model or fallback uses it.

---

## Configuration

All service configuration is driven by a single `.env` file on the server at `/opt/klai/.env`.
This file is deployed from your encrypted `config.sops.env` via `deploy.sh`.

Key variable groups are defined in `config.example.env` and mapped in
`docker-compose.yml`. Treat all credentials, keys, tokens, and passwords as
SOPS-managed secrets; do not commit plaintext values.

For a per-service secret inventory, see `SECRETS_MATRIX.md`.

### listmonk Portal Automation Role

The portal-api listmonk API user needs permissions for idempotent subscriber
upserts, duplicate lookup, list membership updates, and transactional sends.
After creating or rotating the API user/token, run:

```bash
cd /opt/klai
LISTMONK_API_USER=twenty-crm-sync bash scripts/listmonk-ensure-portal-role.sh
```

The script is idempotent and restarts listmonk so role permission changes take
effect immediately.

---

## Observability

Structured logging is collected centrally:

| Component | Purpose |
|-----------|---------|
| Grafana Alloy | Log collection from Docker containers |
| VictoriaLogs | Log storage (30-day retention; a second instance keeps content-class lines for 7 days) |
| Grafana | Dashboards and log exploration |

Configuration files:
- `deploy/alloy/config.alloy` — Alloy collection config
- `deploy/grafana/provisioning/datasources/datasources.yaml`: Grafana datasources
- `deploy/grafana/provisioning/dashboards/logs.json` — Log explorer dashboard

For cross-server log shipping:
- DNS: `logs-ingest.${DOMAIN}` pointing to your log-ingest host
- Environment variable: `VICTORIALOGS_INGEST_TOKEN` (bearer auth)

---

## Updating

Pull the latest compose file and restart:

```bash
# On the server:
cd /opt/klai
git pull                          # if deployed from monorepo
docker compose pull               # pull latest images
docker compose up -d              # restart changed services
```

Database migrations need no separate step: knowledge-ingest runs
`alembic upgrade head` when it starts (`klai-knowledge-ingest/entrypoint.sh:9-10`),
and the portal migrations run during the portal-api deploy
(`deploy/scripts/deploy-portal-api.sh`).

Before major updates, take a backup first:

```bash
bash scripts/backup.sh
```

---

## Troubleshooting

### GHCR `unauthorized` during `docker compose up`

Symptom:

```text
Head "https://ghcr.io/v2/getklai/<service>/manifests/latest": unauthorized
```

Meaning: Docker cannot pull at least one Klai-owned container image from GHCR.
For the public self-host path, this means the package is still private or the
tag does not exist. Fix the package visibility/tag first; later `context
canceled` messages are usually a cascade from the first failed pull.

Then verify the exact manifest:

```bash
docker manifest inspect ghcr.io/getklai/portal-api:latest >/dev/null
```

The repository-level guard is:

```bash
cd deploy
sh check-image-pullable.sh
```

### Zitadel first-instance domain already exists

Symptom:

```text
Errors.Instance.Domain.AlreadyExists
```

This comes from Zitadel's first-instance bootstrap, usually when an existing
Zitadel database/volume already contains an instance for `auth.${DOMAIN}` or
when first-instance/default-instance settings are rerun against partially
initialized state.

Check the actual state before changing config:

```bash
docker compose logs zitadel --tail 200
docker compose ps postgres zitadel
```

For a truly fresh install, remove the partial Zitadel/Postgres state and rerun
bootstrap. For a non-fresh install, do not delete data; create or rotate the
required PAT manually in Zitadel and update `/opt/klai/.env`.

---

## Security Hardening

After initial deployment, run the Docker firewall hardening script:

```bash
sudo bash scripts/harden-docker-user.sh [interface]
```

This sets up DOCKER-USER iptables rules so that only ports 80 and 443 are
reachable from the internet. All other ports (including the Zitadel port 8080)
are blocked at the firewall level, even if they are mapped in docker-compose.yml.

The script also enables Fail2ban with a Caddy-specific filter to ban IPs that
repeatedly fail basic auth on the Grafana dashboard.

To persist rules across reboots, ensure `iptables-persistent` is installed:

```bash
apt-get install iptables-persistent
```
