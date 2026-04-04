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
