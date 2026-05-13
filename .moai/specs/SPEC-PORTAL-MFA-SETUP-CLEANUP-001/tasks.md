## Task Decomposition
SPEC: SPEC-PORTAL-MFA-SETUP-CLEANUP-001

| Task ID | Description | Requirement | Dependencies | Planned Files | Status |
|---------|-------------|-------------|--------------|---------------|--------|
| T-001 | Map the implicit MFA setup flags into explicit state transitions | ANALYZE state-transition diagram | - | `.moai/specs/SPEC-PORTAL-MFA-SETUP-CLEANUP-001/progress.md` | completed |
| T-002 | Add reducer characterization tests for the preserved setup states | PRESERVE characterization coverage | T-001 | `klai-portal/frontend/src/routes/setup/__tests__/-mfa-state.test.ts` | completed |
| T-003 | Extract MFA setup state reducers and WebAuthn helpers | Refactor to reducer-backed state machine | T-002 | `klai-portal/frontend/src/routes/setup/_components/-mfa-state.ts`, `klai-portal/frontend/src/routes/setup/_components/-mfa-webauthn.ts` | completed |
| T-004 | Extract per-method setup UI components | Per-step sub-components in setup/_components | T-003 | `klai-portal/frontend/src/routes/setup/_components/-MfaMethodCard.tsx`, `klai-portal/frontend/src/routes/setup/_components/-PasskeySetup.tsx`, `klai-portal/frontend/src/routes/setup/_components/-EmailOTPSetup.tsx`, `klai-portal/frontend/src/routes/setup/_components/-TOTPSetup.tsx` | completed |
| T-005 | Reduce the route shell to auth, state-machine routing, and composition | Modify `mfa.lazy.tsx` route shell | T-003, T-004 | `klai-portal/frontend/src/routes/setup/mfa.lazy.tsx` | completed |
| T-006 | Add component smoke coverage for email OTP behavior preservation | Preserve visible email send/verify error behavior | T-005 | `klai-portal/frontend/src/routes/setup/__tests__/-EmailOTPSetup.test.tsx` | completed |
| T-007 | Run focused and frontend-wide validation | Security-critical behavior preservation | T-006 | `klai-portal/frontend` test/lint/typecheck commands | completed |
