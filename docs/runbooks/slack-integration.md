# Runbook: Klai in Slack (klai-slack)

Spec: `docs/specs/SPEC-SLACK-001/spec.md`. Service: `klai-slack/`. Slack calls
`https://api.getklai.com/slack/*`, Caddy forwards it to `klai-slack:8000`, and
klai-slack answers through the Partner API of portal-api with one managed
`pk_live_` key per Slack workspace.

## 1. Create the Slack app

1. Open `api.slack.com/apps` -> **Create New App** -> **From a manifest**, pick
   the Klai workspace, paste `klai-slack/manifest.yml`.
2. The manifest carries the bot scopes, the five bot events, the `/klai` slash
   command, interactivity, the OAuth redirect and the `agent_view` block. Every
   request URL is `https://api.getklai.com/slack/events`; the OAuth redirect is
   `https://api.getklai.com/slack/oauth_redirect`.
3. Slack asks to verify the events Request URL once. It only succeeds after the
   service is deployed (section 3), so create the app first, deploy, then press
   **Retry** on the verification.
4. From **Basic Information** copy the client id, client secret and signing
   secret for the SOPS file below. Do not paste them into chat, tickets or git.

## 2. SOPS entries (names only, values never in this repo)

File: `klai-infra/core-01/klai-slack/.env.sops`. Edit it with the procedure in
`klai-infra/docs/runbooks/sops-edit-via-core01.md`, then decrypt again and
compare the key set.

| Key | Source |
|---|---|
| `SLACK_CLIENT_ID` | Slack app, Basic Information |
| `SLACK_CLIENT_SECRET` | Slack app, Basic Information |
| `SLACK_SIGNING_SECRET` | Slack app, Basic Information |
| `SLACK_ENCRYPTION_KEY` | generate: `python3 -c 'import secrets, base64; print(base64.b64encode(secrets.token_bytes(32)).decode())'` (base64 of exactly 32 bytes; startup refuses anything else) |
| `KLAI_SLACK_DB_PASSWORD` | generate a fresh random password |

`PORTAL_API_INTERNAL_SECRET` already exists in the main SOPS env and is reused in
both directions; do not add a second secret. `deploy/SECRETS_MATRIX.md` lists
each variable and the service that reads it.

Compose interpolates these at `compose-up.sh` time from `/opt/klai/.env`, so the
keys must be merged into the live env (`sync-env.yml` / `deploy/deploy.sh
klai-slack`) before the container starts. A missing or empty value is a startup
failure, by design.

## 3. First deploy, in this order

0. **Image first.** `deploy-compose.yml` and the pre-commit hook refuse a compose
   file that references an image that is not anonymously pullable, and a new GHCR
   package starts private. So: merge `klai-slack/` plus `klai-slack.yml` first (the
   build job publishes `ghcr.io/getklai/klai-slack`), set the package to public in
   the GitHub package settings, run `sh deploy/check-image-pullable.sh`, and only
   then merge the compose and Caddy change. The compose commit on this branch was
   made with `--no-verify` for that reason.
1. **SOPS** (section 2) is in place on core-01.
2. **Database.** `deploy/postgres/init.sql` only runs on an empty volume, so on
   the existing production volume create the role and database once, reading the
   password from the live env instead of typing it:
   ```bash
   ssh core-01
   set -a; source /opt/klai/.env; set +a
   docker exec -i klai-core-postgres-1 \
     psql -U klai -d klai -v ON_ERROR_STOP=1 -v pw="$KLAI_SLACK_DB_PASSWORD" <<'SQL'
   CREATE ROLE klai_slack LOGIN PASSWORD :'pw';
   CREATE DATABASE klai_slack OWNER klai_slack;
   SQL
   ```
   Rerun safety: if the role exists, use `ALTER ROLE klai_slack PASSWORD :'pw'`
   instead of `CREATE ROLE`.
3. **Compose + Caddy config** reach core-01 by merging to `main`
   (`deploy-compose.yml` syncs `deploy/docker-compose.yml` and
   `deploy/caddy/Caddyfile`); `caddy-validate.yml` already checked the Caddyfile
   on the PR.
4. **Image.** The `klai-slack.yml` workflow builds and pushes
   `ghcr.io/getklai/klai-slack` on every merge to `main`, but its deploy job is
   off until the repository variable `KLAI_SLACK_DEPLOY=true` exists. For the
   first run do it by hand on core-01:
   ```bash
   source /opt/klai/.env
   docker pull ghcr.io/getklai/klai-slack:latest
   /opt/klai/scripts/compose-up.sh klai-slack
   ```
   The entrypoint runs `alembic upgrade head` before uvicorn; a migration error
   stops the container.
5. **Caddy** picks up the `/slack/*` block when the sync recreates it; check
   with the commands in section 6.
6. When the verification in section 6 is green, set the repository variable
   `KLAI_SLACK_DEPLOY=true` so later merges deploy automatically.

klai-slack is on `klai-net` and `net-postgres` only. It must never join
`socket-proxy`; `scripts/audit-docker-access.sh` fails CI if it does.

## 4. Turn on the `slack` unlock for a tenant

The `/admin/slack` page and the managed key are gated by the platform unlock
`slack` (SPEC-SLACK-001 D10). A Klai platform admin sets it with a full-set
replacement, so read the current set first:

```
GET   /api/admin/orgs/{slug}/platform-unlocks
PATCH /api/admin/orgs/{slug}/platform-unlocks
      {"platform_unlocked_features": [<current set>, "slack"]}
```

The PATCH replaces the whole array and is audited in `tenant_lifecycle_events`.
Leave out the existing features and you switch them off.

## 5. How a customer admin links a workspace

1. The org admin opens `/admin/slack` in the portal, picks the knowledge bases
   Slack may use and creates the Slack key. The `pk_live_...` key is shown once.
2. They install the app: `https://api.getklai.com/slack/install` (OAuth, a Slack
   workspace admin approves).
3. In any channel the Slack workspace admin types `/klai connect pk_live_...`.
   Only workspace admins can do this, only while the workspace is unlinked. The
   service validates the key against portal-api, stores it AES-256-GCM encrypted
   and answers ephemerally. The portal page then shows `connected`.
4. `/klai kb` (workspace admins) narrows the knowledge bases per channel,
   `/klai help` shows the current scope.

Disconnect is done from the portal (`DELETE /api/admin/slack`); portal-api then
calls klai-slack, which revokes the Slack token and deletes the workspace data.
A response of `{"slack_revoked": false}` means klai-slack was unreachable: the
Klai side is gone, revoke the app in Slack manually and retry the internal call.

## 6. Verify

Run these on core-01 unless noted.

| Check | Expected |
|---|---|
| `docker ps --filter name=klai-slack --format '{{.Status}}'` | `Up ... (healthy)` |
| `docker exec <container> python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/health').status)"` | `200` |
| `curl -s -o /dev/null -w '%{http_code}' https://api.getklai.com/slack/install` | `200` (Bolt's install page with an "Add to Slack" link) or a `302` to `slack.com/oauth/v2/authorize`; a `502` means the container or the Caddy upstream is down |
| `curl -s https://api.getklai.com/internal/installations/x -X DELETE` | not answered by klai-slack (Caddy only routes `/slack/*`); the internal API stays on `klai-net` |
| Slack app, Event Subscriptions, Request URL | **Verified** |
| `/klai help` in a linked workspace | ephemeral scope message |

Logs (structlog JSON, no message bodies):

```
service:klai-slack _time:15m
service:klai-slack team_id:T0123ABCD _time:1h
service:klai-slack event:slack_event_duplicate_skipped _time:1h
```

Follow a single request from Slack into portal-api by its `request_id`; klai-slack
forwards `X-Request-ID` to the Partner API call.

## 7. Troubleshooting

| Symptom | Cause | Action |
|---|---|---|
| Container restarts, log shows `Missing required: ...` or an encryption-key error | a SOPS key is absent, empty or `SLACK_ENCRYPTION_KEY` is not 32 bytes | fix SOPS, sync env, `compose-up.sh klai-slack` |
| Container restarts at `alembic upgrade head` | database or role missing, wrong `KLAI_SLACK_DB_PASSWORD` | redo section 3 step 2 |
| Slack shows `dispatch_failed` or Request URL unverified | Caddy block not live, or container down | section 6 checks |
| `/klai connect` says the key is refused | key is not a Slack key, org lacks the `slack` unlock, or org already linked | section 4, `/admin/slack` status |
| No answers on the free Slack plan Messages tab | agent sessions API unavailable; the service falls back to DM replies | expected, see SPEC-SLACK-001 section 9 |
