# Deployment: portal-api and portal-frontend

Describes the CI/CD pipeline for the FastAPI backend (`backend/`) and the steps to
maintain or recover it.

## Overview

```
git push (backend/**) → GitHub Actions → GHCR image → SSH into core-01 → docker compose up
```

The workflow file is at [`.github/workflows/portal-api.yml`](../.github/workflows/portal-api.yml).

## Trigger

The pipeline runs automatically on every push to `main` that touches:

- `backend/**`
- `.github/workflows/portal-api.yml`

## Steps

| Step | What it does |
|---|---|
| Build | Builds the Docker image from `backend/Dockerfile` |
| Push | Pushes two tags to GHCR: `latest` and `<git-sha>` |
| Deploy | SSHes into core-01 and runs `docker compose up -d portal-api` |

## Required GitHub Secrets

Go to **Settings → Secrets and variables → Actions** in the `klai-portal` repo.

| Secret | Value |
|---|---|
| `CORE01_HOST` | `65.21.174.162` (IPv4 — do not use the IPv6 address) |
| `CORE01_DEPLOY_KEY` | The private SSH key that matches the deploy key on core-01 |

> **Note:** The `appleboy/ssh-action` cannot parse bare IPv6 addresses. Always use the IPv4 address.

## SSH Deploy Key

The deploy key is an ed25519 key pair. The public key is installed in
`~/.ssh/authorized_keys` for the `klai` user on core-01.

To rotate the key:

```bash
# Generate a new key pair
ssh-keygen -t ed25519 -C "github-actions@getklai.com" -f /tmp/new_deploy_key -N ""

# Install the public key on core-01
ssh core-01 "echo '$(cat /tmp/new_deploy_key.pub)' >> ~/.ssh/authorized_keys"

# Optionally remove the old key from authorized_keys on core-01

# Update the CORE01_DEPLOY_KEY secret in GitHub with the content of:
cat /tmp/new_deploy_key
```

## Manual Deployment

If the pipeline fails or you need to deploy without a push:

```bash
ssh core-01
docker pull ghcr.io/getklai/portal-api:latest
cd /opt/klai && docker compose up -d portal-api
```

To roll back to a specific build:

```bash
ssh core-01
docker compose stop portal-api
# Edit /opt/klai/docker-compose.yml to pin the image to a specific sha tag
docker compose up -d portal-api
```

## Verifying the Running Image

```bash
ssh core-01
docker inspect klai-core-portal-api-1 --format '{{.Created}} {{index .Config.Labels "org.opencontainers.image.revision"}}'
```

Or check the health endpoint:

```bash
curl --max-time 3 http://localhost:8010/health
```

---

# Deployment: portal-frontend

## Overview

```
git push (frontend/**) → GitHub Actions → npm run build → rsync to core-01
```

The workflow file is at [`.github/workflows/portal-frontend.yml`](../.github/workflows/portal-frontend.yml).

## Trigger

The pipeline runs automatically on every push to `main` that touches:

- `frontend/**`
- `.github/workflows/portal-frontend.yml`

## Steps

| Step | What it does |
|------|-------------|
| Install | `npm ci` with Node 22 and npm cache |
| Build | `npm run build` (Vite) with production environment variables |
| SSH setup | Installs `CORE01_DEPLOY_KEY` via `webfactory/ssh-agent` |
| Deploy | `rsync --delete` the `dist/` folder to `/opt/klai/portal-dist/` on core-01 |

## Build environment variables

Baked into the static build at compile time:

| Variable | Value |
|----------|-------|
| `VITE_OIDC_AUTHORITY` | `https://auth.getklai.com` |
| `VITE_OIDC_CLIENT_ID` | `362901948573220875` |
| `VITE_API_BASE_URL` | `""` (empty — API calls are relative, same origin) |

These are set in the workflow file, not in GitHub Secrets (they are not secret).

## Required GitHub Secrets

Same secrets as portal-api:

| Secret | Value |
|--------|-------|
| `CORE01_HOST` | IPv4 address of core-01 |
| `CORE01_DEPLOY_KEY` | Private SSH key matching the deploy key on core-01 |

## Serving on core-01

Caddy serves the static files from `/opt/klai/portal-dist/` as the SPA fallback for all `*.getklai.com` requests not matched by other routes. The directory is updated in-place by rsync; no service restart is needed.

## Manual Deployment

```bash
# Build locally
cd frontend
VITE_OIDC_AUTHORITY=https://auth.getklai.com \
VITE_OIDC_CLIENT_ID=362901948573220875 \
VITE_API_BASE_URL="" \
npm run build

# Deploy
rsync -av --delete dist/ klai@core-01:/opt/klai/portal-dist/
```

## Local Development

```bash
cd frontend
npm install
cp .env.local.example .env.local  # or set vars manually
npm run dev
```

The dev server runs on `http://localhost:5174`. CORS and OIDC redirect URIs are configured to allow this origin.
