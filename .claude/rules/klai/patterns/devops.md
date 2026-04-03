---
paths:
  - "**/Dockerfile"
  - "**/docker-compose*.yml"
  - ".github/**/*.yml"
  - "**/*.sh"
---
# DevOps Patterns

> Coolify deployments, Docker, service management, CI/CD

## Index
> Keep this index in sync — add a row when adding a pattern below.

| Pattern | When to use | Evidence |
|---|---|---|
| [sops-env-sync](#sops-env-sync) | Updating secrets in `klai-infra/core-01/.env.sops` | `gh run view` shows sync-env passed all guards |
| [docker-compose-sync](#docker-compose-sync) | Adding or removing a service in `docker-compose.yml` | `diff` server vs repo compose file shows match |
| [coolify-env-update](#coolify-env-update) | Adding or changing an env var for a Coolify service | `docker exec <ctr> printenv VAR` returns value |
| [public-01-ssh](#public-01-ssh) | SSH access to public-01 (Coolify, Uptime Kuma) | `ssh -i ~/.ssh/klai_ed25519 root@IP` connects |
| [core-01-ssh](#core-01-ssh) | SSH access to core-01 (AI stack, portal) | `ssh core-01` connects without timeout |
| [coolify-redeploy](#coolify-redeploy) | Triggering a redeploy after a config change | Coolify build log shows "Deployed successfully" |
| [docker-rebuild-no-cache](#docker-rebuild-no-cache) | Force full rebuild after dependency or base image change | `docker images` shows new image timestamp |
| [ghcr-ci-deploy-build-on-server](#ghcr-ci-deploy-build-on-server) | Deploy when GHCR registry auth is stale | `docker ps` shows container with recent CreatedAt |
| [uptime-kuma-add-monitor](#uptime-kuma-add-monitor) | Adding a new service to status monitoring | New monitor appears green on status.getklai.com |
| [umami-access](#umami-access) | Accessing Umami analytics dashboard | `curl -s .../api/heartbeat` returns `{"ok":true}` |
| [trivy-scan-new-workflow](#trivy-scan-new-workflow) | Adding Trivy container scanning to a new Docker build workflow | GitHub Security tab shows SARIF scan results |
| [renovate](#renovate) | How Renovate works, automerge rules, and how to run it manually | `gh run watch --exit-status` exits 0 for Renovate |
| [atomic-env-deploy](#atomic-env-deploy) | Writing `.env` files to a server without data loss risk | `wc -l /opt/klai/.env` matches expected count |
| [local-image-build-from-source](#local-image-build-from-source) | Deploying a private Docker image not on a registry | `docker images repo-name` shows local tag |

---

## sops-env-sync

**When to use:** Updating secrets in `klai-infra/core-01/.env.sops`

Pushing a change to `core-01/.env.sops` on main automatically triggers the
`sync-env.yml` workflow in `klai-infra`. That workflow decrypts the file,
validates it against 8 safety guards, and atomically writes `/opt/klai/.env`
on core-01.

**Safety guards in `sync-env.yml` (hardened March 2026 after production wipe):**

| Guard | What it catches |
|-------|----------------|
| Minimum 10 lines | Decryption failure (empty output) |
| 90% var count threshold | Incomplete SOPS file (would wipe server vars) |
| Critical vars validation | 15 essential vars checked for presence, non-empty, non-placeholder |
| Masked diff output | ADDED/REMOVED/CHANGED keys logged (never values) |
| Key removal block | Removed vars abort on push-trigger; require `workflow_dispatch` |
| Atomic write | `.env.new` + `mv` — no partial files on SSH drop |
| Post-deploy server verification | Same critical var checks on actual server after write |
| Backup rotation | Keep 5 most recent `.env` backups, clean up old ones |

**After a SOPS update lands:** services that need the new value must be restarted
manually (or by their own deploy workflow). Secrets sync does NOT restart containers.

**Manual sync (emergency / new machine / CI unavailable):**
```bash
cd klai-infra && ./core-01/deploy.sh main
```

**Adding a new required field to `config.py`:**
1. Add the real value (never a placeholder) to `core-01/.env.sops` (push → auto-syncs to server)
2. Then push the `config.py` change (portal-api workflow will pre-flight check before deploying)

**SOPS must be the COMPLETE source of truth:**
- Every variable on the server must exist in `.env.sops` with its real production value
- Never add a var to the server without also adding it to SOPS
- Never commit placeholder values (`PLACEHOLDER_VOER_IN`, `CHANGE_ME`, etc.) — generate real values immediately
- Periodically audit: `ssh core-01 "wc -l /opt/klai/.env"` vs `sops -d .env.sops | wc -l`

**Rule:** Never manually edit `/opt/klai/.env` for permanent changes — always go via `.env.sops`.
Manual edits are lost the next time `sync-env.yml` runs.

**See also:** `pitfalls/infrastructure.md#infra-sops-incomplete-wipes-server`, `pitfalls/infrastructure.md#infra-placeholder-values-in-sops`

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

**See also:** `.claude/rules/klai/pitfalls/infrastructure.md#infra-env-not-synced`

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
      cd klai-build && git sparse-checkout set klai-focus/<service>
      docker build -t ghcr.io/getklai/<service>:latest ./klai-focus/<service>
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

## trivy-scan-new-workflow

**When to use:** Adding a Trivy container vulnerability scan to a new Docker build workflow

Every `Build and push *` workflow must include a `scan` job that runs after the image is built and uploads SARIF results to the GitHub Security tab. This is the standard pattern as of March 2026 — all existing workflows already have it.

**Required: add `security-events: write` to the top-level `permissions` block:**
```yaml
permissions:
  contents: read
  packages: write
  security-events: write   # required for SARIF upload
```

**The `scan` job** (add after `build-push`, parallel with `deploy`):
```yaml
  scan:
    needs: build-push
    runs-on: ubuntu-latest
    permissions:
      security-events: write
      packages: read
    steps:
      - name: Log in to GHCR
        uses: docker/login-action@v4
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Run Trivy vulnerability scanner
        uses: aquasecurity/trivy-action@0.35.0
        with:
          image-ref: ghcr.io/getklai/<service-name>:${{ github.sha }}
          format: 'sarif'
          output: 'trivy-results.sarif'
          severity: 'CRITICAL,HIGH'
          ignore-unfixed: true
          exit-code: '1'

      - name: Upload Trivy SARIF to GitHub Security tab
        uses: github/codeql-action/upload-sarif@v3
        if: always()
        with:
          sarif_file: 'trivy-results.sarif'
```

**Key rules:**
- `needs: build-push` — scan runs after the image is pushed, not before
- `exit-code: '1'` — scan job fails on CRITICAL/HIGH CVEs, but `deploy` does NOT need scan (deploy stays `needs: [build-push]` only)
- `ignore-unfixed: true` — skip CVEs with no available fix to avoid noise
- `if: always()` on SARIF upload — uploads results even when the scan finds issues (so they appear in Security tab)
- Use `${{ github.sha }}` tag, not `:latest` — ensures you scan the exact image just built

**For `docs.yml`** (uses env vars for image name):
```yaml
image-ref: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:${{ github.sha }}
```

**Results location:** GitHub → Security → Code scanning alerts (SARIF results per workflow run)

**See also:** `pitfalls/devops.md#devops-ci-green-not-enough`

---

## renovate

**When to use:** Understanding how Renovate works, automerge rules, or triggering a manual run

Renovate replaced Dependabot as the dependency update bot. It runs as a self-hosted GitHub Action using `GITHUB_ADMIN_PAT`.

**Schedule:** Every Monday at 05:00 Amsterdam time. Manual trigger available via Actions → Renovate → Run workflow.

**Trigger manually:**
```bash
gh workflow run renovate.yml --repo GetKlai/klai
gh run watch --exit-status
```

**Automerge rules (defined in `renovate.json`):**

| Update type | Dependency type | Action |
|---|---|---|
| Patch | Any | Auto-merge (squash) |
| Minor | Dev dependencies | Auto-merge (squash) |
| Minor | Prod dependencies | Manual PR |
| Major | Any | Manual PR |
| Any | Docker images | Manual PR (grouped) |

**Docker images:** Renovate groups all Docker image updates (`docker-compose.yml`, Dockerfiles) into a single PR labelled "Docker images". Review and merge manually — these affect production services.

**First run after adding a new `:latest` image to `docker-compose.yml`:** Renovate creates a pin PR to replace `:latest` with an explicit version tag. Review and merge it — this is the supply chain improvement.

**Config files:**
- `renovate.json` — automerge rules and schedule
- `.github/workflows/renovate.yml` — workflow definition

**Why not Dependabot:** Dependabot has limited Docker Compose support and less flexible grouping. Renovate handles all package managers (npm, pip/uv, GitHub Actions, Docker Compose) in one tool with consistent automerge rules.

---

## atomic-env-deploy

**When to use:** Writing an `.env` file to a server (CI workflow, deploy script, or manual operation)

Never use `cat >` or `echo >` to write directly to a live `.env` file. If the SSH connection drops, the process is killed, or the source data is empty, the `.env` file is left truncated or empty — and every service that reads it on next restart is broken.

**Pattern: write-to-temp, then atomic move:**
```bash
# 1. Write to a temporary file (same filesystem as target)
cat > /opt/klai/.env.new << 'ENVEOF'
VAR1=value1
VAR2=value2
ENVEOF

# 2. Set correct permissions before moving
chmod 600 /opt/klai/.env.new

# 3. Atomic move — either fully succeeds or fully fails
mv /opt/klai/.env.new /opt/klai/.env
```

**In a CI workflow (via SSH):**
```yaml
script: |
  set -euo pipefail

  # Decrypt and write to temp file
  echo "$DECRYPTED_ENV" > /opt/klai/.env.new
  chmod 600 /opt/klai/.env.new

  # Validate before moving
  NEW_LINES=$(wc -l < /opt/klai/.env.new)
  OLD_LINES=$(wc -l < /opt/klai/.env)
  if [ "$NEW_LINES" -lt 10 ]; then
    echo "ABORT: new .env has only $NEW_LINES lines (decryption failure?)"
    rm -f /opt/klai/.env.new
    exit 1
  fi

  # Backup current
  cp /opt/klai/.env "/opt/klai/.env.bak.$(date +%s)"

  # Atomic swap
  mv /opt/klai/.env.new /opt/klai/.env
```

**Why `mv` is atomic:**
On the same filesystem, `mv` is a single `rename()` syscall. The file is either the old version or the new version — never a half-written intermediate. `cat >` opens, truncates, then writes incrementally — any interruption leaves a partial file.

**Rule:** Any script or workflow that writes to a production `.env` must use the write-to-temp + validate + `mv` pattern. Direct overwrites (`cat >`, `echo >`, `tee >`) are never acceptable for production secrets files.

**See also:** `pitfalls/infrastructure.md#infra-sync-env-no-safety-checks`

---

## local-image-build-from-source

**When to use:** Deploying a private Docker image that is not published to a registry (e.g., custom fork, private repo).

Build the image locally on the target server from source:

```bash
# On the target server
git clone git@github.com:org/repo.git /opt/builds/repo
cd /opt/builds/repo
git checkout <commit-hash>
docker build -t repo-name:klai .
```

**Key rules:**
1. Always use a local tag namespace (e.g., `vexa-meeting-api:klai`) — never tag as `latest`
2. Pin to a specific commit hash, not a branch name
3. Reference the local tag in `docker-compose.yml`: `image: repo-name:klai`
4. To update: `git pull && git checkout <new-hash> && docker build -t repo-name:klai .`

**Evidence:** `docker images repo-name` shows the local tag with correct creation timestamp.

**Seen in:** SPEC-VEXA-001 — agentic-runtime was built from source on core-01 because it's a private repository without GHCR publishing.

---
