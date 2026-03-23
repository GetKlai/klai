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

## See Also

- [patterns/devops.md](../patterns/devops.md) - Proven deployment patterns
- [pitfalls/infrastructure.md](infrastructure.md) - Infrastructure-level mistakes
