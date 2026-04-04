---
paths:
  - "**/.env*"
  - "**/*.env"
  - "**/*sops*"
  - "klai-infra/**"
---
# SOPS & Environment Variables

## KUMA_TOKEN vars (CRIT)
- 29 tokens only used by `push-health.sh` cron — invisible to `docker exec printenv`.
- One missing token crashes entire monitoring script (`set -u`).
- Recovery: extract from Uptime Kuma SQLite DB on public-01.

## SOPS overview
Mozilla SOPS + age encryption. Encrypted files in git, plaintext never.
- Global: `klai-infra/core-01/.env.sops` → `/opt/klai/.env`
- Per-service: `core-01/{caddy,litellm,zitadel,klai-mailer}/.env.sops` → `/opt/klai/{service}/.env`
- Deploy: `./core-01/deploy.sh {main|zitadel|litellm|caddy|klai-mailer|all}`

## Env modification rules
| Action | Allowed? | How |
|--------|----------|-----|
| Add NEW var | Yes | `echo 'NEW_VAR=value' >> /opt/klai/.env` (single quotes!) |
| Change existing secret | NO | Use SOPS or ask user |
| Delete a var | NO | Ask user |
After ANY change: `docker compose up -d <service>`, verify with `docker exec <ctr> printenv VAR`.

## deploy.sh = MERGE (updated March 2026)
- `deploy.sh main` decrypts SOPS and **merges** into server `.env` — preserves manually-added vars.
- SOPS keys are added/updated; server-only keys are kept. Timestamped backup created before each merge.
- Values with `$` are auto-escaped to `$$` for docker-compose compatibility.

## SOPS must be complete
- Every var on the server must exist in SOPS with a real value.
- Never commit placeholder values (`PLACEHOLDER`, `CHANGE_ME`, `TODO`).
- Periodically audit: server `wc -l .env` vs `sops -d .env.sops | wc -l`.

## Special characters
- `$` in values: use `$$` in `.env` (docker-compose interpolation).
- `(`, `)`, `&`, `!`: wrap value in double quotes. All CI scripts `source .env`.

## Never redirect into .env.sops (CRIT)
`sops -e ... > core-01/.env.sops` destroys the file — the shell truncates BEFORE the
command runs. If encryption fails, the file is 0 bytes and ALL production secrets are gone.
Source: April 2026 incident — agent used `>` redirect, SOPS command failed, file wiped.

## Non-interactive SOPS (for agents)
The ONLY safe procedure. No shortcuts, no `>` redirects into `.env.sops`:
```bash
sops --decrypt --input-type dotenv --output-type dotenv core-01/.env.sops > core-01/.new.env
# append/modify
sops --encrypt --in-place --input-type dotenv --output-type dotenv core-01/.new.env
mv core-01/.new.env core-01/.env.sops
```
Temp file path MUST match `.sops.yaml` `path_regex`. Use literal paths, not `$HOME`.
