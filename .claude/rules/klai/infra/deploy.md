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

## Bind-mount config sync — required pattern (HIGH)

> Closes the bind-mount-without-sync-workflow class of bugs that the
> Caddyfile incident (2026-05-07, SPEC-INFRA-CADDY-CONFIG-DEPLOY-001)
> exposed. Codified by SPEC-INFRA-CONFIG-SYNC-001.

### The class

When `deploy/docker-compose.yml` declares a relative bind-mount of
the shape `- ./<svc>/<file>:/etc/...`, the host source resolves to
`/opt/klai/<svc>/<file>`. Without a workflow that syncs the file
from the repo to that host path, edits to the repo never reach the
running container. The bind-mount silently uses whatever was scp'd
manually long ago — sometimes drifting for months without anyone
noticing.

The Caddyfile incident was a concrete instance: image rebuilds via
`caddy.yml` recreated the container correctly, but the bind-mount
source on `/opt/klai/caddy/Caddyfile` had not been touched since
the last manual `scp`. A new Caddyfile change merged to main was
invisible to production for an unknown number of days until someone
asked "why does this directive not work?".

### The fix

`.github/workflows/deploy-compose.yml` ships a bash helper
`sync_and_recreate <compose-service> <repo-src> <host-dst>` that:

1. Adds the source path to its `paths:` trigger and sparse-checkout
2. Rsyncs with `-ac --itemize-changes` (content-checksum, ignores
   mtime churn from a fresh git clone)
3. On content change: `docker compose ... up -d --force-recreate
   <service>` + 5×2s health check loop using `docker inspect
   --format '{{.State.Status}}'`. Workflow fails on timeout.
4. On no change: idempotent skip (no recreate, no log noise)

### Required when adding a new bind-mount (3-step checklist)

When you add a new line of the form `- ./<svc>/<file>:/...` to
`deploy/docker-compose.yml`, you MUST in the same PR:

1. Add `'deploy/<svc>/<file>'` to `deploy-compose.yml`'s `paths:`
   trigger
2. Add the same path to the `git sparse-checkout set` invocation in
   the workflow's script
3. Add a `sync_and_recreate <compose-service> deploy/<svc>/<file>
   /opt/klai/<svc>/<file>` call alongside the existing four

If the bind-mount is a directory (not a single file), use a
directory-rsync block in the style of the existing grafana
provisioning sync — the helper is single-file only.

### Inventory (as of SPEC-INFRA-CONFIG-SYNC-001)

#### Class A — synced via `sync_and_recreate` helper

- `deploy/caddy/Caddyfile` → compose service `caddy`
- `deploy/alloy/config.alloy` → compose service `alloy`
- `deploy/searxng/settings.yml` → compose service `searxng`
- `deploy/vexa/profiles.yaml` → compose service `runtime-api`
  (note the asymmetry: NOT a service named "vexa" — the file is
  consumed by runtime-api)

#### Class A-dir — synced via directory rsync (predates helper)

- `deploy/grafana/provisioning/` → compose service `grafana`
  (SPEC-OBS-001 Phase C, kept inline because helper is single-file
  only; refactor only if a second dir bind-mount appears)

#### Class B — own dedicated workflow

- `deploy/litellm/*.{py,yaml}` → `litellm-hook-deploy.yml`
- `deploy/librechat/...` → `deploy-librechat-config.yml`
- Tenant Caddyfiles (`caddy/tenants/*.caddyfile`) → portal-api
  `_write_tenant_caddyfile` runtime (NOT CI — per-tenant, dynamic)

#### Class C — one-shot init, no drift risk

- `deploy/postgres/init.sql` — read once on DB volume init
- `deploy/firecrawl-nuq-init.sql` — read once on DB volume init

### When you change a Class A file

Just `git push`. The workflow rsyncs + force-recreates. ~30s end-
to-end. Health check fails the workflow on container-not-running;
operator's recovery is `git revert + push`.

### When you change a Class A-dir file

Same — directory rsync handles it. ~30s end to end.

### When you change a Class B file

Use the dedicated workflow — see the workflow's own paths-trigger
and follow that contract.

### When you change a Class C file

Don't bother editing the existing file directly — it only affects
fresh DB-volume bootstraps. For existing prod, write a migration.

### Adding a new service with a bind-mount config?

Default to Class A. Class B is justified only when the service has
a non-trivial reload mechanism that recreate cannot replace, or
when its deploy cadence differs strongly from the rest of compose.
Class C is rare — only for true one-shot init that cannot be a
migration.

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

## Trivy scanning (CRIT)

Defined and enforced by SPEC-CI-TRIVY-POLICY-001. Three-tier model based on
who can fix the finding:

| Tier | Workflows | Behaviour |
|---|---|---|
| **STRICT** | 10 internal `ghcr.io/getklai/*` builds (portal-api, caddy, whisper-server, knowledge-ingest, klai-knowledge-mcp, retrieval-api, klai-connector, klai-mailer, docs, scribe-api) | `--exit-code 1` blocks the build on any unfixed HIGH/CRITICAL not listed in the service's `.trivyignore.yaml`. |
| **WARN** | `scan-pinned-images.yml` (external compose pins) | `--exit-code 0` — SARIF only. We can't fix upstream, Renovate handles upgrade pressure on Monday-morning cadence. |
| **SKIP** | Locally-built tags (`*-local-YYMMDD-HHMM` per `infra/container-hygiene.md`) | Excluded from the `scan-pinned-images.yml` enumerate matrix — they don't exist on any registry so a manifest-fetch always 404s. |

**Severity policy lives in `.trivy.yaml` at repo root** as the canonical reference. trivy-action 0.35.0/0.36.0 has a known limitation that keeps it from being the only source for `severity` / `ignore-unfixed` / `scanners` — those flags must also appear inline as CLI args. See "trivy-action 0.35.0 SARIF/severity-filter bug" below.

**Vulnerability-scanner only.** Built images contain third-party Python/JS libraries that embed public API tokens (e.g. yt-dlp's per-streaming-service extractors hardcode NBC, Vice, ESPN, Shahid tokens). Trivy's secret scanner classifies those as CRITICAL `aws-access-key-id` / HIGH `jwt-token` — false positives, every time. Source-level secret scanning is covered separately by Semgrep (`SAST — Semgrep` workflow) and Gitleaks.

**Documented exemptions live in per-service `.trivyignore.yaml`.** Each entry MUST carry `id`, ≥40-char `statement` (rationale, no boilerplate like "low priority"), and `expired_at` (YYYY-MM-DD within 12 months). Mechanically enforced at commit time via `.githooks/pre-commit` and at PR time via `.github/workflows/validate-trivyignore.yml`. Implementation: `scripts/validate-trivyignore.sh` + `scripts/_validate_trivyignore.py`.

**Recipes** — adding an exemption, rotating expired entries, running the smoke-test, reading the Security tab: see `docs/runbooks/trivy-policy.md`.

### Required scan-job pattern (CRIT)

Every internal-image scan job MUST follow this skeleton:

```yaml
scan:
  needs: build-push
  permissions:
    security-events: write
    packages: read
  steps:
    - uses: actions/checkout@v6     # REQ — see "scan-job checkout" below
    - uses: docker/login-action@v4  # for ghcr.io pull
      with:
        registry: ghcr.io
        username: ${{ github.actor }}
        password: ${{ secrets.GITHUB_TOKEN }}
    - uses: aquasecurity/setup-trivy@e6c2c5e321ed9123bda567646e2f96565e34abe1  # v0.2.5
      with:
        version: v0.69.3
        cache: true
    - run: |
        trivy image \
          --format sarif \
          --output trivy-results.sarif \
          --severity CRITICAL,HIGH \
          --ignore-unfixed \
          --scanners vuln \
          --exit-code 1 \
          --ignorefile <service>/.trivyignore.yaml \   # only if file exists
          ghcr.io/getklai/<svc>:${{ github.sha }}
    - uses: github/codeql-action/upload-sarif@v4
      if: always()
      with:
        sarif_file: trivy-results.sarif
```

For workflows with a `deploy` job: `deploy.needs` MUST be `[build-push, scan]` so a failed scan blocks the deploy step. Without that, the gate fails CI status but still rolls out the image.

### trivy-action 0.35.0 SARIF/severity-filter bug (HIGH)

trivy-action 0.35.0 (and 0.36.0) honours the `severity` input for the SARIF body but NOT for the exit-code when `format: sarif`. A MEDIUM/LOW finding triggers `exit-code 1` even when the workflow asks for CRITICAL+HIGH gating. `limit-severities-for-sarif: true` filters the SARIF body, not the exit-code path.

**Why:** the wrapper's entrypoint shell-script unsets `TRIVY_SEVERITY` before calling Trivy CLI under specific format conditions, then post-processes the SARIF instead of letting Trivy filter natively.

**Workaround:** klai now invokes Trivy CLI directly via `aquasecurity/setup-trivy@v0.2.5` + `run: trivy image --severity CRITICAL,HIGH ...`. CLI honours `--severity` for both detection AND exit-code. Drop the workaround when trivy-action lands proper precedence (track upstream).

**See:** `docs/retros/2026-05-06-trivy-spec-iteration.md` § Lesson 1.

### Scan-job checkout requirement (CRIT)

`trivy-config:` and `trivyignores:` paths resolve from `$GITHUB_WORKSPACE`. Without `actions/checkout@v6` as the first scan-job step, those paths point at an empty workspace and Trivy logs `cannot find ignorefile` (and silently uses defaults for trivy-config). Pre-PR #3 of SPEC-CI-TRIVY-POLICY-001, none of klai's 11 scan jobs had checkout — for ~9 months the documented `severity` filter appeared not to work because the config-file simply wasn't on disk.

### Trivy DB lag false-positives (MED)

Trivy's vulnerability database sometimes lags upstream advisory databases (npm, PyPI, Debian security tracker). A package can be at the upstream-confirmed fix-version and still flagged by Trivy until the next DB refresh. Pattern observed for `picomatch 4.0.4` (NVD says 4.0.4 IS the fix for CVE-2026-33671 — Trivy still flags it). When you bump a dep to the announced fix-version and Trivy still complains, cross-check NVD before assuming the bump didn't take.

### Diagnose-first when a gate behaves unexpectedly (HIGH)

When a scan job fails and the SARIF / Code-Scanning Alerts API don't show enough findings to explain the exit-code, add a temporary `--format table` step BEFORE the gate-step. `--format sarif` writes to a file with no stdout summary; `--format table` prints `Total: N (HIGH: X, CRITICAL: Y)` plus a per-package breakdown. This is the difference between "blind fix-and-pray" and "concrete debugging".

```yaml
- name: Diagnose — table output (no gate)
  run: |
    trivy image --format table --severity CRITICAL,HIGH --ignore-unfixed --scanners vuln --exit-code 0 <image>
```

### Adding a new internal-image workflow

Copy the scan-job skeleton above. Default `--ignorefile` line is omitted unless the service has a `.trivyignore.yaml`. Verify on first run that the scan job appears in `gh run list --workflow <new>.yml` results — workflows without `pull_request:` triggers will only run after merge to main.

## No manual server edits (CRIT)
Never edit compose/env on server — repo is source of truth. CI overwrites on next push.

## Secret recovery from containers (CRIT)
After env wipe: DO NOT restart containers. Recover values first:
`docker exec <ctr> printenv VAR_NAME` — values lost after restart.
Non-container vars (KUMA_TOKEN_*, GRAFANA_CADDY_HASH) invisible to this method.

## No architecture change in migration (CRIT)
Migration = same services, different server. NEVER consolidate or redesign during a move.
Source: SPEC-GPU-001 — agent replaced TEI + Infinity with single Infinity (GPU memory leak, no metrics).
