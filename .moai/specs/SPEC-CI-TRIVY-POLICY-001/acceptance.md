# Acceptance Criteria — SPEC-CI-TRIVY-POLICY-001

Each scenario is `Given / When / Then`. A scenario passes when the observed CI behaviour matches the `Then` clause.

## Scenario 1 — STRICT gate fires on a fresh HIGH CVE in an internal image

**Given** PR #3 has merged so all 10 internal-image workflows carry `exit-code: '1'` + `limit-severities-for-sarif: 'true'` + `trivy-config: .trivy.yaml`
**And** a developer pushes a commit to main that introduces a new dependency (or updates an existing one) carrying a HIGH-severity unfixed-vulnerability-with-fixed-version
**When** the workflow's `scan` job runs against the freshly built image
**Then** Trivy SHALL exit with code 1, the workflow's status check SHALL be `failure`, the deploy job SHALL not run (gated on `needs: build-push` which itself is gated on the workflow as a whole), and the SARIF SHALL upload to the GitHub Security tab containing exactly one `error`-severity finding for the new HIGH.

## Scenario 2 — STRICT gate stays green when an unfixed HIGH appears with no patch upstream

**Given** all 10 internal-image workflows are STRICT-tier
**And** Trivy DB updates introduce a new HIGH for which no fix-version exists yet
**When** the next workflow run scans the unchanged image
**Then** because `vulnerability.ignore-unfixed: true` is set in `.trivy.yaml`, Trivy SHALL report 0 errors, the workflow SHALL stay green, and the SARIF SHALL still contain the finding for visibility (Security tab) but the SARIF severity-level for that entry SHALL be downgraded per `limit-severities-for-sarif` filter.

## Scenario 3 — Documented exemption suppresses the finding

**Given** `klai-portal/backend/.trivyignore.yaml` contains a valid entry for CVE-2026-6357 (id + statement + `expired_at: 2026-09-01`)
**And** `portal-api.yml` workflow passes `trivyignores: klai-portal/backend/.trivyignore.yaml`
**When** Trivy scans the portal-api image and detects pip 26.0.1 / CVE-2026-6357
**Then** Trivy SHALL skip the finding from the exit-code calculation, the workflow `scan` job SHALL exit 0, and SARIF SHALL still include the CVE marked as suppressed for audit-trail purposes.

## Scenario 4 — Expired exemption is no longer suppressed

**Given** an entry in `klai-portal/backend/.trivyignore.yaml` with `expired_at: 2026-09-01`
**And** the wall-clock date is 2026-09-02 or later
**When** the next portal-api workflow run scans the image
**Then** Trivy SHALL no longer suppress the matching CVE, the `scan` job SHALL exit 1 (assuming the CVE is still HIGH/CRITICAL and unfixed), and CI SHALL block until either the exemption is renewed (with new `expired_at`) or the dep is bumped.

## Scenario 5 — Pre-merge guard rejects malformed `.trivyignore.yaml`

**Given** PR #4 has landed `scripts/validate-trivyignore.sh` and wired it into pre-commit + the workflow trigger on `**/.trivyignore.yaml` paths
**And** a developer opens a PR adding an entry like `{ id: CVE-2099-12345, paths: [...] }` (missing both `statement` and `expired_at`)
**When** the validation guard runs
**Then** the guard SHALL exit non-zero with stderr message naming the offending file + line + missing fields, the PR's status check SHALL be `failure`, and the merge button SHALL be disabled by branch protection.

## Scenario 6 — `scan-pinned-images.yml` skips locally-built tags

**Given** `deploy/docker-compose.gpu.yml` contains `image: vexaai/transcription-service:0.10.6-local-260503-0858`
**And** PR #4 has updated the enumerate-step exclude regex to `[^:]+:.*-local-[0-9]{6}-[0-9]{4}$`
**When** `scan-pinned-images.yml` runs (cron or compose-file change)
**Then** the produced matrix SHALL not include `vexaai/transcription-service:0.10.6-local-260503-0858`, the workflow SHALL not attempt a `docker manifest inspect` for that image, and no UNAUTHORIZED scan-job failure SHALL appear in the run summary.

## Scenario 7 — `scan-pinned-images.yml` stays warn-only on external HIGH

**Given** an external image like `redis:8-alpine` carries 5 HIGH-severity unfixed-with-fix-available CVEs in the Trivy DB
**When** `scan-pinned-images.yml` runs
**Then** the per-image `scan` job SHALL upload SARIF, the job SHALL exit 0 (warn-only because `exit-code: '0'`), the GitHub Security tab SHALL contain the 5 findings, and the workflow SHALL not block any merge.

## Scenario 8 — Single source of truth for severity policy

**Given** `.trivy.yaml` is committed to repo root with `severity: [CRITICAL, HIGH]`
**And** all 11 Trivy invocations across CI workflows pass `trivy-config: .trivy.yaml`
**When** an audit script greps every workflow file for inline `severity:`, `ignore-unfixed:`, or `scanners:` keys
**Then** the script SHALL find zero matches outside `.trivy.yaml` itself (REQ-1 enforcement: no per-workflow duplication of severity policy).

## Scenario 9 — Whisper-server gnupg attack-surface removed

**Given** PR #2's whisper-server Dockerfile change has merged (`nvidia/cuda:12.9.1-cudnn-runtime-ubuntu24.04` + `apt purge -y --auto-remove gnupg gnupg2`)
**When** the new image is built and Trivy scans it
**Then** Trivy SHALL NOT report any CVE in `gnupg2`, `gpg`, `gpgv`, `gpgsm`, `dirmngr`, `keyboxd`, `gnupg-utils`, `gnupg-l10n`, `gpg-agent`, `gpgconf`, `gpg-wks-server`, or `gpg-wks-client` packages, because those packages SHALL not be installed in the image. CVE-2025-68973 family count = 0.

## Scenario 10 — Caddy go-deps refresh via image pin

**Given** PR #2's Caddy Dockerfile change has merged (`caddy:2.11.2-builder-alpine` + `caddy:2.11.2-alpine`)
**When** xcaddy rebuilds and Trivy scans the resulting image
**Then** the 2 CRITICAL findings (CVE-2026-30836 in smallstep/certificates, CVE-2026-33186 in google.golang.org/grpc) SHALL be 0 because Caddy 2.11.2's `go.mod` SHALL pin patched versions; if any of the 4 HIGH findings (CVE-2026-39883 otel, CVE-2026-34986 go-jose v3, CVE-2026-34986 go-jose v4, CVE-2026-27135 nghttp2-libs) persist, they SHALL be documented in `deploy/caddy/.trivyignore.yaml` with rationale and `expired_at` ≤ 2026-08-01.

## Quality gate criteria (rollup)

A SPEC implementation is considered acceptable only when:

1. All 10 internal-image workflows show `conclusion: success` for the latest `scan` job on `main` for at least 3 consecutive runs
2. `gh api '/repos/getklai/klai/code-scanning/alerts?state=open&tool_name=Trivy' --jq '[.[] | select(.most_recent_instance.analysis_key | contains("scan-pinned-images") | not) | select(.rule.security_severity_level == "high" or .rule.security_severity_level == "critical")] | length'` returns 0 OR every non-zero result has a matching valid entry in some `.trivyignore.yaml`
3. `.trivy.yaml` is the only file in the repo containing `severity: [CRITICAL, HIGH]` AND `ignore-unfixed: true` AND `scanners: [vuln]` together
4. Smoke-test (PR #4) demonstrates a synthetic HIGH triggers exit-code 1
5. `scan-pinned-images.yml` next run does not attempt any image with tag matching `[^:]+:.*-local-[0-9]{6}-[0-9]{4}$`
6. `scripts/validate-trivyignore.sh` exits non-zero on a deliberately malformed entry and exits zero on a valid one

## Performance criteria

- Trivy scan job duration ≤ 60 seconds median per workflow (current ~10s for portal-api; flipping `exit-code` doesn't change duration)
- Trivy DB download cached via `aquasecurity/setup-trivy@e6c2c5e3` (already in scan-pinned-images.yml; consistency for internal-image workflows is a follow-up, not blocking)

## Security criteria

- After PR #3, no internal `ghcr.io/getklai/*` image SHALL deploy if it contains an unfixed HIGH or CRITICAL not listed under a valid `.trivyignore.yaml` entry
- Each `.trivyignore.yaml` entry SHALL carry rationale + expiry; no perpetual "set and forget" exemptions
- The branch-protection rule on `main` SHALL keep the scan job as a required status check (already in place per `infra/deploy.md`); this SPEC does not weaken it
