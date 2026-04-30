# SPEC-CI-E2E-GATE-001 — Acceptance Criteria

Seven Given/When/Then scenarios. The SPEC is acceptable when all
seven hold during the Phase-4 soak window AND for the first 30 days
post-flip.

## AC-1 — Login bootstrap journey passes against staging

- **Given** the staging tenant is up with the seeded user and the
  `STAGING_SMOKE_TOTP_SEED` secret is configured,
- **When** the smoke workflow triggers via `workflow_run` from a
  service `Build and push *` job,
- **Then** the runner navigates to `https://staging.getklai.com`,
  performs the email/password/TOTP flow, lands on `/app/*`, and
  records the journey as `pass` with measured duration < 30 seconds.

**Test:** `tests/e2e/smoke.spec.ts::login bootstrap`

## AC-2 — Hostname allowlist refuses a production-hostname call

- **Given** the runner's `host_guard.ts` allowlist is in effect,
- **When** the test attempts a `page.goto(url)` where
  `urlparse(url).hostname` is `getklai.com` (production-shaped),
- **Then** the runner aborts the run with a hard error
  `production_hostname_blocked` and the test marks itself as a
  catastrophic failure (NOT a regular `fail`). The smoke workflow
  exits non-zero so production deploy never runs.

**Test:** mutation test in `tests/e2e/_lib/host_guard.test.ts`

## AC-3 — Password-reset journey end-to-end

- **Given** the seeded user exists in staging Zitadel and the
  Mailtrap (or chosen test inbox) is configured,
- **When** the runner triggers `POST /api/auth/password/reset` for
  the seeded user's email,
- **Then** within 30 seconds the test inbox API receives a message
  with subject containing "password" or "wachtwoord", the runner
  extracts the reset link, follows it, sets a new password, and
  records the journey as `pass`.

**Test:** `tests/e2e/smoke.spec.ts::password reset email`

## AC-4 — Smoke fail blocks production deploy

- **Given** a feature branch deliberately reverts PR #230 (the
  2026-04-29 callback-allowlist hotfix), restoring the broken state,
- **When** the branch is merged and the post-deploy-smoke workflow
  runs,
- **Then** the login-bootstrap journey returns `fail` (502 on
  totp-login), the smoke workflow exits non-zero, and the production
  `deploy` job for portal-api does NOT run (verified by checking the
  workflow's job DAG).

**Test:** manual phase-4 verification on a sandbox feature branch.

## AC-5 — Smoke fail blocks deploy for OTHER services too

- **Given** a feature branch deliberately reverts PR #231 (the
  2026-04-29 mailer redis URL hotfix),
- **When** the branch is merged and triggers a `Build and push klai-mailer`,
- **Then** the password-reset journey returns `fail` (mailer 5xx),
  the smoke workflow exits non-zero, and the production `deploy` job
  for `klai-mailer` does NOT run.

**Test:** manual phase-4 verification on a sandbox feature branch.

## AC-6 — Override workflow-dispatch bypasses the gate

- **Given** the smoke is in a known-flake state (e.g. staging Zitadel
  briefly down) AND a real production hotfix needs to ship,
- **When** an operator triggers the deploy job via
  `workflow_dispatch` with `--force-deploy=true`,
- **Then** the deploy runs, the smoke result is logged but ignored
  for gating purposes, AND the override is recorded in a structlog
  event `smoke_gate_override_used` for audit purposes.

**Test:** manual phase-4 verification.

## AC-7 — Nightly full-suite catches what the per-merge subset misses

- **Given** the nightly workflow runs at 03:00 UTC against staging,
- **When** any of the 14 journeys from SPEC-TEST-E2E-001 returns
  `fail` or `warn`,
- **Then** a Slack message posts to `#klai-ops-alerts` with the
  failing journey name and a link to the run; the next morning's
  on-call sees it before user impact.

**Test:** the existing observability infrastructure; verified by
manually failing one journey in staging on a chosen night.

## Run Acceptance Aggregate

The SPEC is **acceptable** when:

- All 7 ACs pass for the first 30 days post-flip.
- The 2026-04-29 dual outage shape (PR #230 callback allowlist + PR
  #231 mailer redis URL) is reproducibly caught by the gate.
- Median smoke runtime ≤ 90s; P99 ≤ 3 minutes; hard timeout 5
  minutes.
- Cost ≤ $20/month at 30 smokes/day.
- Zero production false-positives in 30 days.
