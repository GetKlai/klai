# SPEC-CI-E2E-GATE-001 — Implementation Plan

## Approach

Stand up a dedicated **staging tenant** that mirrors production
topology (Caddy + portal-api + mailer + Zitadel + retrieval-api +
knowledge-ingest), wire it as the smoke target between the existing
`build-push` and `deploy` GitHub Actions jobs, and ship a 5-journey
Playwright smoke that runs in under 90 seconds. The smoke executes
against staging only — a hostname allowlist in the runner enforces the
"NEVER touch production" invariant.

The 14-journey full SPEC-TEST-E2E-001 runbook stays as the manual
interactive validation. This SPEC defines a SUBSET that becomes the
per-merge gate.

## Task Decomposition

### Phase 1 — Stand up staging tenant (Week 1)

| # | Task | Files / infra | Risk |
|---|---|---|---|
| 1 | Provision a fresh server (`staging-01`) — same Hetzner cloud spec as core-01 (CPX31 or equivalent) | new infra in `klai-infra/staging-01/` | Medium — DNS + cert setup is a one-shot operation, must coordinate `staging.getklai.com` cert |
| 2 | Bring up the same docker-compose stack on staging — postgres + redis + caddy + portal-api + mailer + zitadel + retrieval-api + knowledge-ingest | `klai-infra/staging-01/docker-compose.yml` | Medium — staging Zitadel is a fresh instance; no shared user data with prod |
| 3 | Create `ci-smoke@staging-getklai.com` service account in staging Zitadel with TOTP enabled. Store TOTP seed in GitHub Actions secret `STAGING_SMOKE_TOTP_SEED` | staging Zitadel + GitHub repo settings | High — service-account credentials in CI secrets are a real attack surface; mitigated by scope (only this workflow can read the secret) |
| 4 | Seed staging tenant with one known KB (`smoke-kb`), one known template (`smoke-template`), one active user above | new `klai-infra/staging-01/seed.sql` | Low — idempotent seed script, runnable from any operator workstation |

### Phase 2 — Smoke runner (Week 2)

| # | Task | Files | Risk |
|---|---|---|---|
| 5 | Write `tests/e2e/smoke.spec.ts` — Playwright Test in TypeScript covering the 5-journey subset | new directory `tests/e2e/` | Medium — first TS test in the repo; follow `klai-portal/frontend/` tooling conventions |
| 6 | Implement the hostname-allowlist guard at runner level — refuses any `page.goto(url)` where `urlparse(url).hostname` is not in the staging set | `tests/e2e/_lib/host_guard.ts` | High — this is the "NEVER touch prod" invariant; needs a mutation test that proves a deliberate prod-hostname attempt fails the run |
| 7 | Implement the test inbox poll for password-reset journey — Mailtrap.io free tier (decision deferred to operator) OR a dedicated mail server in staging-01 | `tests/e2e/_lib/inbox_poll.ts` | Medium — Mailtrap rate limits and API quirks; abstract the poll behind an interface so swap-in to a self-hosted alternative is one file |
| 8 | Implement the TOTP-code generator — pyotp-equivalent in TS (`otpauth` npm package) reading `STAGING_SMOKE_TOTP_SEED` | `tests/e2e/_lib/totp.ts` | Low — npm `otpauth` is well-established |

### Phase 3 — CI wiring (Week 3)

| # | Task | Files | Risk |
|---|---|---|---|
| 9 | New `.github/workflows/post-deploy-smoke.yml` triggered by `workflow_run` from each service's `Build and push *` workflow on completion | new workflow file | Medium — `workflow_run` triggers have a non-obvious timing model; must verify the smoke fires within 60s of build complete |
| 10 | Update each service's `deploy` job to gate on `smoke` workflow result via `needs:` cross-workflow dependency | every service's `.github/workflows/*.yml` | High — wrong wiring means production deploys are blocked by an unrelated smoke run; phase in service-by-service with manual override during the first week |
| 11 | Add nightly full-suite workflow `.github/workflows/e2e-nightly.yml` running the full SPEC-TEST-E2E-001 runbook subset on cron | new workflow file | Low — nightly is non-blocking, results post to Slack |

### Phase 4 — Soak + flip (Week 4)

| # | Task | Files | Risk |
|---|---|---|---|
| 12 | Run the smoke in `report-only` mode for 7 consecutive days. Track pass/warn/fail rate | observability dashboard | Low |
| 13 | After 7 days of zero unexplained failures, flip `deploy.needs: [smoke]` to blocking | every service's deploy job | High — first day after flipping, watch for any false-positive that would block a real deploy |
| 14 | Document the override path: a manual workflow-dispatch with `--force-deploy=true` for emergency deploys when the smoke is broken | `docs/runbooks/post-deploy-smoke-override.md` | Low — explicit operator action, audited via workflow logs |

## Files Affected

- New `klai-infra/staging-01/` — entire staging compose stack
- New `tests/e2e/` — TypeScript Playwright test directory
- New `.github/workflows/post-deploy-smoke.yml`
- New `.github/workflows/e2e-nightly.yml`
- 8 existing service workflows under `.github/workflows/` — each gets `deploy.needs: [smoke]`
- New `docs/runbooks/post-deploy-smoke-override.md` — emergency-bypass procedure
- New `klai-infra/staging-01/seed.sql` — staging-tenant seed data
- New GitHub Actions secrets — `STAGING_SMOKE_TOTP_SEED`, `STAGING_SMOKE_PASSWORD`, `MAILTRAP_API_TOKEN` (or equivalent)

## Technology Choices

- **Playwright Test (TypeScript)** over Pytest+Playwright — mirrors `klai-portal/frontend/` tooling; better test-runner UX; native parallelism.
- **Mailtrap.io free tier** for the test inbox — defer the self-hosted alternative until the smoke proves valuable. Free tier covers the projected ~30 smokes/day.
- **`otpauth` npm package** for TOTP code generation — battle-tested, no native deps.
- **`workflow_run` cross-workflow dispatch** for triggering smoke on build completion — avoids the alternative of bundling the smoke into every service's workflow file (8 copies of the same job).

## Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Smoke runs against production by accident (the catastrophic failure mode) | Hostname allowlist in `host_guard.ts` + a CI step that asserts the allowlist exists + a mutation test in the runner repo proving a deliberate prod-hostname call aborts the run |
| Service-account TOTP seed exposed via GitHub Actions secret leakage | Secret scoped to the smoke workflow only; rotated quarterly; service account has zero data — only a known-KB and known-template; cannot exfiltrate user data |
| Smoke flakiness blocks legitimate deploys | Phase-4 soak (7 days report-only) catches systemic flakes; `--force-deploy` workflow-dispatch provides emergency override |
| Staging tenant drifts from production over time | Quarterly review with diff against production docker-compose; staging seed script is the canonical "what staging looks like" |
| 90s budget exceeded as journeys grow | Hard timeout in the workflow + per-journey budget; nightly full-suite catches regressions in journeys we cut from the per-merge gate |

## Success Criteria

- A deliberate regression in the `_validate_callback_url` allowlist (the 2026-04-29 outage shape) is caught by the smoke gate within 90 seconds of the merge that introduces it. Reproduce: revert PR #230 in a feature branch, observe the smoke fire on the resulting build-push; the production deploy never runs.
- The 2026-04-29 mailer broken-redis-URL outage shape is caught the same way — Phase 1 of the rollout includes a regression test that reverts PR #231 and verifies the smoke catches it.
- Smoke median runtime ≤ 90 seconds. P99 ≤ 3 minutes. Hard timeout 5 minutes.
- Cost ≤ $20/month at 30 smokes/day median.
- Zero production false-positives in the first 30 days post-flip.

## Out of Scope

- Replacing the manual SPEC-TEST-E2E-001 runbook. The full 14-journey runbook stays as the interactive validation.
- Cross-browser, mobile, accessibility coverage — Chromium-only.
- Performance benchmarking — existence of a successful response is the assertion, not response-time SLOs.
- Backfill smoke for already-merged PRs — gate is forward-only.

## Decision Points to Resolve in Phase 1

1. Staging tenant infrastructure: dedicated `staging-01` server vs core-01 with namespace isolation.
2. Test inbox provider: Mailtrap.io vs self-hosted MailHog vs Gmail `+ci` filter.
3. Service-account TOTP seed rotation cadence and revocation playbook.
4. Flake-retry policy: how many retries before the gate is considered failing (suggest: 1 retry with a different wallclock, then fail).

## Phasing Recommendation

- **Week 1**: Phase 1 (staging tenant + service account)
- **Week 2**: Phase 2 (smoke runner + 1 journey: login bootstrap)
- **Week 3**: Phase 3 (CI wiring + remaining journeys + nightly)
- **Week 4**: Phase 4 (soak + flip)
