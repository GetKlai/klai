# SPEC-INFRA-003: Implementation Plan

## Task Decomposition

| Task | Files | Effort | Risk |
|------|-------|--------|------|
| R1: vexa-redis auth | docker-compose.yml, bot config | 30 min | Medium |
| R2: firecrawl-postgres SOPS | docker-compose.yml, .env.sops | 20 min | Low |
| R3: Billing admin check | billing.py | 15 min | Low |
| R4: Restrict /metrics | Caddyfile | 15 min | Low |
| R5: CORS documentation | main.py | 5 min | None |

## Deployment Order

1. R5 (docs only)
2. R3 (next portal-api deploy)
3. R4 (Caddy restart)
4. R2 (maintenance window)
5. R1 (Redis + all bots restart, maintenance window)
