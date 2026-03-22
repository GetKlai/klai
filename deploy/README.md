# Klai — Self-Hosting Guide

This directory contains everything you need to run Klai on your own server.

## Prerequisites

- A Linux server (Ubuntu 24.04 recommended), min. 8 GB RAM / 4 vCPU
- A domain name pointed at your server
- Docker and Docker Compose installed
- [SOPS](https://github.com/getsops/sops) + [age](https://github.com/FiloSottile/age) for secret management

## Quick start

**1. Clone the repo and enter the deploy directory**
```bash
git clone https://github.com/GetKlai/klai.git
cd klai/deploy
```

**2. Configure your instance**
```bash
cp config.example.env config.env
# Edit config.env — fill in your domain, server IP, and SSH key
```

**3. Run setup**
```bash
chmod +x setup.sh
./setup.sh
```

Setup will:
- Harden the server (UFW, DOCKER-USER iptables)
- Pull all Docker images
- Initialize the database
- Start all services

**4. Verify**

After setup, these services should be reachable:
- `https://auth.${DOMAIN}` — Zitadel (auth)
- `https://chat.${DOMAIN}` — LibreChat
- `https://my.${DOMAIN}` — Klai Portal
- `https://grafana.${DOMAIN}` — Grafana monitoring (basic auth)

## What gets deployed

| Service | Purpose |
|---------|---------|
| Zitadel | OIDC authentication, user management |
| LiteLLM | LLM proxy — route to OpenAI, Mistral, Ollama, etc. |
| LibreChat | Chat interface |
| Portal API | Tenant provisioning, knowledge base API |
| Caddy | Reverse proxy, automatic TLS (Let's Encrypt) |
| PostgreSQL | Primary database (pgvector for embeddings) |
| MongoDB | LibreChat message storage |
| Redis | Session cache, queues |
| Meilisearch | Full-text search for LibreChat |
| Ollama | Local LLM inference (CPU by default) |
| VictoriaMetrics | Metrics storage |
| VictoriaLogs | Log storage |
| Grafana | Observability dashboard |
| Alloy | Log and metrics collector |

## Configuration

All configuration lives in `config.env` (from `config.example.env`). Key variables:

| Variable | Description |
|----------|-------------|
| `DOMAIN` | Your base domain (e.g. `example.com`) |
| `SERVER_IP` | Server IP address |
| `ADMIN_EMAIL` | Email for Let's Encrypt and admin account |
| `SSH_PUBKEY` | Your SSH public key |

Secrets (API keys, database passwords) are managed via SOPS. See the SOPS section below.

## Secret management

Klai uses [SOPS](https://github.com/getsops/sops) + [age](https://github.com/FiloSottile/age) to store secrets encrypted in git.

**Generate an age key:**
```bash
age-keygen -o ~/.config/sops/age/keys.txt
```

**Edit secrets:**
```bash
sops core-01/.env.sops
```

**Deploy secrets to server:**
```bash
./deploy.sh all
```

Never commit plaintext `.env` files. Only `*.sops*` encrypted files are safe to commit.

## Updating

```bash
git pull
cd deploy
docker compose pull
docker compose up -d
```

> **PostgreSQL** is pinned to a major version tag (`pg17`) — major version upgrades require a dump/restore. See the upgrade procedures in `SERVERS.md` (your private instance notes).

## Security hardening

After every `docker compose up -d`, re-apply the iptables rules to prevent Docker from bypassing UFW:

```bash
./scripts/harden-docker-user.sh
```

This script enforces that only ports 80 and 443 accept inbound traffic — all other direct container access is blocked.

## Adding LLM providers

Edit `litellm/config.yaml` to add models. LiteLLM supports OpenAI, Anthropic, Mistral, Azure, and many others. Restart LiteLLM after changes:

```bash
docker compose up -d litellm
```
