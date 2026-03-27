# DevOps Patterns

> Coolify deployments, Docker, service management, CI/CD

## Index
> Keep this index in sync — add a row when adding a pattern below.

| Pattern | When to use |
|---|---|
| [sops-env-sync](#sops-env-sync) | Updating secrets in `klai-infra/core-01/.env.sops` |
| [docker-compose-sync](#docker-compose-sync) | Adding or removing a service in `docker-compose.yml` |
| [coolify-env-update](#coolify-env-update) | Adding or changing an env var for a Coolify service |
| [public-01-ssh](#public-01-ssh) | SSH access to public-01 (Coolify, Uptime Kuma) |
| [core-01-ssh](#core-01-ssh) | SSH access to core-01 (AI stack, portal) |
| [coolify-redeploy](#coolify-redeploy) | Triggering a redeploy after a config change |
| [docker-rebuild-no-cache](#docker-rebuild-no-cache) | Force full rebuild after dependency or base image change |
| [ghcr-ci-deploy-build-on-server](#ghcr-ci-deploy-build-on-server) | Deploy when GHCR registry auth is stale |
| [uptime-kuma-add-monitor](#uptime-kuma-add-monitor) | Adding a new service to status monitoring |
| [umami-access](#umami-access) | Accessing Umami analytics dashboard |

---

## sops-env-sync

**When to use:** Updating secrets in `klai-infra/core-01/.env.sops`

Pushing a change to `core-01/.env.sops` on main automatically triggers the
`sync-env.yml` workflow in `klai-infra`. That workflow decrypts the file and
writes `/opt/klai/.env` on core-01. No manual action required.

**After a SOPS update lands:** services that need the new value must be restarted
manually (or by their own deploy workflow). Secrets sync does NOT restart containers.

**Manual sync (emergency / new machine / CI unavailable):**
```bash
cd klai-infra && ./core-01/deploy.sh main
```

**Adding a new required field to `config.py`:**
1. Add the value to `core-01/.env.sops` (push → auto-syncs to server)
2. Then push the `config.py` change (portal-api workflow will pre-flight check before deploying)

**Rule:** Never manually edit `/opt/klai/.env` for permanent changes — always go via `.env.sops`.
Manual edits are lost the next time `sync-env.yml` runs.

---

## docker-compose-sync

**When to use:** Adding or removing a service in `deploy/docker-compose.yml`

The CI service workflows (`knowledge-ingest.yml`, `retrieval-api.yml`, etc.) do NOT copy the compose file to the server — they only pull the new image and restart the specific service. The compose file must be synced separately.

**Automated sync:** The `deploy-compose.yml` workflow runs automatically when `deploy/docker-compose.yml` changes on main. It does a sparse checkout and copies the file to `/opt/klai/docker-compose.yml`.

**Manual sync (emergency):**
```bash
scp "$(git rev-parse --show-toplevel)/deploy/docker-compose.yml" core-01:/opt/klai/docker-compose.yml
```

**After syncing:** Restart any services whose definition changed:
```bash
ssh core-01 "cd /opt/klai && docker compose up -d <service>"
```

**New services:** After adding a new service to the compose file, the automated sync will copy the file, but you must still start the service manually the first time (CI only restarts services it knows about).

---

## coolify-env-update

**When to use:** Adding or changing an environment variable for a Coolify service

Variables set in `klai-infra/config.sops.env` are NOT automatically synced to Coolify.
Always update both places.

**Steps:**

```bash
# 1. Add to SOPS env file
cd klai-infra
sops config.sops.env
# Add: NEW_VAR=value

# 2. Update in Coolify UI
# Go to: http://public-01:8000 → Service → Environment Variables
# Add the same variable there

# 3. Trigger redeploy from Coolify (required for new env vars to take effect)
```

**Rule:** SOPS is the source of truth for secrets. Coolify needs a manual sync.

**See also:** `claude-docs/pitfalls/infrastructure.md#infra-env-not-synced`

---

## public-01-ssh

**When to use:** Accessing public-01 (Coolify host, Uptime Kuma) via SSH

```bash
# Use klai_ed25519 key as root — NOT markv, NOT id_ed25519
ssh -i ~/.ssh/klai_ed25519 root@65.109.237.64
```

`markv@65.109.237.64` with `id_ed25519` is rejected. Root + klai_ed25519 is the working combination.

---

## core-01-ssh

**When to use:** Accessing core-01 (AI stack, portal, vexa) via SSH

```bash
# ALWAYS use the SSH config alias — NEVER direct IP
ssh core-01
```

**Critical rules:**
- NEVER use `ssh klai@65.21.174.162` or `ssh root@65.21.174.162` — direct IP connections time out (firewall/routing)
- NEVER try multiple key/user combinations — **fail2ban is active** and will ban the IP after failed attempts
- The SSH config alias handles the correct key, user, and routing automatically
- If `ssh core-01` times out or returns "Permission denied", check: are you using the correct terminal/SSH agent? Do not retry with different flags.

**fail2ban jail:** `sshd` — active, has banned 2000+ IPs historically from brute-force attempts.

---

## coolify-redeploy

**When to use:** After a config change, env var update, or to apply new code

```bash
# Via Coolify UI: Service → Deploy → Redeploy
# Or via Coolify API (if configured):
curl -X POST http://public-01:8000/api/v1/deploy \
  -H "Authorization: Bearer $COOLIFY_TOKEN" \
  -d '{"uuid": "SERVICE_UUID"}'
```

**Rule:** Always check build logs after redeploy. A successful trigger does not mean a successful deploy.

---

## docker-rebuild-no-cache

**When to use:** After updating a dependency, changing a base image, or when stale layers cause issues

```bash
# Force full rebuild without cache
docker build --no-cache -t service-name .

# Or via docker-compose
docker compose build --no-cache service-name
docker compose up -d service-name
```

**Rule:** Use `--no-cache` after any dependency or base image change. Cached layers can silently run old code.

---

## ghcr-ci-deploy-build-on-server

**When to use:** CI deploys a private `ghcr.io/getklai/...` image to core-01, but the server's docker login credentials are stale or missing

`docker pull ghcr.io/getklai/<image>:latest` on core-01 fails with `denied` when the server has no valid ghcr.io credentials. The GITHUB_TOKEN passed via `appleboy/ssh-action` `envs:` also fails — it is scoped to the CI runner context, not the remote shell.

**Solution:** Build the image directly on the server from the public monorepo, instead of pulling from the registry.

```yaml
- name: Deploy to core-01
  uses: appleboy/ssh-action@v1
  with:
    host: ${{ secrets.CORE01_HOST }}
    username: klai
    key: ${{ secrets.CORE01_DEPLOY_KEY }}
    script: |
      cd /tmp && rm -rf klai-build
      git clone --depth=1 --filter=blob:none --sparse https://github.com/GetKlai/klai.git klai-build
      cd klai-build && git sparse-checkout set focus/<service>
      docker build -t ghcr.io/getklai/<service>:latest ./focus/<service>
      rm -rf /tmp/klai-build
      cd /opt/klai && docker compose up -d <service>
```

**Why this works:**
- `GetKlai/klai` is a public repo — no auth needed for clone
- The sparse checkout pulls only the relevant service directory (~seconds)
- The built image uses the same tag as before; `docker compose up -d` picks it up normally
- The CI step still pushes to ghcr.io as an artifact (useful for rollbacks)

**Services using this pattern:** `research-api` (since 2026-03-26)

**Alternative (when registry auth can be fixed):** Store a GitHub PAT with `read:packages` in `/opt/klai/.env` as `GHCR_READ_PAT`, then: `echo "$GHCR_READ_PAT" | docker login ghcr.io -u getklai --password-stdin`

---

## uptime-kuma-add-monitor

**When to use:** Adding a new service to monitoring / status page (status.getklai.com)

Uptime Kuma runs on **public-01** as a Coolify-managed container (`uptime-kuma-ucowwogo0ogoskwk0ggg4o48`). Its state lives in SQLite — no config file. Changes require direct DB writes + container restart.

Two monitor types: **push** (internal services, heartbeat from `push-health.sh` on core-01) and **HTTP** (public endpoints polled directly).

**Full 7-step procedure:** `runbooks/uptime-kuma.md`

---

## umami-access

**When to use:** Accessing or managing the Umami website analytics dashboard

Umami runs on public-01 as a Coolify-managed service.

- **Dashboard:** `https://analytics.getklai.com` — credentials in team password manager (admin account)
- **Container:** `umami-o48wg8wc0cc448gkcs4scsko`
- **Database:** dedicated PostgreSQL container `postgresql-o48wg8wc0cc448gkcs4scsko` (managed by Coolify)
- **Image:** `ghcr.io/umami-software/umami:3.0.3`
- **Website ID:** `bf92a12b-08fe-47f7-a3f1-3ed88d2ba379` (used in the tracker script on getklai.com)

**To check health:**
```bash
ssh -i ~/.ssh/klai_ed25519 root@65.109.237.64 "docker logs --tail 20 umami-o48wg8wc0cc448gkcs4scsko"
curl -s https://analytics.getklai.com/api/heartbeat  # returns {"ok":true}
```

**Tracker script** is in `klai-website/src/layouts/Base.astro` — rendered production-only (`import.meta.env.PROD`).

**Custom events** tracked: `cta-click`, `waitlist-open/submit/close`, `billing-toggle`, `faq-expand`, `lang-switch`, `contact-submit`, `careers-submit`, `outbound-link`, `scroll-depth`.

---
