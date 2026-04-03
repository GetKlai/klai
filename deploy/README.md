# Klai Self-Hosting Guide

This directory contains everything needed to self-host the Klai platform on a single server (core-01).

---

## Prerequisites

- A Linux server (Ubuntu 22.04+ recommended) with at least 8 GB RAM and 4 CPU cores
- Docker 24+ and Docker Compose v2 (`docker compose`)
- A domain name with DNS managed by Hetzner (required for wildcard TLS via the Hetzner DNS plugin)
- [SOPS](https://github.com/getsops/sops) and [age](https://github.com/FiloSottile/age) for secret management
- An SSH key pair for server access

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
SERVER_HOST=core-01          # hostname for the server
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

### 6. Harden Docker networking

```bash
# On the server (after docker compose up -d):
sudo bash scripts/harden-docker-user.sh
```

---

## What Gets Deployed

| Service | Purpose |
|---------|---------|
| **Zitadel** | Identity provider (SSO, user management, OIDC) |
| **LiteLLM** | AI model proxy (Mistral API + Ollama fallback) |
| **LibreChat** | Chat interface for end users |
| **Portal API** | Tenant provisioning and management API |
| **Caddy** | Reverse proxy with wildcard TLS (Hetzner DNS) |
| **klai-mailer** | Transactional email via Zitadel HTTP notifications |
| **GlitchTip** | Frontend error tracking |
| **Grafana** | Monitoring dashboards |
| **VictoriaMetrics** | Metrics storage |
| **VictoriaLogs** | Log aggregation |
| **Grafana Alloy** | Metrics and log collection agent |
| **Qdrant** | Vector database for Knowledge module |
| **knowledge-ingest** | RAG ingestion and retrieval pipeline |
| **klai-knowledge-mcp** | MCP server for saving to personal knowledge base |
| **Gitea** | Knowledge base content store (internal) |
| **docs-app** | Klai Docs (Next.js) |
| **SearXNG** | Self-hosted web search for research features |
| **Whisper Server** | Speech-to-text transcription |
| **scribe-api** | Transcription API |
| **research-api** | Document Q&A |
| **PostgreSQL** | Primary relational database |
| **MongoDB** | LibreChat conversation storage |
| **Redis** | Caching and session storage |
| **Meilisearch** | Full-text search for LibreChat |

---

## Configuration

All service configuration is driven by a single `.env` file on the server at `/opt/klai/.env`.
This file is deployed from your encrypted `config.sops.env` via `deploy.sh`.

Key variable groups:

| Group | Variables |
|-------|-----------|
| Domain | `DOMAIN`, `ADMIN_EMAIL` |
| DNS/TLS | `HETZNER_AUTH_API_TOKEN` |
| Database passwords | `POSTGRES_PASSWORD`, `MONGO_ROOT_PASSWORD`, `REDIS_PASSWORD`, `MEILI_MASTER_KEY` |
| Zitadel | `ZITADEL_MASTERKEY`, `ZITADEL_DB_PASSWORD`, `ZITADEL_ADMIN_PASSWORD`, `ZITADEL_ORG_NAME`, `ZITADEL_ADMIN_*` |
| LiteLLM | `LITELLM_MASTER_KEY`, `LITELLM_DB_PASSWORD`, `MISTRAL_API_KEY` |
| LibreChat | `LIBRECHAT_KLAI_JWT_SECRET`, `LIBRECHAT_KLAI_OIDC_*` |
| Grafana | `GRAFANA_ADMIN_PASSWORD`, `GRAFANA_CADDY_USER`, `GRAFANA_CADDY_HASH` |
| Portal API | `PORTAL_API_ZITADEL_PAT`, `PORTAL_API_DB_PASSWORD`, `PORTAL_API_INTERNAL_SECRET` |
| Monitoring | `KUMA_TOKEN_*` (Uptime Kuma push tokens) |

---

## Observability

Structured logging is collected centrally:

| Component | Purpose |
|-----------|---------|
| Grafana Alloy | Log collection from Docker containers |
| VictoriaLogs | Log storage (30-day retention) |
| Grafana | Dashboards and log exploration |

Configuration files:
- `deploy/alloy/config.alloy` — Alloy collection config
- `deploy/grafana/provisioning/datasources/victorialogs.yaml` — Grafana datasource
- `deploy/grafana/provisioning/dashboards/logs.json` — Log explorer dashboard

For cross-server log shipping (public-01 to core-01):
- DNS: `logs-ingest.${DOMAIN}` pointing to core-01
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

# Run any new migrations if needed:
docker exec -i klai-core-postgres-1 psql -U klai -d klai < postgres/migrations/001_knowledge_schema.sql
```

Before major updates, take a backup first:

```bash
bash scripts/backup.sh
```

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
