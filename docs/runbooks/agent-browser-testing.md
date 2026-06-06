# Agent browser and E2E testing

This is the short contract agents must follow before opening the Klai portal in
a browser.

## Local standalone UI

Use this when validating local UI changes.

```bash
make setup
make dev-up
make migrate
make backend
make frontend
scripts/local-dev-status.sh --mode local --strict
```

Then open the URL printed by the preflight. Defaults are:

- Frontend: `http://localhost:5174/`
- Backend: `http://localhost:8010/`

In Conductor, `CONDUCTOR_PORT` changes those to:

- Frontend: `CONDUCTOR_PORT`
- Backend: `CONDUCTOR_PORT+1`

Local standalone must not redirect to `my.getklai.com/login`. If it does, stop
and run:

```bash
scripts/local-dev-status.sh --mode local --strict
```

Do not continue through production login while claiming to test local UI.

## Production E2E

Use this only when intentionally testing the deployed environment.

```bash
scripts/local-dev-status.sh --mode prod-e2e
cd klai-portal/frontend
source .env.local
npm run test:e2e:prod
```

Production E2E must not target `localhost`.

## Env files

- `klai-portal/frontend/.env.development.local` is for Vite local dev.
- `klai-portal/frontend/.env.local` may contain production E2E credentials.
- Do not overwrite `.env.local` during local dev setup.

