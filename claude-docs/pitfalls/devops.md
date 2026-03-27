# DevOps Pitfalls

> Coolify, Docker, deployments, service management

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

## devops-deploy-path-mismatch

**Severity:** CRIT

**Trigger:** Frontend deploy via rsync completes successfully but the site does not update in the browser

A CI job may rsync the build output to one directory while the web server (Caddy, Nginx) serves from a different directory. The deploy reports success, but production stays on the old bundle.

**What happened:** The `portal-frontend` GitHub Action rsynced to `/opt/klai/portal-dist/` but Caddy serves from `/srv/portal/`. The new JS bundle sat in the staging directory for weeks while users saw the old version. The Action exit code was 0.

**How to detect:**
```bash
# Check the file timestamps in the directory the web server actually serves
ssh core-01 "ls -lt /srv/portal/assets/*.js | head -3"

# If the newest file is days/weeks old, the deploy target is wrong
# Compare with the staging directory
ssh core-01 "ls -lt /opt/klai/portal-dist/assets/*.js | head -3"
```

**How to prevent:**
1. The rsync step in the CI workflow must end at the directory the web server serves — not a staging directory
2. If a two-step rsync is used (staging → serving), both steps must be in the workflow
3. After every deploy, verify the bundle timestamp matches the deploy time (see `klai-claude/rules/klai/ci-verify-after-push.md` Step 2)

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

Full protocol: `klai-claude/rules/klai/ci-verify-after-push.md`

---

## See Also

- [patterns/devops.md](../patterns/devops.md) - Proven deployment patterns
- [pitfalls/infrastructure.md](infrastructure.md) - Infrastructure-level mistakes
