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
| Add NEW var | Yes | SOPS procedure below, then commit + push |
| Change existing var | Yes | SOPS procedure below, then commit + push |
| Delete a var | Ask user | Risk: may break services |

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

## Non-interactive SOPS (for agents) — run on core-01 directly

core-01 has SOPS installed and the age key at `~/.config/sops/age/keys.txt`.
**Always run SOPS on the server itself** — no local age key required, works from any OS.

```bash
# 1. SSH to core-01 and set up a working directory matching the .sops.yaml path_regex
ssh core-01 "mkdir -p /tmp/klai-sops/core-01"

# 2. Copy the SOPS file + config to the server (path_regex: core-01/.*\.env)
scp klai-infra/core-01/.env.sops core-01:/tmp/klai-sops/core-01/.env.sops
scp klai-infra/.sops.yaml core-01:/tmp/klai-sops/.sops.yaml

# 3. Decrypt, modify, encrypt — all on the server
ssh core-01 "
  cd /tmp/klai-sops &&
  SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt sops --decrypt --input-type dotenv --output-type dotenv core-01/.env.sops > core-01/.new.env &&
  sed -i 's|OLD_VAR=.*|NEW_VAR=new_value|' core-01/.new.env &&
  SOPS_AGE_KEY_FILE=~/.config/sops/age/keys.txt sops --encrypt --in-place --input-type dotenv --output-type dotenv core-01/.new.env &&
  mv core-01/.new.env core-01/.env.sops
"

# 4. Retrieve the updated encrypted file
scp core-01:/tmp/klai-sops/core-01/.env.sops klai-infra/core-01/.env.sops

# 5. Cleanup
ssh core-01 "rm -rf /tmp/klai-sops"

# 6. Commit and push — GitHub Action auto-syncs to /opt/klai/.env
cd klai-infra && git add core-01/.env.sops && git commit -m "fix(infra): update X in SOPS" && git push
```

After push, the GitHub Action decrypts and syncs to `/opt/klai/.env` automatically.
Then restart the affected service: `ssh core-01 "cd /opt/klai && docker compose up -d <service>"`.

## The path_regex requirement (CRIT)
`.sops.yaml` only encrypts files matching `core-01/.*\.env(\.sops)?$`.
The temp file on the server **must** be at `core-01/.new.env` relative to where `.sops.yaml` lives.
That is why the working directory is `/tmp/klai-sops` with both files copied there.

## Server .env — emergency-only direct edits

Direct edits to `/opt/klai/.env` are overwritten on the next `deploy.sh` or GitHub Action run.
Use ONLY as a temporary measure while the SOPS fix is in progress:

```bash
ssh core-01 "sed -i 's|OLD_VAR=.*|NEW_VAR=value|' /opt/klai/.env && docker compose up -d <service>"
```

Immediately follow up with the SOPS procedure above to make it permanent.
