---
paths:
  - "**/Dockerfile"
  - "**/docker-compose*.yml"
  - ".github/**/*.yml"
  - "**/*.sh"
---
# Deployment & CI/CD

## CI deploy verification (CRIT)
CI green ≠ production rollout. After `gh run watch --exit-status`:
1. Check container age: `docker ps --format '{{.Names}}\t{{.Status}}'`
2. Verify health endpoint or logs: `docker logs --tail 20 <ctr>`
3. Bundle timestamp for frontend: `ls -lt /srv/klai-portal/assets/*.js | head -3`

### Server rollout verification
Frontend: newest `.js` timestamp must match deploy time. If old, rsync target may be wrong.
```bash
ssh core-01 "ls -lt /srv/klai-portal/assets/*.js | head -3"
ssh core-01 "grep -l 'expected_keyword' /srv/klai-portal/assets/*.js"
```
Backend: container `CreatedAt` must be recent, health must return `{"status":"ok"}`.
```bash
ssh core-01 "docker ps --filter name=portal-api --format 'table {{.Names}}\t{{.Status}}\t{{.CreatedAt}}'"
ssh core-01 "curl -s http://localhost:8010/health"
```
Never skip verification — even for trivial changes or successful local builds.

## docker-compose.yml sync
CI service workflows do NOT copy compose to server — only pull image + restart.
`deploy-compose.yml` auto-syncs when `deploy/docker-compose.yml` changes on main.
Manual: `scp deploy/docker-compose.yml core-01:/opt/klai/docker-compose.yml`

## Atomic env writes (CRIT)
Never `cat >` or `echo >` to a live `.env`. Write-to-temp + validate + `mv`:
```bash
cat > /opt/klai/.env.new << 'EOF'
...
EOF
chmod 600 /opt/klai/.env.new && mv /opt/klai/.env.new /opt/klai/.env
```

## GHCR auth stale deploys
`docker pull` fails silently without `set -e` → old image runs. Store `GHCR_READ_PAT` in SOPS.
Alternative: build on server from public repo (sparse checkout + `docker build`).

## Alembic revision IDs — never hand-typed (CRIT)
Hand-typed placeholder IDs (e.g. `a1b2c3d4e5f6`, `p1r2o3v4s5b1`, `z3a4b5c6d7e8`)
collide with existing migrations. SPEC-KB-020 and SPEC-PROV-001 both got hit
by this: `alembic upgrade head` failed with "Revision X is present more than
once" and multiple-head errors.

**Enforced in CI** via `klai-portal/backend/scripts/validate_alembic.py`,
wired into the `quality` job in `.github/workflows/portal-api.yml`. The script
fails the build if:
- The alembic DAG has more than one head, OR
- Two migration files declare the same `revision = "xxx"` id.

**Local workflow:**
- Always generate via `alembic revision -m "description"` or `--autogenerate`
  — never write a revision id by hand. Alembic uses `uuid.uuid4().hex[:12]`
  which is collision-safe (2^48 space).
- Before setting `down_revision`, confirm actual DB head: `SELECT version_num FROM alembic_version;`
- If in doubt: `docker exec klai-core-portal-api-1 alembic heads` to see what the container sees.
- Run the integrity check locally before pushing: `cd klai-portal/backend && uv run python scripts/validate_alembic.py`

Local `alembic/versions/` may be missing migrations that only exist in production
— local file listing is not authoritative. Always cross-check against the prod
`alembic_version` table.

## Alembic heads after merge
Two branches with migrations → multiple heads → `alembic upgrade head` fails.
Fix: `alembic merge heads -m "merge heads"`. Use `IF NOT EXISTS` in all DDL.
The CI integrity check (see above) catches this before merge to main.

## CI compose-sync overwrites server config (HIGH)

The `deploy-compose.yml` GitHub Actions workflow syncs `deploy/docker-compose.yml` to the server and triggers service recreation. If the repo contains template placeholders (like `RENDER_ME`) or config files without real secrets, it overwrites the working server config.

**Why:** CI treats the repo as source of truth and copies files verbatim. Config files with inline secrets (not env vars) get overwritten with whatever is in git.

**Prevention:** Never put secrets in config files — always use environment variables. For services that don't support env var substitution in their config (like Garage), use a Docker entrypoint that renders the config, or mount a server-local config that CI does not touch. Test by checking `git diff deploy/` before pushing — if config files changed, verify they contain no placeholders.

## Semgrep false positives on OAuth log messages (MED)

Rule `python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure` matches on credential-adjacent keywords in the log *format string* (e.g. "OAuth token", "credentials", "refresh"), regardless of whether any actual secret is logged.

**Why:** The rule is keyword-based, not value-based. Any log message that *describes* an OAuth operation triggers it even when only metadata (status codes, IDs) is logged.

**Prevention:** Add `# nosemgrep: python.lang.security.audit.logging.logger-credential-leak.python-logger-credential-disclosure` on affected log lines. Affected files to watch: `app/api/oauth.py`, `app/adapters/oauth_base.py`, `app/services/portal_client.py`. When adding OAuth-related logging, check for credential-adjacent keywords in the format string and annotate proactively.

## Renovate
Schedule: Monday 05:00 Amsterdam. Automerge: patch (any), minor (devDeps only).
Docker images: grouped manual PR. Trigger: `gh workflow run renovate.yml`.

## Trivy scanning
Every Docker build workflow needs `scan` job after `build-push` with `security-events: write`.

**Vulnerability scanning only — set `scanners: 'vuln'` on the trivy-action.**
Built images contain third-party Python/JS libraries that embed public API
tokens in their source files (e.g. yt-dlp's per-streaming-service extractors
hardcode NBC, Vice, ESPN, Shahid tokens). Trivy's secret scanner classifies
those as CRITICAL `aws-access-key-id` / HIGH `jwt-token` and breaks the scan
job — false positives, every time. Source-level secret scanning is covered
separately by Semgrep (`SAST — Semgrep` workflow) and Gitleaks. If a service
ever needs a CVE allowlist, prefer a `.trivyignore` over disabling the gate.

## No manual server edits (CRIT)
Never edit compose/env on server — repo is source of truth. CI overwrites on next push.

## Secret recovery from containers (CRIT)
After env wipe: DO NOT restart containers. Recover values first:
`docker exec <ctr> printenv VAR_NAME` — values lost after restart.
Non-container vars (KUMA_TOKEN_*, GRAFANA_CADDY_HASH) invisible to this method.

## No architecture change in migration (CRIT)
Migration = same services, different server. NEVER consolidate or redesign during a move.
Source: SPEC-GPU-001 — agent replaced TEI + Infinity with single Infinity (GPU memory leak, no metrics).
