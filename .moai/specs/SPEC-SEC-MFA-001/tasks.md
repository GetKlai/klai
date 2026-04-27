## Task Decomposition
SPEC: SPEC-SEC-MFA-001
Mode: TDD (RED-GREEN-REFACTOR per task)

| Task ID | Description | Requirement | Dependencies | Planned Files | Status |
|---------|-------------|-------------|--------------|---------------|--------|
| T-001 | Add respx>=0.22 to dev dependency-group | REQ-5.1 | — | klai-portal/backend/pyproject.toml | done |
| T-002 | RED: tests/test_auth_mfa_fail_closed.py with respx Scenarios 1, 2, 6, 7, 7a, 8 (fail-closed paths) | REQ-1.1..1.7, 2.1..2.7, 3.2, 5.1, 5.2, 5.4, 5.7 | T-001 | klai-portal/backend/tests/test_auth_mfa_fail_closed.py | done |
| T-003 | RED: Scenarios 3, 4, 5 (regression + documented fail-open) added to same module | REQ-3.1, 3.6, 5.2(c,d,e), 5.5 | T-002 | klai-portal/backend/tests/test_auth_mfa_fail_closed.py | done |
| T-003a | Run-phase additions: REQ-1.6 (generic Exception), REQ-2.2 (RequestError on find_user_by_email), REQ-3.4 (recommended policy) | REQ-1.6, 2.2, 3.4 | T-006 | klai-portal/backend/tests/test_auth_mfa_fail_closed.py | done |
| T-004 | GREEN: split pre-auth try in auth.py::login (find_user_by_email vs has_totp) | REQ-2.1, 2.2, 2.3, 2.4, 2.5, 2.6 | T-002, T-003 | klai-portal/backend/app/api/auth.py | done |
| T-005 | GREEN: extract _emit_mfa_check_failed helper (structlog event with reason, mfa_policy, zitadel_status, email_hash, outcome, level routing) | REQ-4.1, 4.2, 4.3, 4.4 | T-004 | klai-portal/backend/app/api/auth.py | done |
| T-006 | GREEN: extract _resolve_and_enforce_mfa helper; replace user_has_mfa=True fallback with 503 raise; split DB-lookup fail-open vs portal_user-found-org-fetch-fails 503 | REQ-1.1..1.6, 3.1, 3.2, 3.4, 3.7 | T-005 | klai-portal/backend/app/api/auth.py | done |
| T-007 | Update test_auth_security.py: delete test_mfa_check_failure_defaults_to_pass; narrow test_mfa_policy_lookup_failure_defaults_to_optional docstring | REQ-5.3, 5.4 | T-006 | klai-portal/backend/tests/test_auth_security.py | done |
| T-008 | REFACTOR: pre-submission self-review (no structural simplification needed; ruff complexity passes) | (quality gate) | T-007 | klai-portal/backend/app/api/auth.py | done |
| T-009 | Quality gate: ruff check (clean), pyright (0/0/0), full backend pytest (1156 passed), coverage on MFA-block fully covered (overall 64% pre-existing) | REQ-5.6 | T-008 | — | done (coverage gap on overall auth.py is out of scope; MFA-block is fully covered) |
| T-010 | Inline simplify self-review on changed files (no structural changes warranted) | (run.md Phase 2.10) | T-009 | — | done |
| T-011 | Add Grafana alert YAML in deploy/grafana/provisioning/alerting/portal-mfa-rules.yaml (path corrected from SPEC; Grafana lives in superproject, not klai-infra) | REQ-4.5, 4.6, 4.7 | — | deploy/grafana/provisioning/alerting/portal-mfa-rules.yaml | done |
| T-012 | Add runbook docs/runbooks/mfa-check-failed.md | REQ-4.7 | T-011 | docs/runbooks/mfa-check-failed.md | done |
| T-013 | Bump klai-infra submodule pin | n/a | — | — | not needed (Grafana lives in superproject) |
| T-014 | Conventional commits + push + draft PR | (git_strategy personal) | T-009..T-012 | — | done (commit 623e4aa6 + polish commit, branch pushed) |
| T-015 | Polish pass: structlog for has_totp warning + orphan PortalOrg fail-open + visibility test | self-review | T-014 | klai-portal/backend/app/api/auth.py, klai-portal/backend/tests/test_auth_mfa_fail_closed.py, .moai/specs/SPEC-SEC-MFA-001/progress.md | done |

### Coverage map (acceptance.md → tasks)

- Scenario 1 (has_any_mfa 500 + required → 503): T-002, T-006
- Scenario 2 (find_user_by_email 500 → 503): T-002, T-004
- Scenario 3 (optional + has_any_mfa 500 → 200 fail-open): T-003, T-006
- Scenario 4 (happy MFA): T-003 (regression)
- Scenario 5 (happy no-MFA optional): T-003 (regression)
- Scenario 6 (404 → continues to 401): T-002, T-004
- Scenario 7 (portal_user found + org fetch raises → 503): T-002, T-006
- Scenario 7a (portal_user not found → fail-open 200): T-002, T-006
- Scenario 8 (RequestError → 503): T-002, T-006

### Out-of-test verification (Sync phase, not Run)

- Grafana alert rules load on staging
- LogsQL query returns expected schema
- Runbook reachable from alert annotation
- Manual code review against research §4 fail-open catalogue
