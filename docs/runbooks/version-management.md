# Version Management Playbook

Industry-standard procedures for keeping Klai's dependencies up to date. This playbook covers Docker images, Python packages (uv), Node packages (npm), and Python/Node runtime versions.

The goal: every version running in production is **knowable from git alone** (pinned in `docker-compose.yml` + `uv.lock` + `package-lock.json`), every upgrade is **tested** (CI quality gates + lockfile-frozen installs), and every upgrade is **reversible** (git revert + rolling deploy).

---

## 1. Principles

### 1.1 Pin everything explicitly

No `:latest`, no `^range`-only. The single question "what version is running in production?" must be answerable by reading git. Rationale: `:latest` silently rolls forward on `docker compose pull`, and unconstrained version ranges silently roll forward on `npm install` — neither is reproducible.

**Two honest exceptions, enforced by policy:**

- **Internal CI-deployed images** (`ghcr.io/getklai/*`): use `:latest` because our own GitHub Actions rebuild on every commit. Each build is *also* tagged `:${github.sha}` so rollback = `docker pull <image>:<sha> && docker compose up -d`.
- **Locally-built images** (`vexa-*:klai`, `firecrawl:latest`): pinned to the git SHA of the source repo, recorded in `docker-compose.yml` comments at build time.

**Lockfiles are mandatory:**

- Python: `uv.lock` is the source of truth. `Dockerfile` uses `uv sync --frozen` to install from the lockfile, not `pip install .` which re-resolves transitive deps.
- Node: `package-lock.json` is checked in. CI uses `npm ci` (fails on lockfile drift), never `npm install`.

### 1.2 Separate repo version from running version

A bumped `docker-compose.yml` does not change the running container. `docker compose pull && up -d <service>` must run on the server. The `deploy/` workflow auto-syncs `docker-compose.yml` to `/opt/klai/docker-compose.yml` — but the pull + restart are deliberate human actions.

### 1.3 Lockfile == CI == prod

The lockfile resolved locally must be byte-identical to CI's install must be byte-identical to the Docker image's install. Any drift is a reproducibility bug. Enforced via `uv sync --frozen` and `npm ci`.

### 1.4 Test gate before server

An upgrade that breaks CI never reaches the server:

- Python: `ruff check` + `ruff format --check` + `pyright` + `pip-audit` + `pytest`
- Node: `eslint` + `npm audit --audit-level=high` + `npm run build` + `vitest`
- Docker: Trivy image scan (CRITICAL/HIGH, unfixed excluded) + container health-check startup

Any red check blocks merge. The CI has the power to fail the upgrade.

### 1.5 Rollback is a property, not an afterthought

Every upgrade must be revertable by `git revert <commit>` alone. That requires:

- Dep changes and data migrations in separate commits
- No destructive operations (volume deletes, DB column drops) in the same commit as a version bump
- Container images always retain the previous `:<sha>` tag in GHCR so rollback works even without rebuilding

---

## 2. Cadence

| Layer | Cadence | Trigger mechanism |
|---|---|---|
| Patch (Python/Node `0.0.X`) | Weekly | Renovate/Dependabot auto-PR, auto-merge if CI green |
| Minor (Python/Node `0.X.0`) | Bi-weekly | Renovate/Dependabot auto-PR, human review |
| Major (Python/Node `X.0.0`) | On demand | Human-driven: planning + test branch + staged rollout |
| Docker image minor | Monthly | `docker compose pull` maintenance window on core-01 |
| Docker image major | On demand | Human-driven: read release notes, volume migration plan |
| Python runtime (3.13 → 3.14) | Yearly | Schedule after 3.X.1 (skip .0) release |
| Full dependency audit | Quarterly | Manual audit, writes to `reports/dependency-audit-YYYY-MM-DD.md` |

Out-of-cadence triggers: any CVE with CVSS ≥ 7.0 on a direct dependency triggers an immediate upgrade (or explicit `pip-audit --ignore-vuln` with rationale).

---

## 3. Procedures

### 3.1 Python package upgrade (klai-portal/backend, other services)

**For a single package bump (no floor change):**

```bash
cd klai-portal/backend
uv lock --upgrade-package fastapi  # resolve fastapi to newest allowed by pyproject
uv sync --group dev                 # install locally
uv run pytest -q                    # verify tests
git add uv.lock
git commit -m "chore(deps): bump fastapi to X.Y.Z"
```

**For a broad upgrade (bump all within existing constraints):**

```bash
uv lock --upgrade                   # upgrade everything to latest allowed
uv sync --group dev
uv run ruff check . && uv run --with pyright pyright && uv run pytest -q
git add uv.lock && git commit -m "chore(deps): bump all Python deps"
```

**For a major bump (raising the floor in `pyproject.toml`):**

1. Edit `pyproject.toml` — raise the floor (e.g. `"redis[hiredis]>=7.4"`).
2. `uv lock --upgrade` regenerates with the new floor.
3. Read the package's CHANGELOG between the old and new major for breaking changes.
4. Run full test suite locally.
5. **Critical**: grep the codebase for the changed API — a major bump usually changes function signatures. Lockfile + pyright will catch type mismatches; ruff+tests catch behavior changes.
6. Commit, push to a branch, let CI validate.
7. On merge, CI deploys `portal-api` container with the new deps baked in.

### 3.2 Node package upgrade (klai-portal/frontend)

**Single package:**

```bash
cd klai-portal/frontend
npm install <pkg>@<version>         # also edits package.json range
git add package.json package-lock.json
```

**Broad minor/patch bump:**

```bash
npm update                          # respects caret ranges in package.json
npm run lint && npm run build && npm run test
```

**Major bump:**

```bash
npm install <pkg>@<major>           # explicit major, edits caret
npm run lint && npm run build && npm run test
# If compile errors from new types, fix them in the same commit
```

**When to use `overrides` in `package.json`:** only when a peer dep mismatch exists upstream (e.g., BlockNote 0.48 ships `@tiptap/core@3.20` but `@blocknote/react` imports from `@tiptap/core@3.22`). Document the override in the commit message — it is technical debt that should resolve when the upstream is fixed.

**When to pin to RC/pre-release:** never in production without explicit rationale in `VERSIONS.md`. If pinned to `v0.8.5-rc1`, include a "stable goal" note referencing the stable version we intend to migrate to.

### 3.3 Docker image minor/patch upgrade

For any external service (non-`ghcr.io/getklai/*`):

1. **Research**: read the release notes between current pinned version and target. Data-migration-on-startup? Config file syntax change? Image labels show new required env vars?
2. **Repo**: edit `deploy/docker-compose.yml` with the new explicit version tag.
3. **VERSIONS.md**: update the row with the new version; re-check rationale is still accurate.
4. **Commit + push**: `deploy-compose.yml` workflow auto-syncs the compose file to `/opt/klai/docker-compose.yml`.
5. **Pull + restart on server**:
   ```bash
   ssh core-01 "cd /opt/klai && docker compose pull <service> && docker compose up -d <service>"
   ```
6. **Verify**:
   ```bash
   ssh core-01 "docker ps --filter name=<service> --format '{{.Names}}\t{{.Status}}\t{{.CreatedAt}}'"
   ssh core-01 "docker logs --tail 30 <container>"
   ```
   Container must be `Up X seconds (healthy)`. Logs must not show errors.

### 3.4 Docker image major upgrade

Same as minor + **always** these extra steps:

1. **Back up data volume before starting**:
   ```bash
   ssh core-01 "docker run --rm -v <volume>:/data -v /opt/klai/backups:/backup alpine tar czf /backup/<service>-$(date +%F).tar.gz /data"
   ```
2. **Check for data migration**: release notes frequently mandate a migration step (`meilisearch` v1.40 → v1.42 is a concrete example — the binary refused to start against the old DB).
3. **Plan rollback**: know the exact `git revert <sha>` command + how to restore the volume from backup.
4. **Execute in low-traffic window**: prod bumps for stateful services happen outside business hours.
5. **Update `VERSIONS.md` rationale**: a major bump often changes the upgrade path for future bumps.

### 3.5 Python runtime upgrade (e.g. 3.13 → 3.14)

A runtime bump touches five places. All five must change in one PR:

1. `klai-portal/backend/pyproject.toml` — `requires-python = ">=3.14"`
2. `klai-portal/backend/pyproject.toml` — `[tool.ruff] target-version = "py314"`
3. `klai-portal/backend/Dockerfile` — `FROM python:3.14-slim` (both stages)
4. `.github/workflows/portal-api.yml` — `python-version: "3.14"`
5. `klai-portal/backend/uv.lock` — regenerate with `uv lock --upgrade`

Plus: grep for any `version_info` / `sys.version` hardcoded checks and patch them. Plus: test that any `typing` / `collections.abc` usage still resolves on the new runtime.

### 3.6 Node runtime upgrade

Similar but simpler — edit `.github/workflows/portal-frontend.yml` (`node-version:`) and `package.json` (`engines.node` if set). `package-lock.json` regenerates on `npm install`.

---

## 4. Testing gates

Every upgrade PR must pass:

| Gate | Tool | Fail condition |
|---|---|---|
| Python lint | `ruff check` | Any error |
| Python format | `ruff format --check` | Any diff |
| Python types | `pyright` | Any error |
| Python security | `pip-audit` | Any unignored CVE |
| Python tests | `pytest` | Any failure or skipped-without-reason |
| Node lint | `eslint` | Any error |
| Node security | `npm audit --audit-level=high` | Any HIGH/CRITICAL |
| Node build | `vite build` | Any error |
| Node tests | `vitest run` | Any failure |
| Docker scan | Trivy | CRITICAL/HIGH with fix available |
| Runtime smoke | container health-check | Container not `healthy` after start_period |

Two additional gates for major bumps:

| Gate | When | Method |
|---|---|---|
| API compatibility | Python/Node major deps | Search the codebase for the changed API, patch + type-check |
| Data migration | Stateful container major | Back up volume, run migration, verify data integrity post-start |

---

## 5. Pinning rules cheat sheet

| Layer | Rule | Example |
|---|---|---|
| External Docker image | Explicit version tag | `redis:8-alpine` ✓ `redis:latest` ✗ |
| Internal CI-deployed image | `:latest` OK (CI also pushes `:sha`) | `ghcr.io/getklai/portal-api:latest` ✓ |
| Locally-built image | `:klai` or similar + source SHA in comment | `vexa-meeting-api:klai` (comment: `built from feature/agentic-runtime @ 600cba04`) |
| Python dep in pyproject | Floor with upper bound for known-breaking | `fastapi>=0.136` ✓; `graphiti-core>=0.28,<0.30` ✓ |
| Python lockfile | Pinned exact versions + hashes | `uv.lock` ✓ (never commit `requirements.txt` hand-written) |
| Node dep in package.json | Caret range (`^0.48.1`) + lockfile is truth | `"react": "^19.2.5"` + `package-lock.json` ✓ |
| Node lockfile | Regenerated on every install | `package-lock.json` checked in, `npm ci` in CI |

---

## 6. Rollback

### Fast rollback — failed CI after merge (image not yet deployed)

```bash
git revert <merge-sha>
git push origin main
```

CI rebuilds with reverted deps. Server still runs old image until next deploy.

### Fast rollback — failed deploy on server (old image still in registry)

**Internal service** (our CI-built image):

```bash
# Find the previous SHA from GHCR
gh api repos/GetKlai/klai/actions/runs --jq '.workflow_runs[] | select(.name=="Build and push portal-api") | .head_sha' | head -5

# Pin to that SHA on the server
ssh core-01 "docker pull ghcr.io/getklai/portal-api:<prev-sha> && \
  docker tag ghcr.io/getklai/portal-api:<prev-sha> ghcr.io/getklai/portal-api:latest && \
  docker compose up -d portal-api"
```

**External service** (public image):

```bash
# Edit docker-compose.yml back to previous version, push, then on server:
ssh core-01 "cd /opt/klai && docker compose pull <service> && docker compose up -d <service>"
```

### Rollback with data corruption

If a major upgrade corrupted a data volume (Meilisearch schema break, Postgres major mismatch):

1. Stop the service: `docker compose stop <service>`
2. Restore volume from backup: `docker run --rm -v <volume>:/data -v /opt/klai/backups:/backup alpine tar xzf /backup/<service>-YYYY-MM-DD.tar.gz -C /`
3. `git revert` the compose version bump.
4. `docker compose pull && docker compose up -d <service>`
5. Verify: logs clean, data consistency check against expected rows/keys.

---

## 7. Common pitfalls (from our history)

These are real failures from the codebase; avoid repeating them.

### 7.1 `sys.UnraisableHookArgs` removed in Python 3.13

Python 3.13 removed `sys.UnraisableHookArgs` from the runtime attribute surface. If you're upgrading the runtime, grep for this specific symbol — we had it in `conftest.py` and it crashed every pytest run on 3.13 until replaced with a `typing.Any` annotation.

**Lesson:** major runtime bumps can break tooling code (conftest, dev scripts). Run the full test suite, not just the app.

### 7.2 uv shebangs are absolute

`uv sync` writes absolute shebangs into console scripts (`#!/app/.venv/bin/python3.13`). The builder stage and runtime stage in a multi-stage Dockerfile **must** use the same `WORKDIR` for the venv path, or every script fails with `exec: no such file or directory`.

**Lesson:** when a container crash-loops with "no such file or directory" on an executable that exists, it's a broken shebang interpreter path.

### 7.3 Pydantic 2.13 strictened missing-field validation

A model refactor added `last_sync_documents_ok: int | None` without a default. Tests that passed on Pydantic 2.12 (lax validation) failed on 2.13 (strict). The same refactor also made `MagicMock`-wrapped async calls explicitly unsafe to await.

**Lesson:** major Pydantic bumps expose real bugs that were hidden. Test coverage is essential before bumping — drift between production code and test mocks becomes visible only under strict validation.

### 7.4 BlockNote 0.48.1 transitive `@tiptap/core` mismatch

`@blocknote/core@0.48.1` pins `@tiptap/core@3.20`, but `@blocknote/react@0.48.1` pulls `@tiptap/react@3.22` which expects `@tiptap/core@3.22`. Build fails with "Missing export: cancelPositionCheck". Upstream bug.

**Lesson:** when a transitive dep mismatch blocks a build, either (a) pin to a version before the mismatch (we rolled back to 0.47.1) or (b) use `npm overrides` to force a single transitive version. Always document why.

### 7.5 Meilisearch requires data migration on minor bumps

Meilisearch v1.40 → v1.42 data format is incompatible. `:latest` silently rolled from 1.40 to 1.42 on pull, then the container crash-looped with "Your database version (1.40.0) is incompatible with your current engine version (1.42.1)."

**Lesson:** assume every stateful container's minor bump requires a migration check. `VERSIONS.md` flags these explicitly. In dev we wiped the volume; in prod we follow [Meilisearch's dump/migrate/restore procedure](https://www.meilisearch.com/docs/learn/update_and_migration/updating).

### 7.6 CI didn't actually run pytest

For months, `portal-api.yml` CI ran ruff + pyright + pip-audit but not pytest. 13 tests silently failed on `main` with drifted mocks. A pytest step in CI is not optional — it's the primary behaviour gate.

**Lesson:** audit CI gates against what they promise to run. If the repo README says "tests run in CI", verify it.

### 7.7 `:latest` on the server is not the same as `:latest` in the repo

Before this playbook, the repo had `image: mongo:latest` for weeks. Server was running mongo pulled 5 days prior. "We're on latest" was false. Explicit pins force both repo and server to state exactly what's running.

**Lesson:** `:latest` makes the question "what's running?" un-answerable without SSH. Explicit pins make it answerable from git.

### 7.8 Zitadel PAT invalidation on upgrade

Zitadel minor bumps sometimes invalidate the portal-api PAT (`Errors.Token.Invalid`). Factor this into every Zitadel bump:

- Have a fresh PAT rotation window scheduled post-deploy
- See `.claude/rules/klai/platform/zitadel.md` and `runbooks/platform-recovery.md#zitadel-pat-rotation`

### 7.9 LibreChat patches are mount-point-specific

Three CJS files are mounted into LibreChat at `/app/node_modules/@librechat/agents/dist/cjs/...`. The internal path can change between LibreChat versions. Every LibreChat upgrade requires opening the new image and verifying the mount paths still map to real files.

**Lesson:** when you mount patches into someone else's container, assume their internal paths are unstable between versions.

---

## 8. Tools reference

### 8.1 Python — uv

```bash
uv lock                        # regenerate lockfile from pyproject.toml
uv lock --upgrade              # bump all deps to latest within ranges
uv lock --upgrade-package <p>  # bump single package
uv sync --group dev            # install from lockfile (includes dev deps)
uv sync --frozen               # fail if pyproject and lock disagree (CI mode)
uv tree --depth 1              # show top-level resolved versions
uv run pytest                  # run in the managed venv
uv run --with pip-audit pip-audit  # one-shot tool without adding to project
```

### 8.2 Node — npm

```bash
npm install                    # install from lockfile, update if needed
npm ci                         # install exact lockfile, fail on drift (CI mode)
npm update                     # bump within caret ranges, update lockfile
npm install <pkg>@<ver>        # explicit version, updates package.json + lock
npm list <pkg>                 # show installed version tree
npm audit                      # CVE scan
npm outdated                   # show all outdated packages
```

### 8.3 Docker

```bash
# Pull all images defined in compose
docker compose pull

# Pull a specific service's image
docker compose pull <service>

# Restart with new image
docker compose up -d <service>

# Show version from image label
docker inspect <image> --format '{{index .Config.Labels "org.opencontainers.image.version"}}'

# Check health after restart
docker ps --filter name=<service> --format '{{.Names}}\t{{.Status}}'
docker logs --tail 30 <container>

# Back up volume before major upgrade
docker run --rm -v <volume>:/data -v /opt/klai/backups:/backup alpine \
  tar czf /backup/<service>-$(date +%F).tar.gz /data
```

### 8.4 Registry queries (to find latest available)

```bash
# Docker Hub
curl -s 'https://hub.docker.com/v2/repositories/<namespace>/<image>/tags/?page_size=20&ordering=last_updated'

# GHCR (via gh)
gh api /orgs/<org>/packages/container/<image>/versions --jq '.[0:10].[].metadata.container.tags'

# PyPI
curl -s https://pypi.org/pypi/<package>/json | jq -r '.info.version'

# npm
curl -s https://registry.npmjs.org/<package>/latest | jq -r '.version'
```

### 8.5 CI automation

- **Renovate** (`renovate.json` + `.github/workflows/renovate.yml`): scheduled Monday 05:00 Amsterdam. Auto-merges patches and dev-only minors. Docker image groups get a grouped manual PR. Single source of truth for version-update PRs across all managers (pip/pep621, npm, docker-compose, Dockerfile, github-actions).
- **Dependabot security updates** (GitHub repo feature, not `dependabot.yml`): raises PRs automatically when a CVE is found in a dep. Independent of Renovate. Enabled via `gh api -X PUT repos/GetKlai/klai/automated-security-fixes`.
- **Trivy per service** (each `<service>.yml` workflow has a `scan` job): runs after every internal image build; fails CI on CRITICAL/HIGH with a fix available.
- **Trivy per pinned external image** (`.github/workflows/scan-pinned-images.yml`): weekly scheduled scan of every external image pinned in our compose files. Non-blocking — findings go to the Security tab.
- **pip-audit / npm audit**: part of every service's quality job. Fails CI on unignored vulnerabilities.
- **Semgrep** (`.github/workflows/semgrep.yml`): SAST on every push + PR.
- **Secret scanning + push protection** (GitHub repo feature): blocks commits containing detected secrets from being pushed to GitHub.

> **Not used**: `.github/dependabot.yml` (version-update PRs). Renovate supersedes it — running both creates duplicate conflicting PRs.

---

## 9. CVE detection layers

Five independent mechanisms detect vulnerabilities. The goal is defence in depth: if one misses, another catches. None of them is the single source of truth.

| Layer | Covers | When it runs | Where alerts land |
|---|---|---|---|
| `pip-audit` in CI | Python deps in `uv.lock` | Every push + PR to `main` per service | PR status check (blocks merge) |
| `npm audit` in CI | Node deps in `package-lock.json` | Every push + PR to `main` per frontend | PR status check (blocks merge) |
| Trivy on internal image build | OS layer + installed packages in our `ghcr.io/getklai/*` images | Every internal image build | Security tab → Code scanning alerts |
| Trivy on external pinned images | OS layer + installed packages in `mongo:8.2.7`, `redis:8-alpine`, etc. | Weekly (`scan-pinned-images.yml`) + on compose change | Security tab → Code scanning alerts |
| Dependabot security updates | Python + Node deps across the whole repo | Real-time (GitHub's vulnerability DB) | Auto-PR + Security tab → Dependabot alerts |
| Secret scanning + push protection | Accidentally committed API keys, tokens, credentials | On every push | Security tab → Secret scanning alerts + push block |

### Response procedure

When an alert fires:

1. **Triage**: is it actually exploitable in our context? Many CVEs have a high CVSS but no reachable code path in our deployment (e.g., a feature we don't use).
2. **Fix available?** → bump the dep (follow §3.1 for Python, §3.2 for Node, §3.3 for images).
3. **No fix available?** → document the acceptance in `VERSIONS.md` with rationale + re-assess date. Add to `pip-audit --ignore-vuln` list if needed. Portal-api already ignores `CVE-2026-4539` and `CVE-2025-71176` with a "re-assess Q3 2026" note.
4. **Actively exploited?** → bump immediately, skip the Renovate cadence (§10.1).

---

## 10. Audit procedure

Quarterly, run a full audit:

1. Check `VERSIONS.md` for drift:
   ```bash
   ssh core-01 "docker ps --format '{{.Names}}\t{{.Image}}' | sort"
   ```
   Every row must match a pin in `VERSIONS.md`. Differences indicate either unpulled images on the server or stale pins in the file.

2. Scan for latest stable versions of every pinned image:
   ```bash
   # Scripted via reports/dependency-audit-*.md — see historical examples
   ```

3. Write a report to `reports/dependency-audit-YYYY-MM-DD.md` with:
   - Pin vs upstream-latest per service
   - Pin vs upstream-latest per direct Python/Node package
   - CVE status for each (pip-audit + npm audit + Trivy)
   - Recommended upgrade order (risk ranking)

4. File an issue for each "should bump" item with the rollback plan.

---

## 11. When this playbook is wrong

This playbook describes a stable policy, not an algorithm. Use judgment:

- **§10.1 — Actively exploited CVE**: skip the cadence, bump immediately. Security tab + Dependabot alert trump the weekly Renovate schedule.
- **EOL upstream**: don't wait for the quarterly audit.
- **Major bump with broken API surface** that can't land in one PR: split into three: floor bump now, call-site migration later, upper bound removal last.
- **`:latest` temptation**: write the rationale in `VERSIONS.md` first. If you can't articulate why it's an exception, it's not an exception.
- **Trivy complaining about a CVE with no upstream fix**: add to the service's `pip-audit --ignore-vuln <id>` or Trivy's `.trivyignore` with a re-assess date. Never silence globally.
- **Secret scanning false positive**: use `# gitleaks:allow` or the equivalent and add a comment. Never disable push protection for the whole repo.

---

*Playbook version: 1.1 (2026-04-19) — added §9 CVE detection layers + §8.5 inventory after enabling Dependabot security updates, secret scanning, and the weekly external image scan.*
*Source incidents: dependency-audit-2026-04-19, pin-all-images-2026-04-19*
