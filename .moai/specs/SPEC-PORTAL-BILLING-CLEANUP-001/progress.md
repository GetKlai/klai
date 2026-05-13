## SPEC-PORTAL-BILLING-CLEANUP-001 Progress

- Started: 2026-05-13
- Dependency check: `SPEC-PORTAL-PRICING-PER-USER-001` is `shipped`, so the concurrent-work guard is clear.
- CodeIndex impact checked for `billing.lazy.tsx` and `_billing-types.ts`; both reported LOW risk / no upstream dependents.
- Implemented: `billing.lazy.tsx` reduced to a route shell with reducer-backed billing status transitions.
- Implemented: extracted billing helpers and section components:
  - `klai-portal/frontend/src/routes/admin/-_billing-types.ts`
  - `klai-portal/frontend/src/routes/admin/-_billing-helpers.ts`
  - `klai-portal/frontend/src/routes/admin/_components/-BillingActiveSection.tsx`
  - `klai-portal/frontend/src/routes/admin/_components/-BillingBreakdownSection.tsx`
  - `klai-portal/frontend/src/routes/admin/_components/-BillingField.tsx`
  - `klai-portal/frontend/src/routes/admin/_components/-BillingMandateSection.tsx`
  - `klai-portal/frontend/src/routes/admin/_components/-BillingStatusCards.tsx`
- Preserved: `Promise.allSettled` breakdown/status fetch path and `adminLogger.error` / `adminLogger.warn` behavior from the prior hotfix.
- Verified: `npm run build`, `npm run lint`, `npm run test`.
