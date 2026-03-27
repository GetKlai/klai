# Container Restart Pre-flight Check

**[HARD] Before running `docker compose up -d [service]`, complete all pre-flight steps. Before moving on after a restart, verify the service is healthy.**

## Pre-flight (before `up -d`)

```bash
# 1. Check which env vars the container will receive (including globals from .env)
docker compose config [service] | grep -A 60 'environment:'

# 2. Look for URL-type vars containing passwords: REDIS_URL, DATABASE_URL, etc.
#    Will any of these conflict with image defaults?
#    Special chars (/, +, =) in passwords break URL parsing — see pitfalls/devops.md#devops-redis-password-special-chars
```

Stop and resolve any conflicts before proceeding.

## Post-flight (immediately after `up -d`)

```bash
# 3. Check logs — no errors?
docker logs --tail 30 [container-name]

# 4. Health check green?
docker ps --filter name=[service] --format '{{.Names}}\t{{.Status}}'
```

**Do not proceed to the next task until both checks pass.**

## Why

A `docker compose up -d` recreates the container and injects ALL variables from `/opt/klai/.env`, including ones the service never received before. This has caused production outages when a service picked up a global `REDIS_URL` with a URL-encoded password that broke URL parsing — crashing the service that was working fine before.

See: `claude-docs/pitfalls/devops.md#devops-compose-up-inherits-global-env`

## Applies to

Any `docker compose up -d` on core-01, including:
- Config changes to docker-compose.yml
- Environment variable changes
- Image updates
- Service restarts during debugging or fixes
