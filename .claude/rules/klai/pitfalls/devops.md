---
paths:
  - "**/Dockerfile"
  - "**/docker-compose*.yml"
  - ".github/**/*.yml"
  - "**/*.sh"
---
# DevOps Pitfalls

> Coolify, Docker, deployments, service management

## Index
> Keep this index in sync — add a row when adding an entry below.

| Entry | Sev | Rule |
|---|---|---|
| [devops-image-versions-from-training-data](#devops-image-versions-from-training-data) | HIGH | WebSearch current version before pinning any image |
| [devops-compose-restart-does-not-reload-env](#devops-compose-restart-does-not-reload-env) | HIGH | Use `up -d` not `restart` after `.env` changes |
| [devops-compose-up-inherits-global-env](#devops-compose-up-inherits-global-env) | HIGH | Pre-flight check env vars before `docker compose up -d` |
| [devops-redis-password-special-chars](#devops-redis-password-special-chars) | HIGH | URL-encode special chars (`/`, `+`, `=`) in Redis password |
| [devops-ghcr-auth-silent-stale-deploy](#devops-ghcr-auth-silent-stale-deploy) | HIGH | Store GHCR PAT in SOPS; use `set -e` in deploy script |
| [devops-deploy-path-mismatch](#devops-deploy-path-mismatch) | CRIT | Verify rsync target matches web server serve directory |
| [devops-ci-green-not-enough](#devops-ci-green-not-enough) | HIGH | CI green ≠ production rollout; always verify on server |
| [devops-alembic-multiple-heads](#devops-alembic-multiple-heads) | HIGH | Run `alembic heads` after merging branches with migrations |
| [devops-alembic-duplicate-object-on-rerun](#devops-alembic-duplicate-object-on-rerun) | HIGH | Use `IF NOT EXISTS` in all migration DDL statements |
| [devops-recover-secrets-from-running-containers](#devops-recover-secrets-from-running-containers) | HIGH | Recover lost env vars from running containers before restarting |
| [devops-no-manual-server-edits](#devops-no-manual-server-edits) | CRIT | Never edit compose/env on server — repo is source of truth |

---

## devops-image-versions-from-training-data

**Severity:** HIGH

**Trigger:** Writing a `docker-compose.yml` or any infrastructure file with pinned image versions

Never use version numbers from AI training data. Training data is always months to years out of date. Version numbers that "feel right" (e.g. `redis:7`, `postgres:16`) may be multiple major versions behind current stable.

**What happened:** The initial stack used Redis 7 (EOL Feb 2026), Meilisearch v1.12 (25 minor versions behind v1.37), Grafana 11 (one major version behind 12), and MongoDB 7 (one major behind 8). Redis 7.2 had already passed end-of-life when discovered.

**What to do:**
1. For every image tag in a compose file, use `WebSearch "service-name latest stable version"` to find the current version
2. Verify the tag actually exists before writing it: `docker pull image:tag` or check Docker Hub/GitHub releases
3. Never write a floating tag like `main-stable` or `latest` in production — always pin to an explicit version
4. After pinning, note the version in the running services table in `SERVERS.md`

**Red flags:**
- Writing `redis:7`, `postgres:16`, `mongo:7` — these are version numbers that existed during training, not necessarily current
- Using a floating tag like `main-stable` without knowing what version it resolves to
- Copying version numbers from documentation examples or tutorials (often outdated)

---

---

## devops-compose-restart-does-not-reload-env

**Severity:** HIGH

**Trigger:** Updating `/opt/klai/.env` on the server and then restarting a service with `docker compose restart`

`docker compose restart [service]` stops and starts the existing container with the **same environment variables** that were injected when the container was first created. It does NOT re-read `.env`.

**Wrong:**
```bash
sed -i 's/^SOME_TOKEN=.*/SOME_TOKEN=new-value/' /opt/klai/.env
docker compose restart portal-api   # Old value is still active — restart did nothing
```

**Correct:**
```bash
sed -i 's/^SOME_TOKEN=.*/SOME_TOKEN=new-value/' /opt/klai/.env
docker compose up -d portal-api     # Recreates container, re-reads .env
```

**Always verify after env changes:**
```bash
docker exec klai-core-portal-api-1 env | grep SOME_TOKEN
```

**Rule:** After any change to `.env` or any `env_file:` referenced in docker-compose.yml, use `docker compose up -d [service]`, not `restart`.

**This applies to ALL env sources — including per-tenant env_file paths:**
```bash
# Added var to /opt/klai/librechat/getklai/.env
echo 'KNOWLEDGE_INGEST_SECRET=abc123' >> /opt/klai/librechat/getklai/.env
docker compose restart librechat-getklai   # WRONG — var still missing in container
docker compose up -d librechat-getklai     # Correct — container recreated, var present
```

**Always verify after env changes:**
```bash
docker exec librechat-getklai printenv KNOWLEDGE_INGEST_SECRET
```

---

## devops-compose-up-inherits-global-env

**Severity:** HIGH

**Trigger:** Running `docker compose up -d [service]` na een config change, of op een service zonder expliciete `env_file`

`docker compose up -d` recreëert de container en injecteert **alle** variabelen uit `/opt/klai/.env` — inclusief vars die de service nooit eerder zag. Als het image een env var als default gebruikt die opeens een waarde krijgt vanuit `.env`, kan dat onverwacht gedrag of een crash veroorzaken.

**Wat er mis ging:** Vexa bot-manager werkte prima. Na `docker compose up -d` (voor een andere config change) pakte de container opeens de globale `REDIS_URL` op uit `.env`. Die URL bevatte een wachtwoord met speciale tekens — URL parsing crashte. Vóór de recreatie was deze var onbekend bij de container.

**Verplichte pre-flight check vóór elke `docker compose up -d`:**
```bash
# 1. Check welke env vars de service krijgt (inclusief globale .env vars)
docker compose config [service] | grep -A 50 'environment:'

# 2. Let specifiek op URL-vars met wachtwoorden: REDIS_URL, DATABASE_URL, etc.
#    Conflicteren ze met image defaults?

# 3. Na start: direct logs checken VOOR je verder gaat
docker logs --tail 30 [container-name]

# 4. Health check groen?
docker ps --filter name=[service] --format '{{.Names}}\t{{.Status}}'
```

**Regel:** Nooit verder gaan na een `up -d` zonder expliciete verificatie dat de service healthy is en de logs geen errors tonen.

---

## devops-redis-password-special-chars

**Severity:** HIGH

**Trigger:** Redis wachtwoord opnemen in een `redis://` URL via `${REDIS_PASSWORD}`

Wachtwoorden met `/`, `+`, `=` breken URL parsing als ze niet URL-encoded zijn. Python's `redis.from_url()` en andere clients parsen `redis://:wachtwoord/met/slashes@host:6379` verkeerd — de `/` wordt als URL-pad geïnterpreteerd, waarna de port als wachtwoord wordt gezien.

**Fout:**
```yaml
REDIS_URL: redis://:${REDIS_PASSWORD}@redis:6379   # Kapot als wachtwoord / of + bevat
```

**Correct:** Gebruik een aparte URL-encoded variabele in `.env`:
```bash
# In .env — handmatig URL-encoden (/ → %2F, + → %2B, = → %3D):
REDIS_URL=redis://:hPKBf%2FKXA%2B%2F%2FOixZhv...@redis:6379
```

Voorkeur: gebruik services die `REDIS_HOST` + `REDIS_PORT` + `REDIS_PASSWORD` **apart** accepteren — die zijn robuuster dan een volledige URL.

**Referentie in deze stack:** `FIRECRAWL_REDIS_URL` in `.env` is correct URL-encoded. Gebruik dat als voorbeeld, nooit een raw `${REDIS_PASSWORD}` in een URL.

**Detectie:** `ValueError: Port could not be cast to integer` of `Authentication required` bij Redis connect na container recreatie.

---

## devops-ghcr-auth-silent-stale-deploy

**Severity:** HIGH

**Trigger:** CI workflow deploys via SSH + `docker compose up -d` and the deploy appears to succeed but the server runs an old image.

**What happened:** `docker pull ghcr.io/getklai/...` failed silently on core-01 because the Docker credentials in `/home/klai/.docker/config.json` contained an expired `ghs_` token (GitHub App installation token). The `docker compose up -d` still succeeded using the cached old image. CI reported success but the new code was never deployed. This was discovered weeks after the fact when a breaking change was not reflected in production.

**Root causes:**
1. No `set -e` in the deploy script — `docker pull` failure didn't abort the script
2. GITHUB_TOKEN (via `envs:`) had already expired or was not scoped for pulls from the org's packages
3. The server's stored credentials in `~/.docker/config.json` were stale

**What to do:**

1. **Use a permanent PAT stored in `/opt/klai/.env`** instead of passing GITHUB_TOKEN from the workflow:
   ```yaml
   - name: Deploy to core-01
     uses: appleboy/ssh-action@v1
     with:
       host: ${{ secrets.CORE01_HOST }}
       username: klai
       key: ${{ secrets.CORE01_DEPLOY_KEY }}
       script: |
         set -e
         source /opt/klai/.env
         echo "$GHCR_READ_PAT" | docker login ghcr.io -u mvletter --password-stdin
         docker pull ghcr.io/getklai/SERVICE:latest
         cd /opt/klai && docker compose up -d SERVICE
   ```

2. **Always use `set -e`** — without it, `docker pull` failure silently continues to `docker compose up -d` with the old image.

3. **Store `GHCR_READ_PAT` in SOPS** (`klai-infra/core-01/.env.sops`), deploy via `deploy.sh main`. The PAT is a fine-grained personal access token scoped to `read:packages`.

**Why not GITHUB_TOKEN via envs?**
- `GITHUB_TOKEN` is a short-lived installation token. When passed via `envs:` it may have expired by the deploy step.
- The `envs:` mechanism requires the variable to be set on the _GitHub Actions runner_, not the target server. Storing it in the server `.env` is more reliable and doesn't depend on GHA token lifecycle.

**Diagnosis:** To check if an image is stale on the server:
```bash
docker inspect ghcr.io/getklai/SERVICE:latest --format '{{.Created}}'
```
Compare to the CI build time.

---

## devops-deploy-path-mismatch

**Severity:** CRIT

**Trigger:** Frontend deploy via rsync completes successfully but the site does not update in the browser

A CI job may rsync the build output to one directory while the web server (Caddy, Nginx) serves from a different directory. The deploy reports success, but production stays on the old bundle.

**What happened:** The `portal-frontend` GitHub Action rsynced to `/opt/klai/portal-dist/` but Caddy serves from `/srv/klai-portal/`. The new JS bundle sat in the staging directory for weeks while users saw the old version. The Action exit code was 0.

**How to detect:**
```bash
# Check the file timestamps in the directory the web server actually serves
ssh core-01 "ls -lt /srv/klai-portal/assets/*.js | head -3"

# If the newest file is days/weeks old, the deploy target is wrong
# Compare with the staging directory
ssh core-01 "ls -lt /opt/klai/portal-dist/assets/*.js | head -3"
```

**How to prevent:**
1. The rsync step in the CI workflow must end at the directory the web server serves — not a staging directory
2. If a two-step rsync is used (staging → serving), both steps must be in the workflow
3. After every deploy, verify the bundle timestamp matches the deploy time (see `.claude/rules/klai/post-push.md` Step 2)

**Red flags:**
- User reports a new feature is missing after a green CI run
- `ls -lt` on the serve directory shows files from days ago
- The CI logs show a successful rsync but to a different path than the web server's `root` directive

---

## devops-ci-green-not-enough

**Severity:** HIGH

**Trigger:** Declaring a deploy complete after `gh run watch` returns exit code 0

CI passing means the code compiled, linted, and the container was built. It does NOT mean the new code is running in production. Always verify the server rollout after a green CI.

**What to do:**
1. `gh run watch --exit-status` — wait for green
2. Check server-side: container age (`docker ps`), health endpoint, log output, or bundle timestamp
3. Only then declare the deploy complete

Full protocol: `.claude/rules/klai/post-push.md`

---

## devops-alembic-multiple-heads

**Severity:** HIGH

**Trigger:** Running `alembic upgrade head` after merging a feature branch that added its own migration while main also had new migrations

When two branches independently add Alembic migrations (each based on the same `down_revision`), merging them creates multiple "heads." Alembic refuses to run `upgrade head` when it detects more than one head because it cannot determine the order.

**What happened:** During SPEC-KB-012, the taxonomy feature branch added a migration while main had accumulated 3 other migrations (settings tab, RLS policies, etc.). After merging, Alembic reported 4 heads and refused to upgrade.

**Symptom:**
```
ERROR [alembic.util.messaging] Multiple head revisions are present for given argument 'head'
```

**Prevention:**
1. Before merging a feature branch with migrations, check for head conflicts:
   ```bash
   alembic heads
   ```
2. If multiple heads exist, create a merge migration:
   ```bash
   alembic merge heads -m "merge heads"
   ```
3. Alternatively, if one branch's migrations are already applied on the server, stamp the server to the correct state and then target the specific migration:
   ```bash
   alembic stamp <already-applied-revision>
   alembic upgrade <target-revision>
   ```

**Rule:** After any branch merge that involves Alembic migrations, run `alembic heads` to check for multiple heads before deploying. Resolve multi-head situations locally, not on the server.

---

## devops-alembic-duplicate-object-on-rerun

**Severity:** HIGH

**Trigger:** Running an Alembic migration that creates database objects (RLS policies, indexes, constraints) which already exist on the server

When a migration creates objects without `IF NOT EXISTS` guards, and those objects were previously created manually or by a partial migration run, Alembic fails with `DuplicateObject` or `DuplicateTable` errors. The migration is not marked as applied, so retrying hits the same error.

**What happened:** During SPEC-KB-012 deployment, an earlier RLS policies migration (`c5d6e7f8a9b0`) had been partially applied — the policies existed on the server but the migration was not stamped. Running `alembic upgrade` failed with `DuplicateObjectError` on the RLS policy creation.

**Symptom:**
```
sqlalchemy.exc.ProgrammingError: (psycopg.errors.DuplicateObject)
  policy "..." for table "..." already exists
```

**Fix for an already-applied migration stuck in this state:**
```bash
# Skip the problematic migration by stamping it as done
alembic stamp <problematic-revision>

# Then continue with the remaining migrations
alembic upgrade head
```

**Prevention for new migrations:**
1. Use `IF NOT EXISTS` for policies, indexes, and constraints in migration `upgrade()`:
   ```python
   op.execute("CREATE POLICY IF NOT EXISTS ...")
   op.execute("CREATE INDEX IF NOT EXISTS ...")
   ```
2. For objects that don't support `IF NOT EXISTS` (some constraint types), wrap in a try/except in the migration or check existence first
3. Write idempotent migrations — a migration should be safe to re-run even if partially applied

**Rule:** All Alembic migrations that create policies, indexes, or other named database objects must use `IF NOT EXISTS` guards. Migrations that fail partway through leave the database in an inconsistent state that requires manual intervention.

**See also:** `pitfalls/platform.md#platform-alembic-shared-postgres-schema-conflict`

---

## devops-recover-secrets-from-running-containers

**Severity:** HIGH

**Trigger:** Environment variables on the server have been lost or overwritten, and services are still running

When `/opt/klai/.env` is wiped or corrupted, running containers still have their original environment variables in memory. These values are recoverable with `docker exec <container> printenv VAR_NAME` — but only until the container is restarted or recreated. Once a container restarts, it reads the (now broken) `.env` and the original values are gone forever.

**What happened (March 2026):**
The `sync-env.yml` workflow overwrote `/opt/klai/.env` with an incomplete SOPS file, wiping 47 vars. The `MISTRAL_API_KEY` was among the lost vars — it had been added manually to the server months ago and never back-ported to SOPS. It was recovered from the still-running LiteLLM container:
```bash
docker exec klai-core-litellm-1 printenv MISTRAL_API_KEY
```

**Critical recovery procedure:**
```bash
# 1. DO NOT restart any containers — their memory still has the real values
# 2. List all running containers
docker ps --format '{{.Names}}'

# 3. For each critical var, recover from the container that uses it
docker exec <container> printenv VAR_NAME

# 4. Build the complete .env from recovered values + known values
# 5. Write the corrected .env to the server
# 6. Only THEN restart containers that need it
```

**Key services and their critical vars:**
| Container | Critical vars to recover |
|-----------|------------------------|
| litellm | `MISTRAL_API_KEY`, `LITELLM_MASTER_KEY` |
| caddy | `HETZNER_AUTH_API_TOKEN`, `ADMIN_EMAIL` |
| portal-api | `PORTAL_API_ZITADEL_PAT`, `PORTAL_API_DB_PASSWORD` |
| zitadel | `ZITADEL_MASTERKEY` |

**IMPORTANT — non-container vars are invisible to this method:**
`KUMA_TOKEN_*` vars (monitoring tokens), `GRAFANA_CADDY_HASH`, and any other vars only used by scripts in `/opt/klai/scripts/` are NOT in any container's environment. They cannot be recovered with `docker exec printenv`. Recover these from Uptime Kuma's SQLite DB or other external sources. See `pitfalls/infrastructure.md#infra-kuma-tokens-not-in-containers`.

---

## devops-no-manual-server-edits

**Severity:** CRIT

**Trigger:** Wanting to change a config value (env var, compose setting) on core-01

NEVER edit `docker-compose.yml` or `.env` directly on the server for values that are managed by the repo. CI workflows (`sync-env.yml`, deploy steps) overwrite server files with the repo version on every push, silently reverting manual changes.

**What happened (March 2026):**
`GRAPHITI_LLM_MODEL` was changed manually on core-01 from `klai-fast` to `klai-large`. The next CI deploy of knowledge-ingest pulled a fresh image and ran `docker compose up -d`, which read the server's compose file — but a separate `Sync docker-compose.yml` workflow then overwrote the compose file with the repo version (still `klai-fast`), reverting the change.

**What to do:**
1. Edit `deploy/docker-compose.yml` in the repo
2. Commit and push
3. Let CI sync the file to the server and restart the service

**What NOT to do:**
- SSH into core-01 and edit docker-compose.yml directly
- Use `sed` to change values on the server
- Assume manual changes will persist across deploys

**Rule:** The repo is the single source of truth for all config. Server-side manual edits are always temporary and will be overwritten by CI.

**Rule:** During an env wipe incident, the first priority is recovering values from running containers. Never restart or `docker compose up -d` any service until you have recovered all critical vars. A restart reads the broken `.env` and permanently loses the in-memory values. After container recovery, also check for non-container vars by running `push-health.sh` and comparing against the script's expected variables.

**See also:** `pitfalls/infrastructure.md#infra-sops-incomplete-wipes-server`, `pitfalls/infrastructure.md#infra-kuma-tokens-not-in-containers`

---

## See Also

- [patterns/devops.md](../patterns/devops.md) - Proven deployment patterns
- [pitfalls/infrastructure.md](infrastructure.md) - Infrastructure-level mistakes
- [pitfalls/code-quality.md](code-quality.md) - Linting and type checking mistakes
