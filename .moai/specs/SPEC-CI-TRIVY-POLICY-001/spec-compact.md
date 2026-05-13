# SPEC-CI-TRIVY-POLICY-001 — Compact reference

Auto-extracted from spec.md + acceptance.md for run-phase token efficiency.

## Requirements (EARS)

**REQ-1** — The system SHALL define `severity: [CRITICAL, HIGH]`, `vulnerability.ignore-unfixed: true`, and `scan.scanners: [vuln]` in **one** file `.trivy.yaml` at repo root. All CI Trivy invocations SHALL reference it via `trivy-config: .trivy.yaml`. No CI workflow SHALL inline these three keys.

**REQ-2** — WHEN a CI workflow scans a `ghcr.io/getklai/*` image, the Trivy step SHALL exit non-zero if Trivy reports at least one unfixed HIGH/CRITICAL not listed under an unexpired `.trivyignore.yaml` entry. The step SHALL pass `exit-code: '1'` AND `limit-severities-for-sarif: 'true'`.

**REQ-3** — `scan-pinned-images.yml` SHALL pass `exit-code: '0'` (warn-only), upload SARIF to Security tab, and reference `trivy-config: .trivy.yaml`. External images SHALL never block CI.

**REQ-4** — WHEN `scan-pinned-images.yml`'s enumerate-step builds the matrix, it SHALL exclude tags matching `[^:]+:.*-local-[0-9]{6}-[0-9]{4}$` per `infra/container-hygiene.md` locally-built convention.

**REQ-5** — WHILE a `.trivyignore.yaml` entry exists, it SHALL contain `id`, `statement` (rationale, no boilerplate), and `expired_at` (`YYYY-MM-DD`, ≤12 months from `created`). A pre-merge guard SHALL reject PRs with entries missing one of these fields.

## Acceptance scenarios (Given / When / Then)

1. **STRICT gate fires on fresh HIGH** — Given STRICT-tier active; When new HIGH dep landed; Then Trivy exit 1 + workflow failure + deploy gated.
2. **Unfixed HIGH stays green** — Given `ignore-unfixed: true` in .trivy.yaml; When upstream HIGH appears with no fix; Then 0 errors, workflow green, SARIF still uploaded.
3. **Documented exemption suppresses** — Given valid `.trivyignore.yaml` entry; When matching CVE detected; Then exit 0, SARIF marks suppressed.
4. **Expired exemption is no longer suppressed** — Given `expired_at` past; When workflow runs; Then suppression lifted, CI blocks.
5. **Pre-merge guard rejects malformed entry** — Given entry missing `statement` or `expired_at`; When validate-trivyignore.sh runs; Then exit non-zero + status failure.
6. **scan-pinned-images skips locally-built tags** — Given `vexaai/transcription-service:0.10.6-local-260503-0858` in compose; When enumerate runs; Then matrix excludes it.
7. **scan-pinned-images stays warn-only** — Given external image with HIGH; When workflow runs; Then SARIF uploaded, exit 0, no merge block.
8. **Single source of truth** — Given `.trivy.yaml` committed; When grep audit runs; Then 0 inline `severity:` matches outside `.trivy.yaml`.
9. **Whisper-server gnupg removed** — Given Dockerfile bumped to `cuda:12.9.1` + apt purge gnupg; When scan runs; Then 0 CVEs in gnupg family.
10. **Caddy go-deps refreshed via pin** — Given Dockerfile pinned to `caddy:2.11.2-*`; When xcaddy rebuilds; Then 2 CRITICAL = 0; residuals (≤4 HIGH max) documented in `.trivyignore.yaml` ≤ 2026-08-01.

## Files to modify

**New:**
- `.trivy.yaml`
- `klai-portal/backend/.trivyignore.yaml`
- `klai-scribe/whisper-server/.trivyignore.yaml` (conditional on PR #2 outcome)
- `deploy/caddy/.trivyignore.yaml` (conditional on PR #2 outcome)
- `klai-docs/.trivyignore.yaml` (conditional on PR #2 outcome)
- `scripts/validate-trivyignore.sh`
- `docs/runbooks/trivy-policy.md`

**Modified workflows (11):**
- `.github/workflows/portal-api.yml` (PR #1 + PR #3)
- `.github/workflows/caddy.yml` (PR #1 + PR #3)
- `.github/workflows/whisper-server.yml` (PR #1 + PR #3)
- `.github/workflows/knowledge-ingest.yml` (PR #1 + PR #3)
- `.github/workflows/klai-knowledge-mcp.yml` (PR #1 + PR #3)
- `.github/workflows/retrieval-api.yml` (PR #1 + PR #3)
- `.github/workflows/klai-connector.yml` (PR #1 + PR #3)
- `.github/workflows/klai-mailer.yml` (PR #1 + PR #3)
- `.github/workflows/docs.yml` (PR #1 + PR #3)
- `.github/workflows/scribe-api.yml` (PR #1 + PR #3)
- `.github/workflows/scan-pinned-images.yml` (PR #1 + PR #4)

**Modified Dockerfiles / pyproject.toml (PR #2):**
- `klai-scribe/whisper-server/Dockerfile`
- `deploy/caddy/Dockerfile`
- `klai-connector/pyproject.toml`
- `klai-docs/package.json` + `package-lock.json`

**Modified rules:**
- `.claude/rules/klai/infra/deploy.md` (Trivy section rewrite — PR #4)

## Exclusions (out of scope)

- Cosign image signing / SLSA-Level-3 attestations
- Runtime vulnerability scanning (Falco / Trivy K8s admission webhook)
- Multi-scanner combos (Grype, Snyk on top of Trivy)
- pip-audit consolidation into Trivy ignore-list (separate concern)
- Renovate `vulnerabilityAlerts: enabled: true` config (separate concern)
- trivy-action upgrade to ≥0.36 (separate Renovate-track)
- klai-portal/frontend npm scan via Trivy-fs (covered by frontend pipeline)
- Pre-commit Trivy hook for local development (possible follow-up SPEC)
- klai-mailer current CI failure (workflow file issue, not Trivy)
- CUDA 13.x bump for whisper-server (major version risk; out of scope here)
