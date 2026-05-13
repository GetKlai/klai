# Getting Started

Get the Klai portal running locally in under 10 minutes. No external accounts or access required.

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Docker Desktop | 4.x+ | [docker.com](https://www.docker.com/products/docker-desktop/) |
| Python | 3.12+ | `brew install python@3.12` |
| uv | latest | `brew install uv` |
| Node.js | 20 LTS+ | `brew install node@20` |

## Quick Start

```bash
git clone https://github.com/GetKlai/klai.git && cd klai

make setup      # copies env files, installs dependencies
make dev-up     # starts PostgreSQL, Redis, MongoDB, Meilisearch, LiteLLM
make migrate    # runs database migrations
make backend    # starts FastAPI on :8010 (auto-creates dev user)
# In a second terminal:
make frontend   # starts Vite on :5174
```

Open [http://localhost:5174](http://localhost:5174) — you're logged in as a dev user. No Zitadel, no OIDC, no external auth.

## How It Works

The default configuration uses **standalone mode** (Mode C):

- **Backend**: `AUTH_DEV_MODE=true` bypasses Zitadel authentication. All API requests authenticate as `dev-user-1`. The backend auto-creates a dev organization and user on first startup.
- **Frontend**: `VITE_AUTH_DEV_MODE=true` skips the OIDC login flow entirely. No redirect to any identity provider.
- **Billing**: `MOCK_BILLING=true` skips Moneybird API calls.

## AI Features

AI features (chat, knowledge base) require an LLM API key. Add yours to `.env.dev`:

```bash
ANTHROPIC_API_KEY=sk-ant-...
```

Then restart the Docker services: `make dev-down && make dev-up`.

## Other Modes

For core developers with access to the production Zitadel instance, two additional modes are available. See [docs/runbooks/local-dev.md](docs/runbooks/local-dev.md) for details:

- **Mode A (Frontend-only)**: Only the frontend runs locally, API calls proxy to production. No Docker needed.
- **Mode B (Full-stack + Zitadel)**: Full local stack with real OIDC authentication via `auth.getklai.com`.

## Useful Commands

| Command | Description |
|---------|-------------|
| `make help` | Show all available targets |
| `make seed` | Add demo data (extra users) |
| `make dev-status` | Check Docker service health |
| `make dev-reset` | Delete all data and start fresh |
| `make lint` | Run linters (ruff + eslint) |
| `make check` | Run type checks (pyright + tsc) |

## Troubleshooting

**Port 5434 in use**: Another PostgreSQL is running. Check with `lsof -nP -iTCP:5434 -sTCP:LISTEN`.

**Backend crashes with AES-256 error**: The encryption keys weren't generated. Re-run setup:
```bash
rm klai-portal/backend/.env
make setup
```
This regenerates `.env` with fresh encryption keys. If you need to generate keys manually:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"          # PORTAL_SECRETS_KEY, ENCRYPTION_KEY
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # SSO_COOKIE_KEY
```

**Database migration fails**: Reset everything: `make dev-reset && make dev-up && make migrate`.

For more detailed troubleshooting, see [docs/runbooks/local-dev.md](docs/runbooks/local-dev.md).
