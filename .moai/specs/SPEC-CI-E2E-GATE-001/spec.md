---
id: SPEC-CI-E2E-GATE-001
version: "0.1.0"
status: draft
created: "2026-04-30"
updated: "2026-04-30"
author: MoAI
priority: high
issue_number: 0
---

## HISTORY

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1.0 | 2026-04-30 | MoAI | Stub created as the structural forcing-function answer to the 2026-04-29 dual outage (login 502 + mailer 500). Both regressions could have been caught in the CI deploy window if a real end-to-end login + password-reset smoke had been wired up. |

# SPEC-CI-E2E-GATE-001: Post-deploy E2E smoke gate as a forcing function for security-allowlist regressions

## Overview

Wire `SPEC-TEST-E2E-001` (the manual Playwright smoke runbook) into a CI-driven post-deploy gate that blocks merges of regressions that would 5xx the live login or mailer flows. The gate runs against a dedicated **staging tenant** (NOT production), uses a service-account login (NOT a human's credentials), and reports pass/warn/fail per journey to a GitHub status check on the merge commit.

The forcing-function value of this SPEC is that the next class of regression — a security-allowlist that misses a hostname, a config validator that rejects a real prod value, a dependency that drops a payload field — is caught before the deploy fans out to all production services. Two such regressions hit prod on 2026-04-29 in the same hour because no staging-equivalent test exercised the actual login + mail flow against the deployed image. Both incidents would have been caught by a 90-second smoke run between build-push and deploy.

## Environment

- **Runner:** GitHub Actions on ubuntu-latest with the Playwright Docker image. Triggered after `build-push` succeeds, BEFORE `deploy` runs.
- **Target tenant:** NEW dedicated staging tenant — `staging.getklai.com` (apex) or `staging-ci.getklai.com`. NOT the existing `getklai.getklai.com` tenant (which is internal Klai usage and is the production-equivalent for Klai-the-customer).
- **Staging Zitadel:** either a separate Zitadel instance OR the production Zitadel with a dedicated org for staging. Decision deferred to the implementer.
- **Login mechanism in CI:** dedicated service-user account with TOTP secret stored in GitHub Actions secrets. The smoke uses pyotp (or equivalent) to generate the code at runtime — no human interaction.
- **Existing artifact reuse:** `.moai/specs/SPEC-TEST-E2E-001/` (the runbook). The CI gate runs a SUBSET of those journeys (auth, chat, throwaway-KB, cleanup, report) — a 90-second tier. The full 14-journey runbook stays as the manual interactive version.

## Assumptions

- A1: The merge-to-main workflow has a `build-push` job followed by a `deploy` job. The gate slots between them.
- A2: A 90-second budget is acceptable per merge — measured from build complete to deploy start. CI cost ~$0.005 per run; expected runs ~30/day = $4.5/month.
- A3: The staging tenant can be torn down + reseeded daily (or per-run) without affecting customers. Throwaway data in staging is acceptable; data in production is not.
- A4: Service-account TOTP secrets stored in GitHub Actions encrypted secrets are sufficiently protected. They are NOT stored in any other location and are scoped to the smoke workflow only.
- A5: The mailer flow uses a dedicated test inbox (Mailtrap, MailHog, or a `+ci@getklai.com` Gmail filter rule) so the smoke can verify password-reset emails arrive.

## Requirements

### R1 — Ubiquitous: gate runs on every PR merging to main

Every merge commit to `main` SHALL trigger the smoke workflow as a required GitHub status check before `deploy` runs. The check SHALL pass / fail / warn per the per-journey contract from `SPEC-TEST-E2E-001` § R3.

### R2 — Event-driven: build-push success → smoke trigger

WHEN the `build-push` job for any klai service workflow completes successfully on a merge commit THEN the smoke workflow SHALL be triggered with the new image SHA as input. The smoke SHALL deploy the new image to the staging tenant ONLY (not production), run the journey subset, and report results.

### R3 — Event-driven: smoke fail blocks production deploy

WHEN the smoke workflow returns FAIL on any must-pass journey THEN the `deploy` job for production SHALL not run. A GitHub Status of `failure` on the smoke check SHALL prevent the workflow from proceeding to the production-deploy step. An operator override (manual workflow re-run with `--force-deploy` flag) is permitted but SHALL be logged.

### R4 — State-driven: staging tenant is per-run reseeded

IF a smoke run starts THEN it SHALL execute against a freshly-seeded staging tenant state: one known KB, one known template, one known active user with TOTP enabled. State SHALL be reset between runs so each smoke begins from the same baseline.

### R5 — Unwanted Behavior: smoke MUST NOT touch production

The smoke workflow SHALL NEVER call any URL pointing to a production-tenant subdomain (`*.getklai.com` excluding the explicit staging hostnames). A pre-flight URL allowlist SHALL be enforced in the smoke runner; any test action whose target URL fails the allowlist check SHALL abort the run with a clear error.

### R6 — Optional: nightly full-suite smoke

Where a 90-second per-merge gate is too short for the full 14-journey SPEC-TEST-E2E-001 inventory, a separate **nightly** workflow SHALL run the full SPEC against staging on a cron schedule. Nightly results SHALL be posted to a Slack / GitHub Discussion channel for asynchronous review.

### R7 — Optional: smoke result archive

Smoke run reports + screenshots SHALL be uploaded as GitHub Actions artifacts with 14-day retention. This makes it easy to bisect the merge that introduced a regression by reviewing the failing run alongside the passing prior run.

## Specifications

### Smoke journey subset (per-merge gate)

The 90-second tier SHALL cover at minimum:

| # | Journey | Why included |
|---|---|---|
| 1 | Login bootstrap | Catches REQ-20-class regressions (callback URL allowlist) |
| 2 | Auth/shell | Catches RLS / cookie / CORS regressions |
| 3 | Chat (read-only) | Catches retrieval-api / portal connectivity |
| 4 | Password-reset email | Catches mailer-class regressions (the 2026-04-29 outage) |
| 5 | KB write + cleanup | Catches knowledge-ingest deploy errors |

Total budget: ≤90 seconds wall clock.

### Test inbox for password-reset verification (R4 + Journey 4)

Decision (deferred to implementation): Mailtrap.io free tier OR a dedicated Mailtrap-like service hosted in klai-infra. The smoke polls the inbox API for an arrival within 30 seconds of triggering the reset; absence is a FAIL.

### CI/CD wiring

```yaml
deploy:
  needs: [build-push, smoke]   # smoke is now blocking
  if: needs.smoke.outcome == 'success'
  ...

smoke:
  needs: [build-push]
  runs-on: ubuntu-latest
  timeout-minutes: 3
  steps:
    - uses: actions/checkout@v6
    - uses: docker/setup-buildx-action@v3
    - run: |
        # Deploy new image to staging tenant
        ssh staging-01 "docker compose pull && docker compose up -d ${{ matrix.service }}"
        # Run the smoke journeys
        npx playwright test smoke.spec.ts \
          --reporter=json,junit \
          --output-dir=.tmp/e2e-screenshots/$RUN_TS/
    - uses: actions/upload-artifact@v5
      with:
        name: smoke-${{ github.sha }}
        path: .tmp/e2e-reports/
        retention-days: 14
```

### Hostname allowlist (REQ-5)

Hardcoded in the smoke runner:

```
ALLOWED_SMOKE_HOSTS = {
    "staging.getklai.com",
    "staging-ci.getklai.com",
    "auth-staging.getklai.com",
    "localhost", "127.0.0.1",
}
```

Any `page.goto(url)` call where `urlparse(url).hostname not in ALLOWED_SMOKE_HOSTS` aborts the run.

## Files Affected

- New `.github/workflows/post-deploy-smoke.yml` — the smoke workflow.
- New `tests/e2e/smoke.spec.ts` (or equivalent in chosen test framework) — the actual journey code.
- New `klai-infra/staging-01/` — staging tenant Docker compose, Caddy config, isolated Zitadel.
- Existing `.github/workflows/portal-api.yml`, `klai-mailer.yml`, `retrieval-api.yml`, etc. — add `needs: [smoke]` to their `deploy` jobs.
- Existing `.moai/specs/SPEC-TEST-E2E-001/` — referenced as the source of truth for journey definitions; this SPEC declares which subset becomes the gate.

## MX Tag Plan

- The smoke workflow file is `# @MX:ANCHOR` (deploy gate — fan_in across every klai service workflow).
- The journey subset definition is `# @MX:ANCHOR` (changing it changes what is and isn't caught).

## Exclusions

- **Replacing manual SPEC-TEST-E2E-001 runbook:** No. The full 14-journey runbook remains as the interactive validation. This SPEC defines a 5-journey CI subset.
- **Cross-browser, mobile, accessibility coverage:** out of scope; CI smoke is Chromium-only.
- **Performance benchmarking:** out of scope; existence of the response is the assertion, not response time (beyond a generous timeout).
- **Backfill smoke for already-merged PRs:** out of scope; the gate is forward-only.
- **Replacing SPEC-SEC-AUTH-COVERAGE-001 contract tests:** No. Those are unit-level. This SPEC is integration-level.

## Implementation Notes (for `/moai run`)

- Decision points to resolve before implementation:
  1. Staging tenant infrastructure: dedicated server vs core-01 with namespace isolation
  2. Test inbox: Mailtrap.io vs self-hosted MailHog vs Gmail `+ci` filter
  3. Service-account TOTP: how to rotate the seed if the GitHub Actions secret is exposed
  4. Failure-mode budget: how many flake-retries before the gate is considered failing (suggest: 1 retry)
- Phasing recommendation:
  1. Week 1: Stand up staging tenant + 1 smoke journey (login bootstrap)
  2. Week 2: Add password-reset journey + test inbox
  3. Week 3: Add remaining 3 journeys + nightly full-suite workflow
  4. Week 4: Flip `deploy` jobs to `needs: [smoke]` and observe for 7 days
- Hard rule: do NOT enable `needs: [smoke]` on production deploy jobs until the smoke has run green for 7 consecutive days (no flakes). Flake noise is worse than no gate.
- Anti-pattern to avoid: do NOT use a real human's TOTP-enabled account for the service login. Always create a dedicated `ci-smoke@getklai.com` account.
