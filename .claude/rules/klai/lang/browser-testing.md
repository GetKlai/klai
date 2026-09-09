---
paths:
  - "klai-portal/frontend/**"
  - "**/e2e/**"
  - "**/*.spec.ts"
  - "scripts/local-dev-status.sh"
---
# Local / Production Browser Testing Contract

Before any browser-driven portal check (Playwright, Browser MCP, manual
localhost navigation, screenshots, or E2E), establish which runtime contract is
being tested. Do not guess ports, auth mode, proxy target, or whether a
localhost listener belongs to this workspace.

- **Local standalone UI** means: frontend in `VITE_AUTH_DEV_MODE=true`, backend
  in `AUTH_DEV_MODE=true`, frontend proxying to the local backend, no Zitadel,
  no production login redirect. The required preflight is:
  `scripts/local-dev-status.sh --mode local --strict`. If it fails, fix setup or
  report the failure. Do not continue clicking through login.
- **Production E2E** means: no localhost target. Validate credentials/target
  with `scripts/local-dev-status.sh --mode prod-e2e`, then run from
  `klai-portal/frontend` with `source .env.local && npm run test:e2e:prod`.
- **Conductor ports**: if `CONDUCTOR_PORT` is set, the frontend port is
  `CONDUCTOR_PORT` and the backend port is `CONDUCTOR_PORT+1`; otherwise they
  default to `5174` and `8010`. Use `make frontend` / `make backend` or the
  preflight output. Never start an ad-hoc Vite server on a random port to
  "just check" a portal route.
- **Env files**: Vite dev config belongs in
  `klai-portal/frontend/.env.development.local`. `klai-portal/frontend/.env.local`
  may contain production E2E credentials and must not be overwritten for local
  dev.
- If a local portal route lands on `my.getklai.com/login` or another production
  login page while you intended local standalone testing, stop immediately and
  diagnose with `scripts/local-dev-status.sh --mode local --strict`.
