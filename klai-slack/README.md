# klai-slack

Klai in Slack (SPEC-SLACK-001). A FastAPI service with Bolt for Python mounted
on `/slack/*`. It speaks Slack on one side (HTTP Events API, OAuth install
flow, slash commands) and the Klai Partner API on the other: every Slack
workspace is linked to one Klai org through a managed `pk_live_` key, stored
AES-256-GCM encrypted.

## Endpoints

| Method | Path | Who calls it | Auth |
|---|---|---|---|
| `POST` | `/slack/events` | Slack: events, slash commands, interactivity | Slack signing secret (Bolt) |
| `GET` | `/slack/install` | Browser: starts the Slack OAuth install | OAuth state |
| `POST` | `/slack/connect` | Browser, form-POSTed by the portal: starts the portal-driven link | signed link token (`token=` form field) |
| `GET` | `/slack/oauth_redirect` | Slack: OAuth callback | OAuth state |
| `DELETE` | `/internal/installations/{team_id}` | portal-api: disconnect | `Authorization: Bearer <INTERNAL_SECRET>` |
| `GET` | `/health` | Docker healthcheck | none |

## Configuration

Copy `.env.example` to `.env`. Every secret is mandatory and checked at
startup; `SLACK_ENCRYPTION_KEY` must be base64 of exactly 32 bytes. In
production the values come from SOPS in `klai-infra` and the compose
`environment:` block (SPEC-SLACK-001 §7.5).

## Run locally

```bash
uv sync
createdb klai_slack                      # any local PostgreSQL
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --port 8000
```

Slack must reach the HTTP route, so expose port 8000 through a tunnel
(for example `cloudflared tunnel --url http://localhost:8000`) and set in your
development Slack app:

- Event Subscriptions and Interactivity request URL: `https://<tunnel>/slack/events`
- Slash command `/klai`: `https://<tunnel>/slack/events`
- OAuth redirect URL: `https://<tunnel>/slack/oauth_redirect`, and `PUBLIC_BASE_URL=https://<tunnel>`

Link a workspace from the portal page `/admin/slack` ("Verbind met Slack"),
which form-POSTs a link token to `https://<tunnel>/slack/connect`.
Socket Mode is not wired up; the HTTP route is what production uses.

## Tests and gates

```bash
uv run ruff check . && uv run ruff format --check .
uv run --with pyright pyright
uv run pytest -q
SLACK_TEST_DATABASE_URL=postgresql+asyncpg://…/klai_slack_test uv run pytest -q -m postgres
```

The default run is database-free. The `postgres` tests migrate the given
database to head, exercise the repository and the encrypted Bolt store, and
downgrade it again, so point them at a throwaway database.

## Layout

| Module | Role |
|---|---|
| `app/main.py` | FastAPI app, Bolt handler mount, hourly retention purge |
| `app/slack_app.py` | `AsyncApp`, OAuth settings, event dedupe middleware, uninstall handling |
| `app/commands/` | `/klai` dispatcher (`SUBCOMMANDS`), `kb`, `help` |
| `app/portal_link.py` | `POST /slack/connect` and the OAuth callback of the portal-driven link |
| `app/clients/partner.py` | Partner API: knowledge bases, chat, feedback |
| `app/clients/portal.py` | portal-api `/internal/slack/link-oauth` and `/internal/slack/link/{team_id}` |
| `app/repo.py` | all database access |
| `app/services/scope.py` | effective knowledge-base scope (spec §5) |
| `app/oauth_store.py`, `app/crypto.py` | encrypted Bolt installation store, AES-256-GCM |
