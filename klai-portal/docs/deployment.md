# Deployment: portal-api and portal-frontend

What a contributor needs to know about how the portal is built and shipped.
Both parts are built and tested by workflows in this repository. Klai's
production rollout runs from those workflows with host targets and
credentials that are managed in private infrastructure automation and do not
appear in this repository. For the platform as a whole, see
[platform.md](../../docs/architecture/platform.md).

## portal-api

Workflow: [`.github/workflows/portal-api.yml`](../../.github/workflows/portal-api.yml),
named "Build and push portal-api". It runs on every push to `main` that
touches the backend, the workflow itself, `deploy/scripts/deploy-portal-api.sh`
or `deploy/docker-compose.yml` (`portal-api.yml:3-24`), and it can be called
from other workflows.

| Job | What it does |
|---|---|
| `quality` | ruff lint and format, code guards, pyright, `pip-audit`, Alembic integrity and post-deploy guards, expand-then-contract check, pytest |
| `rls-policy-smoke-test` | Applies every `post_deploy_*.sql` to a throwaway PostgreSQL and checks the row-level-security policies (`portal-api.yml:128`) |
| `zitadel-password-policy-drift` | Checks that the production password policy matches what the portal enforces (`portal-api.yml:392`) |
| `build-push` | Builds `klai-portal/backend/Dockerfile` and pushes `ghcr.io/getklai/portal-api:<git sha>`, plus `latest` on `main` (`portal-api.yml:455-490`) |
| `scan` | Trivy scan of the image; a high or critical finding stops the deploy (`portal-api.yml:492-541`) |
| `deploy` | On `main` only, after every gate: rolls the image out to production, one run at a time (`portal-api.yml:543-560`) |

The rollout itself is `deploy/scripts/deploy-portal-api.sh`. It runs
`alembic upgrade head` from the new image, applies the post-deploy SQL files
(the row-level-security bootstrap first, then the others in alphabetical
order, skipping `*_rollback_*`), and swaps the container through
`deploy-with-rollback.sh`, which puts the previous image back when the health
probe fails after the swap (`deploy-portal-api.sh:1-24`, `:124-157`).

Migrations also run at container start-up: the image's entrypoint runs
`alembic upgrade head` before it starts uvicorn on port 8010 and stops the
container when a migration fails (`klai-portal/backend/entrypoint.sh:1-23`).
Changes to row-level-security policies live in `post_deploy_*.sql` files,
because the application database role cannot run that DDL; see
`klai-portal/backend/AGENTS.md`.

A running portal-api answers `GET /health` and `GET /api/health`
(`klai-portal/backend/app/main.py:469-476`).

## portal-frontend

Workflow: [`.github/workflows/portal-frontend.yml`](../../.github/workflows/portal-frontend.yml).
It runs on pushes to `main` that touch `klai-portal/frontend/**`,
`klai-widget/**` or the workflow (`portal-frontend.yml:3-10`).

| Job | What it does |
|---|---|
| `quality` | Locale check, `npm audit`, ESLint, design-token drift check, vitest for the portal and the widget |
| `visual` | Visual regression and accessibility audit of the component catalogue |
| `build-deploy` | Builds the widget bundle and the SPA with `npm run build` (Vite), then on `main` publishes the static `dist/` to production |

The SPA is static: API calls are relative to the page's own origin
(`VITE_API_BASE_URL` is empty), and the browser authenticates with the
session cookie that portal-api sets ([auth-flows.md](auth-flows.md)); the
frontend source reads no OIDC settings. Publishing a new build needs no
service restart.

## Local development

```bash
make backend    # portal-api with hot reload, port 8010 (or CONDUCTOR_PORT+1)
make frontend   # Vite dev server, port 5174 (or CONDUCTOR_PORT)
```

The ports come from the root `Makefile` (`Makefile:7-8`). For a local login
without Zitadel, set `AUTH_DEV_MODE` with `DEBUG` on the backend and
`VITE_AUTH_DEV_MODE` on the frontend ([auth-flows.md](auth-flows.md),
section 9). The full setup is in
[local-dev.md](../../docs/runbooks/local-dev.md).

## Self-hosting

A self-hosted deployment configures its own registry, host and deploy key in
the repository or environment that owns its deploy workflow;
`deploy/scripts/deploy-portal-api.sh` and `deploy/docker-compose.yml` are the
reference for the steps.
