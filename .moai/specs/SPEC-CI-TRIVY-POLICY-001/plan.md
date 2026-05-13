# Implementation Plan — SPEC-CI-TRIVY-POLICY-001

## Strategy

Three sequential PRs against `main`. Each independently revert-safe.

PR #1 lays the centralised plumbing without flipping any gate. PR #2 resolves the existing 10 HIGH/CRITICAL findings across 5 services. PR #3 flips the gate atomically across all 10 internal-image workflows. Optional PR #4 finishes scan-pinned-images filter + docs + smoke-test.

Worktree: `/Users/mvletter/Developer/klai-trivy-policy/` on branch `feature/SPEC-CI-TRIVY-POLICY-001` from `origin/main`.

## PR #1 — Centralised config (no behaviour change)

### Tasks

1. Create `.trivy.yaml` at repo root:
   ```yaml
   # .trivy.yaml — single source of truth for klai's CVE policy
   # Referenced by all CI Trivy invocations via `trivy-config: .trivy.yaml`
   # Schema: https://trivy.dev/v0.69/docs/references/configuration/config-file/
   severity:
     - CRITICAL
     - HIGH
   vulnerability:
     ignore-unfixed: true
   scan:
     scanners:
       - vuln
   ```
2. Modify all 10 internal-image workflows: add `trivy-config: .trivy.yaml`, remove inline `severity:` / `ignore-unfixed:` / `scanners:` keys.
3. Modify `scan-pinned-images.yml`: add `trivy-config: .trivy.yaml`, remove inline `severity:` / `ignore-unfixed:`. Keep `exit-code: '0'`.
4. Verify each workflow's next CI run is green with same outcomes as before (still warn-only).

### Quality gate

- `gh run list --workflow <each>.yml --limit 3` per workflow shows green
- portal-api remains red on its scan job (unchanged from current state) — to be fixed by PR #2

### Files touched

11 files: `.trivy.yaml` (new), 10 workflow modifications.

### Estimated diff size

~40 lines added (`.trivy.yaml` + 4 lines modified per workflow × 11).

## PR #2 — Per-service findings dispositie

### Tasks per service

#### portal-api
- Create `klai-portal/backend/.trivyignore.yaml`:
  ```yaml
  vulnerabilities:
    - id: CVE-2026-6357
      statement: |
        Affects pip 26.0.1 — the CI runner image's bootstrap pip, NOT an app
        dependency resolved by uv at runtime. uv handles all package resolution
        post-build; the container never invokes pip after the build completes.
        Re-evaluate when actions/runner-images ships a default pip ≥26.1.
      expired_at: 2026-09-01
  ```

#### klai-connector
- Bump `lxml` in `klai-connector/pyproject.toml` to a version that fixes CVE-2026-41066. Lookup latest patched lxml version at PR-creation time.
- Re-run `klai-connector.yml` workflow against new image SHA, verify HIGH/CRIT count = 0.
- No `.trivyignore.yaml` needed if dep-bump succeeds.

#### klai-docs
- Bump `next` in `klai-docs/package.json` to a version fixing GHSA-q4gf-8mx6-v5v3. Update `package-lock.json`.
- Bump `picomatch` (likely transitive) by running `npm audit fix` or by adding it as direct dep with patched version.
- Re-run `docs.yml` workflow against new image SHA, verify HIGH/CRIT count = 0.
- No `.trivyignore.yaml` needed if dep-bumps succeed.

#### caddy
- Modify `deploy/caddy/Dockerfile`:
  ```dockerfile
  FROM caddy:2.11.2-builder-alpine AS builder

  RUN xcaddy build \
      --with github.com/caddy-dns/hetzner/v2 \
      --with github.com/mholt/caddy-ratelimit

  FROM caddy:2.11.2-alpine

  COPY --from=builder /usr/bin/caddy /usr/bin/caddy

  # SEC-018 F-029: Caddy deliberately runs as root. ...
  ```
- Re-run `caddy.yml` workflow, verify all 6 HIGH/CRITICAL clear (smallstep/certificates, grpc, otel, go-jose v3, go-jose v4, nghttp2-libs).
- If residuals remain (≤2 expected for upstream stragglers), create `deploy/caddy/.trivyignore.yaml` with rationale and `expired_at: 2026-08-01`.

#### whisper-server
- Modify `klai-scribe/whisper-server/Dockerfile`:
  ```dockerfile
  FROM nvidia/cuda:12.9.1-cudnn-runtime-ubuntu24.04

  RUN apt-get update && apt-get install -y --no-install-recommends \
      python3 python3-pip python3-venv \
      ffmpeg \
      libsndfile1 \
      && apt-get purge -y --auto-remove gnupg gnupg2 \
      && rm -rf /var/lib/apt/lists/*

  WORKDIR /app
  ...
  ```
- Verify GPU smoke-test on gpu-01: `docker compose up -d whisper-server`, then transcribe a known audio file, verify output matches baseline.
- Re-run `whisper-server.yml` workflow against new image SHA, verify all 10 HIGH alerts clear.
- If CVE-2025-68973 family lingers (unlikely given purge), add one ignore-entry `klai-scribe/whisper-server/.trivyignore.yaml`.

### Quality gate

- All 5 services' next scan runs show 0 HIGH/CRITICAL OR all residuals are documented in their `.trivyignore.yaml`
- whisper-server passes GPU smoke-test on gpu-01

### Files touched

~7-9 files: 4 Dockerfile / pyproject.toml / package.json modifications, 1-5 `.trivyignore.yaml` files.

## PR #3 — Activate STRICT gating

### Tasks

For each of 10 internal-image workflows, modify the Trivy step to:
```yaml
- name: Run Trivy vulnerability scanner
  uses: aquasecurity/trivy-action@0.35.0
  with:
    image-ref: ghcr.io/getklai/<svc>:${{ github.sha }}
    trivy-config: .trivy.yaml
    trivyignores: <service-relative-path>/.trivyignore.yaml  # only if file exists
    format: 'sarif'
    output: 'trivy-results.sarif'
    exit-code: '1'
    limit-severities-for-sarif: 'true'
```

Note: `trivyignores:` is only added for services that have a `.trivyignore.yaml` (portal-api, possibly caddy/whisper-server/docs depending on PR #2 outcome). Services with 0 HIGH/CRIT findings post-PR #2 don't need a `.trivyignore.yaml` and don't add the `trivyignores:` line.

### Quality gate

- All 10 workflows green on next push to main
- A test PR with a synthetically introduced HIGH CVE (smoke-test, manual) gets blocked

### Files touched

10 workflow modifications. ~30 lines diff.

### Rollback

`git revert <PR#3 merge commit>` removes `exit-code: '1'` + `limit-severities-for-sarif: 'true'` everywhere; `.trivy.yaml` + ignore-files stay (no behaviour without the gate flip).

## PR #4 — SKIP filter + docs + smoke-test (optional)

### Tasks

1. Modify `scan-pinned-images.yml` enumerate-step:
   ```bash
   IMAGES=$(yq eval-all '.services[].image' \
     deploy/docker-compose.yml \
     deploy/docker-compose.gpu.yml \
     docker-compose.dev.yml \
     2>/dev/null \
     | sort -u \
     | grep -vE '^(ghcr\.io/getklai/|ghcr\.io/mendableai/firecrawl|[^:]+:klai$|[^:]+:local$|[^:]+:.*-local-[0-9]{6}-[0-9]{4}$|---)' \
     | grep -vE '^(null|~)$' \
     | jq -R . | jq -sc .)
   ```
2. Rewrite `.claude/rules/klai/infra/deploy.md` Trivy section with three-tier model + references.
3. Create `docs/runbooks/trivy-policy.md` with: how to add an ignore-entry, expiry-rotation cadence, smoke-test recipe.
4. Create `scripts/validate-trivyignore.sh` (REQ-5 enforcement: rejects entries missing `id` / `statement` / `expired_at`).
5. Wire `scripts/validate-trivyignore.sh` into pre-commit and into a CI check (lightweight, runs on any PR touching `**/.trivyignore.yaml`).
6. Run smoke-test once: build a test image with a deliberately old base (e.g. `debian:11.4`), push to a branch, verify scan blocks merge.

### Quality gate

- `scan-pinned-images.yml` next run no longer attempts vexaai locally-built tags
- `validate-trivyignore.sh` rejects a synthetic bad entry
- Smoke-test on a test branch produces the expected red CI

## Reference implementations

- **EARS pattern**: `SPEC-INFRA-CONTAINER-HYGIENE-001/spec.md` — mechanical guard model (REQ-1 hooks, REQ-2 labels, REQ-5 audit). Closest analogue for "rule that blocks at commit/CI time".
- **Per-service config files**: existing `klai-portal/backend/pyproject.toml` pip-audit ignore-list (lines 73-74 of `portal-api.yml`) shows how rationale-comments document deferred CVEs. We're upgrading from inline workflow comments to per-service `.trivyignore.yaml` with mandatory `expired_at`.
- **Pre-commit guard pattern**: `deploy/check-image-pullable.sh` (called from pre-commit per `infra/container-hygiene.md`). `validate-trivyignore.sh` follows the same shape: shell script, fails fast, clear error message.
- **CI uniform-pattern apply**: SPEC-INFRA-001 history shows mass-modify-workflows pattern (one PR touching 10+ workflow files with identical diff shape).

## MX tag plan

Workflow YAML files don't carry MX tags. The single new shell script (`scripts/validate-trivyignore.sh`) gets:
- `@MX:NOTE` at the top: rationale for why `.trivyignore.yaml` entries require `id` + `statement` + `expired_at` (references REQ-5 of this SPEC).
- `@MX:ANCHOR` on the validation function if it gains call-sites beyond pre-commit + CI.

`.trivy.yaml` and `.trivyignore.yaml` files don't carry MX tags (declarative config).

## Open questions resolved during planning

- **Phase ordering**: 3 separate PRs (decided 2026-05-06 with user)
- **Whisper-server CVE-2025-68973**: base image bump to CUDA 12.9.1 + apt purge gnupg (decided 2026-05-06 with user)
- **Caddy 6 HIGH/CRIT**: pin caddy:2.11.2-builder-alpine + caddy:2.11.2-alpine (decided 2026-05-06 with user)
- **Rule + runbook split**: both files (decided 2026-05-06 with user)
- **SPEC-id**: SPEC-CI-TRIVY-POLICY-001 (decided 2026-05-06 with user)

## Effort estimate (priority, not time)

- PR #1: priority HIGH (unblocks everything else, low risk)
- PR #2: priority HIGH (covers existing finding gap)
- PR #3: priority MEDIUM (the gate flip itself; gated on PR #2 verification)
- PR #4: priority MEDIUM (docs + smoke-test; can lag PR #3 by a few days)
