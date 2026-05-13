# Sync Report — SPEC-PORTAL-BILLING-CLEANUP-001

- Date: 2026-05-13
- Main commit: `82ea28c9`
- PR: #640 (`refactor(portal): split admin billing route`)

## Status

- SPEC status: `implemented`
- Deployment: `Build and deploy portal-frontend` succeeded on `main`.
- Semgrep: succeeded for the billing-cleanup main commit.

## Verification

- `npm run build`
- `npm run lint`
- `npm run test` (37 files, 260 tests)
- Voys Google-SSO Playwright smoke against `https://voys.getklai.com/admin/billing`

## Notes

- The repository prod-tenant CI workflow for isolated bot login is not a valid Voys Google-SSO check; it failed due missing `E2E_USER_EMAIL`/bot-login environment in CI.
- The actual Voys check used the captured Google-SSO storage state and validated the live billing page without submitting the Moneybird mandate form.
