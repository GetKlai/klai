---
id: SPEC-CI-TRIVY-POLICY-001
version: "0.1.0"
status: draft
created: "2026-05-06"
updated: "2026-05-06"
author: MoAI
priority: high
issue_number: 0
---

## HISTORY

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1.0 | 2026-05-06 | MoAI | Initial draft. Triggered by `Build and push portal-api` workflow failing on every push to main since 2026-05-06 morning. Root cause analysis surfaced two issues: (1) `portal-api.yml :scan` step omits `exit-code: '0'` so trivy-action 0.35.0's default `1` applies, and (2) trivy-action 0.35.0 unsets `TRIVY_SEVERITY` when `format: sarif` is used without `limit-severities-for-sarif: true` — meaning the documented `severity: 'CRITICAL,HIGH'` filter has been silently disabled across all 10 internal scan jobs since the workflows were written. Result: scan jobs upload SARIF but never gate. The `infra/deploy.md` rule and the inline workflow comment in `portal-api.yml` both state the policy is "fail-on-find for CRITICAL+HIGH" — actual behaviour is fail-open across the board. SPEC fixes the policy gap by introducing a centralised `.trivy.yaml` plus per-service `.trivyignore.yaml` (with mandatory `expired_at`) and flips `exit-code: '1'` + `limit-severities-for-sarif: 'true'` on all 10 internal-image workflows simultaneously. External-pin scans stay warn-only. Locally-built tags (`*-local-YYMMDD-HHMM`) get filtered out of `scan-pinned-images.yml`. |

# SPEC-CI-TRIVY-POLICY-001: Industry-standard Trivy CVE scanning policy (three-tier: STRICT / WARN / SKIP)

## Overview

Klai's CI Trivy scans currently behave as security theater: every internal-image workflow uploads SARIF to the GitHub Security tab but never blocks merges. Two latent bugs caused this:

1. **`exit-code` defaults to `'1'`** in trivy-action 0.35.0 when not set explicitly. Nine of the ten internal-image workflows compensated by setting `exit-code: '0'` (warn-only). One — `portal-api.yml` — forgot, and on 2026-05-06 a new pip MEDIUM CVE on the runner image broke its scan job for every push to main.
2. **The `severity: 'CRITICAL,HIGH'` filter is silently dropped** for SARIF format. trivy-action 0.35.0's entrypoint unsets `TRIVY_SEVERITY` when `format: sarif` is used without `limit-severities-for-sarif: 'true'`. Visible in CI logs as `Building SARIF report with all severities`. Consequence: even when a workflow uses `exit-code: '1'`, it would fire on MEDIUM/LOW findings — not just HIGH/CRITICAL as the policy comment claims.

These two interact: the workflow that wanted to fail on HIGH/CRITICAL ended up failing on MEDIUM, and the workflows that wanted to fail on HIGH/CRITICAL ended up failing on nothing.

This SPEC replaces the broken setup with three explicit tiers:

- **STRICT** — every `ghcr.io/getklai/*` image we build. CI fails the build on any unfixed HIGH/CRITICAL not listed in the service's `.trivyignore.yaml`. This is what `infra/deploy.md` already promises but never delivered.
- **WARN** — every external image we pin in compose (nginx, postgres, qdrant, …). SARIF goes to Security tab; CI never blocks. Upgrade pressure runs through Renovate. We can't fix upstream so blocking would just stall every merge.
- **SKIP** — locally-built non-pullable tags (`vexaai/*-local-YYMMDD-HHMM` per `infra/container-hygiene.md`'s convention). No registry, no scan, no false failures.

The policy lives in **one** place: `.trivy.yaml` at repo root. Documented exemptions live in **per-service** `.trivyignore.yaml` files with mandatory `expired_at` dates so no exemption survives forever without active renewal.

## Three-tier scan model

### STRICT tier — internal images we build

Applies to: `caddy.yml`, `klai-connector.yml`, `klai-mailer.yml`, `knowledge-ingest.yml`, `klai-knowledge-mcp.yml`, `portal-api.yml`, `retrieval-api.yml`, `scribe-api.yml`, `whisper-server.yml`, `docs.yml` (10 workflows).

Trivy step pattern after this SPEC:

```yaml
- name: Run Trivy vulnerability scanner
  uses: aquasecurity/trivy-action@0.35.0
  with:
    image-ref: ghcr.io/getklai/<svc>:${{ github.sha }}
    trivy-config: .trivy.yaml
    trivyignores: <service-relative-path>/.trivyignore.yaml
    format: 'sarif'
    output: 'trivy-results.sarif'
    exit-code: '1'
    limit-severities-for-sarif: 'true'
```

The four lines that change behaviour: `trivy-config`, `trivyignores`, `exit-code: '1'`, `limit-severities-for-sarif: 'true'`. The first two share policy. The third actually gates. The fourth makes the severity filter functional.

### WARN tier — external pinned images

Applies to: `scan-pinned-images.yml` matrix scan against ~25 external images.

Trivy step keeps `exit-code: '0'` and adds `trivy-config: .trivy.yaml` for severity-policy consistency. SARIF still uploads. This stays warn-only because we don't own those images and Renovate handles upgrade pressure on a Monday-morning cadence.

### SKIP tier — locally-built non-pullable tags

`scan-pinned-images.yml`'s `enumerate` step extracts every `image:` ref from `deploy/docker-compose*.yml` and `docker-compose.dev.yml`. The current exclude regex catches `:klai$` and `:local$` but not the canonical `<semver>-local-YYMMDD-HHMM` convention from `infra/container-hygiene.md` ("Verify image pullable before pinning a tag"). The 2026-04-22 run failed on `vexaai/transcription-service:0.10.6-local-260503-0858` for exactly this reason.

Updated exclude regex pattern: `[^:]+:.*-local-[0-9]{6}-[0-9]{4}$`.

## Requirements (EARS format)

### REQ-1 — Centralised severity policy
The system SHALL define `severity: [CRITICAL, HIGH]`, `vulnerability.ignore-unfixed: true`, and `scan.scanners: [vuln]` in **one** file `.trivy.yaml` at the repository root. All CI Trivy invocations SHALL reference this file via `trivy-config: .trivy.yaml`. No CI workflow SHALL inline these three keys.

### REQ-2 — STRICT-tier gating for internal images
WHEN a CI workflow scans a `ghcr.io/getklai/*` image, the Trivy step SHALL exit with non-zero status if Trivy reports at least one unfixed HIGH or CRITICAL vulnerability not listed under an unexpired `.trivyignore.yaml` entry. The step SHALL pass `exit-code: '1'` and `limit-severities-for-sarif: 'true'` (the latter neutralises trivy-action 0.35.0's SARIF severity-filter unset).

### REQ-3 — WARN-tier preserved for external images
`scan-pinned-images.yml` SHALL pass `exit-code: '0'` (warn-only), upload SARIF to the GitHub Security tab, and reference `trivy-config: .trivy.yaml` for severity-policy consistency. External images SHALL never block CI; upgrade pressure runs through Renovate.

### REQ-4 — SKIP-tier filter for locally-built tags
WHEN the `enumerate` job of `scan-pinned-images.yml` builds the matrix of external images, the system SHALL exclude any image tag matching `[^:]+:.*-local-[0-9]{6}-[0-9]{4}$`. The exclusion SHALL carry an inline comment referencing `infra/container-hygiene.md`'s locally-built convention.

### REQ-5 — `.trivyignore.yaml` discipline (mandatory fields)
WHILE a `.trivyignore.yaml` entry exists, it SHALL contain three required fields per entry: `id` (CVE / GHSA / rule identifier), `statement` (rationale describing why the finding is non-exploitable in this context — boilerplate "low priority" or "acceptable risk" SHALL be rejected), and `expired_at` (date in `YYYY-MM-DD` format, no more than 12 months from the entry's `created` date). A pre-merge guard script SHALL reject any PR whose `.trivyignore.yaml` files contain entries missing one of those three fields.

## Affected files

**New (config + initial exemptions):**

- `.trivy.yaml` — root policy file (REQ-1)
- `klai-portal/backend/.trivyignore.yaml` — initial known exemption: CVE-2026-6357 (pip 26.0.1, CI runner bootstrap, `expired_at: 2026-09-01`)
- `klai-scribe/whisper-server/.trivyignore.yaml` — interim residual entries IF the gnupg purge doesn't fully clear CVE-2025-68973 family
- `deploy/caddy/.trivyignore.yaml` — interim residual entries IF Caddy 2.11.2 base bump doesn't fully clear all 6 HIGH/CRITICAL findings (smallstep/certificates, grpc, otel, go-jose v3, go-jose v4, nghttp2-libs)
- `klai-docs/.trivyignore.yaml` — interim residual entries IF Renovate-managed npm bumps for `next` and `picomatch` haven't landed yet at flip-time
- `scripts/validate-trivyignore.sh` — pre-commit / CI guard script enforcing REQ-5
- `docs/runbooks/trivy-policy.md` — recipe for adding entries, rotating expired ones, running smoke-test, reading Security tab

**Modified workflows:**

- `.github/workflows/portal-api.yml` (REQ-2: 4 args added to Trivy step)
- `.github/workflows/caddy.yml` (REQ-2)
- `.github/workflows/whisper-server.yml` (REQ-2)
- `.github/workflows/knowledge-ingest.yml` (REQ-2)
- `.github/workflows/klai-knowledge-mcp.yml` (REQ-2)
- `.github/workflows/retrieval-api.yml` (REQ-2)
- `.github/workflows/klai-connector.yml` (REQ-2)
- `.github/workflows/klai-mailer.yml` (REQ-2)
- `.github/workflows/docs.yml` (REQ-2)
- `.github/workflows/scribe-api.yml` (REQ-2)
- `.github/workflows/scan-pinned-images.yml` (REQ-3 + REQ-4: enumerate-step regex extension)

**Modified Dockerfiles (PR #2 dep-bumps):**

- `klai-scribe/whisper-server/Dockerfile` — bump `nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04` to `nvidia/cuda:12.9.1-cudnn-runtime-ubuntu24.04`, add `apt-get purge -y --auto-remove gnupg gnupg2` to remove unused gpg attack surface
- `deploy/caddy/Dockerfile` — pin `caddy:builder` to `caddy:2.11.2-builder-alpine` and `caddy:latest` to `caddy:2.11.2-alpine` (also resolves an existing violation of `infra/servers.md`'s "never `:latest` in production" rule)

**Modified rules:**

- `.claude/rules/klai/infra/deploy.md` — Trivy section rewritten with three-tier model, severity-policy reference, expiry-discipline reference, and pointers to `.trivy.yaml` + `.trivyignore.yaml`

## Implementation phases (three PRs)

This SPEC is delivered through three sequential PRs. Each PR is independently mergeable and revert-safe.

### PR #1 — Centralised config (no behaviour change)

Adds `.trivy.yaml` and modifies all 11 workflows to reference it via `trivy-config:`. Workflow `exit-code` values stay as-is (`0` everywhere except portal-api which still has the implicit `1` default that's currently failing). `severity:` and `ignore-unfixed:` are removed from per-workflow inputs (they now come from `.trivy.yaml`).

Goal: prove central policy works; no gating change yet. portal-api's scan job remains red until PR #2 lands.

### PR #2 — Per-service findings dispositie

Five services have open HIGH/CRITICAL findings. This PR resolves them through dep-bumps where possible and documented `.trivyignore.yaml` entries where not.

| Service | Finding(s) | Resolution |
|---|---|---|
| portal-api | CVE-2026-6357 MEDIUM (pip 26.0.1) | `klai-portal/backend/.trivyignore.yaml` with `expired_at: 2026-09-01` |
| klai-connector | CVE-2026-41066 HIGH (lxml) | dep-bump in `pyproject.toml` |
| klai-docs | GHSA-q4gf-8mx6-v5v3 HIGH (next), CVE-2026-33671 HIGH (picomatch) | npm bumps via `package-lock.json` updates (Renovate-style, manual here) |
| caddy | 6 HIGH/CRITICAL across smallstep/certificates, grpc, go-jose v3+v4, otel, nghttp2-libs | Pin `caddy:2.11.2-builder-alpine` + `caddy:2.11.2-alpine` (also fixes existing `:latest` rule violation); xcaddy rebuild pulls fresh Go deps; residuals (if any) get `.trivyignore.yaml` entries |
| whisper-server | 10 HIGH (all CVE-2025-68973 in gnupg family) | Bump base to `nvidia/cuda:12.9.1-cudnn-runtime-ubuntu24.04`; `apt purge -y --auto-remove gnupg gnupg2` to remove unused attack surface |

Each finding's resolution is verified by re-running its workflow against the new image SHA. Residual findings that genuinely cannot be fixed at this time get a `.trivyignore.yaml` entry with `expired_at` set to a near-term date (≤3 months) so they bubble up for re-evaluation.

### PR #3 — Activate STRICT gating

Single PR that flips all 10 internal-image workflows simultaneously: `exit-code: '1'` + `limit-severities-for-sarif: 'true'` + `trivyignores: <path>` (where applicable). After this PR, REQ-2 is in force.

Verification: `gh run list --workflow <each>.yml --limit 3` shows green for the latest run on main.

Rollback path: single revert commit reverts the workflow changes; `.trivy.yaml` and `.trivyignore.yaml` files stay (they're harmless without `exit-code: 1`).

### PR #4 — SKIP filter + docs + smoke-test (optional follow-up)

Updates `scan-pinned-images.yml` enumerate-step regex (REQ-4), rewrites `infra/deploy.md` Trivy section, adds `docs/runbooks/trivy-policy.md`, and runs the smoke-test once to demonstrate the gate is real (REQ-6 informally).

This PR could be folded into PR #3 for atomicity, or split for review-load reduction. Default plan: split.

## Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| PR #3 breaks CI on a service whose findings weren't fully cleared by PR #2 | Medium | High | Strict separation: PR #2 must land + verify all 10 services' next scan-runs are clean (or have ignore-entry coverage) before PR #3 opens. PR #1 provides the trivy-config plumbing without flipping the gate. |
| Trivy DB updates introduce a fresh HIGH on a base image post-flip | Medium | Medium | 80% of base-image-driven CVEs auto-bump via Renovate (pinned base images get PRs). Remainder is documented `.trivyignore.yaml` with `expired_at` ≤3 months for forced re-evaluation. |
| Caddy 2.11.2 doesn't actually clear all 6 findings | Low | Medium | PR #2 verifies on actual scan; residuals get documented `.trivyignore.yaml` entries with rationale. Worst case: 6 entries with valid `statement` + `expired_at` ≤3 months. |
| Whisper-server CUDA 12.9.1 bump breaks GPU inference | Low | High | Same major (12.x), patch-level CUDA bump. Smoke test on gpu-01 before merging PR #2's whisper Dockerfile change. Major-version (CUDA 13) bump explicitly out of scope. |
| `.trivyignore.yaml` becomes copy-paste cargo cult without rationale | Medium | Medium | REQ-5 enforced mechanically by `scripts/validate-trivyignore.sh`. Entries missing `statement` or with boilerplate like "low priority" SHALL be rejected at pre-commit / CI. |
| trivy-action ≥0.36 release changes SARIF behaviour again | Low | Medium | Action version is pinned at `0.35.0`. Renovate group keeps trivy-action upgrades behind manual approval. |
| Branch-protection bypass via `--no-verify` push | Low | High | Branch protection on main already requires green status checks per `infra/deploy.md`. Verify before PR #3 merge. |

## Out of scope (explicit)

- Cosign image signing / SLSA-Level-3 attestations
- Runtime vulnerability scanning (Falco, Trivy K8s admission webhook) — Klai runs Compose, not K8s
- Multi-scanner combos (Grype, Snyk on top of Trivy) — diminishing returns
- pip-audit consolidation into Trivy ignore-list — separate concern, follow-up SPEC
- Renovate `vulnerabilityAlerts: enabled: true` auto-merge configuration — separate concern
- trivy-action upgrade to ≥0.36 — separate Renovate-track
- klai-portal/frontend npm scan via Trivy-fs — already covered by frontend pipeline
- Pre-commit Trivy hook for local development — possible follow-up SPEC
- klai-mailer current CI failure (workflow file issue, not Trivy)

## References

- `.claude/rules/klai/infra/deploy.md` — current Trivy rule (to be rewritten by REQ-7 doc work)
- `.claude/rules/klai/infra/container-hygiene.md` — locally-built tag convention referenced by REQ-4
- `.claude/rules/klai/infra/servers.md` — "never `:latest` in production" rule (Caddy fix in PR #2 also closes this gap)
- `docs/retros/2026-05-03-vexa-transcription-tag.md` — origin of the locally-built convention
- `aquasecurity/trivy-action@0.35.0` — entrypoint logic at `entrypoint.sh` (the `Building SARIF report with all severities` branch)
- Trivy filtering docs — `.trivyignore.yaml` schema (top-level keys: `vulnerabilities`, `secrets`, `misconfigurations`, `licenses`; per-entry: `id`, `paths`, `purls`, `statement`, `expired_at` in `YYYY-MM-DD`)
- Existing CI-domain SPECs: `SPEC-CI-E2E-GATE-001`, `SPEC-CI-PG-FIXTURE-001`
- Existing INFRA-domain SPEC pattern: `SPEC-INFRA-CONTAINER-HYGIENE-001` (mechanical guards model)
