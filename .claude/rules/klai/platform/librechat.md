---
paths:
  - "deploy/librechat/**"
  - "klai-infra/core-01/librechat/**"
---
# LibreChat

## OIDC config (required per tenant)
```
OPENID_USERNAME_CLAIM=preferred_username  (default `given_name` is wrong)
OPENID_REUSE_TOKENS=false                (CRIT: true breaks existing users)
```

## MongoDB per tenant
- One database per tenant: `MONGO_URI=mongodb://mongo/{tenant_id}`.
- Shared Meilisearch is safe (userId is globally unique).

## Redis config caching
- `librechat.yaml` is cached in Redis with no TTL when `USE_REDIS=true`.
- Container restart reads from Redis, not disk.
- Correct: `redis-cli FLUSHALL` first, then restart container.

## Interface schema evolves between versions (HIGH)

LibreChat minor upgrades (e.g. 0.8.3 → 0.8.5) regularly add new keys to the
`interface:` block of `librechat.yaml`. New keys default to `true` — surfaces
we intentionally hid in the previous version (parameters, endpointsMenu,
temporaryChat, peoplePicker, fileCitations, marketplace, sidePanel,
modelSelect) silently reappear for every tenant after the upgrade.

**Why:** LibreChat treats unspecified interface keys as opt-in; there is no
"hide everything by default" mode. The base `deploy/librechat/librechat.yaml`
in this repo only disables the keys that existed at the time it was written.

**Prevention:** When bumping the LibreChat image version:

1. Diff the upstream `librechat.example.yaml` `interface:` block against our
   `deploy/librechat/librechat.yaml` — any new key MUST be added explicitly,
   even if `false` is the "boring" default.
2. Bump the `version:` field in our yaml to match the latest supported schema
   (current running version is shown in LibreChat startup logs as
   "Outdated Config version: X.Y.Z").
3. Run `/moai workflow-dispatch deploy-librechat-config.yml` after merging
   — the workflow regenerates per-tenant yaml + FLUSHALLs Redis + restarts
   every tenant container.

Ref: SEC-021 post-incident review +
https://www.librechat.ai/docs/configuration/librechat_yaml/object_structure/interface

## Unauthenticated probe endpoints (HIGH)

Pre-flight health checks on a tenant's LibreChat container can only use
endpoints that do NOT require auth. The complete whitelist is:

- `GET /health` — HTTP 200 if process is alive
- `GET /api/config` — HTTP 200 if app is bootstrapped (returns login-provider
  config, registration flags, etc.). This is the endpoint LibreChat's own
  web client calls before login.

**Do NOT probe** `/api/endpoints`, `/api/user`, `/api/keys`, or anything under
`/api/agents|messages|convos` — all require auth as of v0.8.5 and return 401.
Using them from portal-api breaks the chat-health check for every tenant.

See `klai-portal/backend/app/api/app_chat.py` for the reference probe.

## addParams limitations
- `addParams` values are literal — no `${ENV_VAR}` or `{{USER_VAR}}` interpolation.
- Only `apiKey` and `baseURL` support env var syntax.
- For dynamic context: use LiteLLM pre-call hook or MCP server headers.

## Dual system message
- `promptPrefix` in librechat.yaml + LiteLLM pre-call hook = two system messages.
- Extend existing system message content instead of prepending a new one.

## npx in container
- `npx -y <pkg>` fails due to turbo workspace detection.
- Workaround: `npm install --prefix /tmp/<pkg> <package> && node /tmp/<pkg>/...`

## Community MCP packages
- Check if vendor provides built-in MCP server first.
- Test top 5 tools end-to-end before integrating.
- Check last commit date + GitHub issues.

## TEI embedding timeout (gpu-01)
- TEI runs on gpu-01 (RTX 4000 Ada, BAAI/bge-m3). Large batches still queue up to 35s.
- Set client timeout ≥120s. Retry with exponential backoff (3 attempts). Batch size: 32.
