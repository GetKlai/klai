## SPEC-SEC-MFA-001 Progress

- Started: 2026-04-25
- Branch: feature/SPEC-SEC-MFA-001 (worktree at ~/.moai/worktrees/klai/SPEC-SEC-MFA-001)
- Base: origin/main @ 19dcf997 (Merge PR #175)
- Mode: TDD (per quality.yaml development_mode)
- Harness level: thorough (security domain + auth keyword)
- Scale-based mode: Standard (≈8 files, 3 domains: backend/tests/infra/docs)

### Phase log

- Phase 0.9 (JIT language detection): Python 3.13 (klai-portal/backend pyproject.toml)
- Phase 0.95 (mode select): Standard Mode — multi-domain feature, sub-agent solo
- Phase 1 (strategy): Approved by user 2026-04-25
  - Research validated against live code: 3 fail-open holes intact at expected line ranges
  - Adjustment 1: respx not in pyproject.toml (added respx>=0.22 to dev deps)
  - Adjustment 2: klai-infra is a git submodule but Grafana provisioning lives in
    deploy/grafana/provisioning/alerting/ in the SUPERPROJECT; no submodule pin bump
    required after all (SPEC research path was incorrect)
  - Adjustment 3: structlog `_slog` already in auth.py; reused for new event emission
- Phase 1.5 / 1.6 (task decomposition + acceptance-as-tasks): see tasks.md
- Phase 1.7 (file scaffolding): not applicable; modifies existing files
- Phase 1.8 (MX context scan): no @MX:ANCHOR or @MX:WARN in target files
- Phase 2 (TDD RED-GREEN-REFACTOR):
  - RED: 9 base scenarios written to tests/test_auth_mfa_fail_closed.py;
    pytest 5 failed / 4 passed against current code (expected RED state)
  - GREEN: refactored login() with split try, _resolve_and_enforce_mfa helper,
    _emit_mfa_check_failed event, _mfa_unavailable 503 helper. Added hashlib import.
    All 9 scenarios + 4 retained existing tests passing.
  - Run-phase additions: 3 explicit acceptance Run-phase additions added
    (REQ-1.6 generic Exception, REQ-2.2 RequestError, REQ-3.4 recommended)
  - Total fail-closed scenarios: 12 / Total auth tests: 22 / Backend total: 1156 passed
- Phase 2.5 (TRUST 5):
  - Tested: 12 new + 4 retained MFA tests + 1156 backend tests, no regressions
  - Readable: SPEC-mapped docstrings on each helper, inline REQ-references
  - Unified: matches existing structlog / httpx / audit / event patterns
  - Secured: email sha256-hashed (REQ-4.3), 503 before cookie set (REQ-1.5)
  - Trackable: structured events with auto-bound request_id via LoggingContextMiddleware
- Phase 2.75 / Quality gate:
  - ruff check: clean (3 UP037 auto-fixed)
  - pyright: 0 errors / 0 warnings on auth.py
- Phase 2.8 (review): self-review per workflow-modes.md Pre-submission Self-Review
  - Helper extraction proportional; no over-engineering
  - YAGNI honored: no speculative configuration knobs added
  - SPEC-only changes; no unrelated improvements
- Coverage: 64% overall on app.api.auth (pre-existing — many other endpoints
  not in scope per minimal-changes pitfall). MFA enforcement block (helpers
  lines ~233-380 + login refactor section) has full branch coverage.
- Phase 2.10 (simplify): inline self-review pass; no structural simplifications
  necessary beyond refactor already done

### Files changed

- klai-portal/backend/app/api/auth.py — refactored login() + 3 new helpers
- klai-portal/backend/pyproject.toml — added respx>=0.22 to dev deps
- klai-portal/backend/tests/test_auth_mfa_fail_closed.py — NEW, 12 scenarios
- klai-portal/backend/tests/test_auth_security.py — REQ-5.3 delete, REQ-5.4 narrow
- deploy/grafana/provisioning/alerting/portal-mfa-rules.yaml — NEW, 2 alerts
- docs/runbooks/mfa-check-failed.md — NEW
- .moai/specs/SPEC-SEC-MFA-001/{progress.md,tasks.md} — workflow tracking

### Polish pass (2026-04-27)

After the initial commit (623e4aa6) a self-review surfaced two nits + one
documentation gap; all addressed in a follow-up commit on the same branch:

1. **Logger consistency** — `has_totp_check_failed` warning switched from
   stdlib `logger.warning` to `_slog.warning` to honour
   `.claude/rules/klai/projects/portal-logging-py.md`'s "structlog for new
   log statements" rule.
2. **Orphan PortalOrg FK** — added explicit branch in
   `_resolve_and_enforce_mfa` for `portal_user is not None and org is None`
   (the org row was deleted/soft-deleted while the FK still pointed at it).
   Pre-existing behaviour silently fell back to `mfa_policy="optional"`;
   we keep fail-open semantics but emit `mfa_check_failed` warning so the
   data-integrity bug is observable in Grafana.
3. **Coverage gap (REQ-5.6)** — explicitly documented as a deferred
   follow-up below; no code change.

New test added: `test_portal_user_orphan_org_proceeds_documented_fail_open`.

### Known limitations / deferred

- **Coverage gap on auth.py overall (64% vs SPEC's 85% target)**: The gap is
  caused by other endpoints in auth.py (TOTP setup, IDP intent, password
  reset, sso_complete) that have partial test coverage in unrelated test
  modules. Closing the gap requires testing untouched endpoints — out of
  scope for SPEC-SEC-MFA-001 per the `minimal-changes` pitfall. The MFA
  enforcement block itself (the SPEC's actual concern) has full branch
  coverage. Recommended follow-up: track in a separate SPEC for `auth.py`
  coverage hardening (TOTP setup, IDP intent, password reset, sso_complete).
- **Submodule pin bump (T-013)**: not required — Grafana provisioning is in
  the superproject, not in klai-infra.

### Verification

- pytest tests/test_auth_mfa_fail_closed.py tests/test_auth_security.py: 23/23 passed
- pytest (full backend): 1160 passed
- uv run ruff check app/api/auth.py tests/...: clean
- uv run --with pyright pyright app/api/auth.py: 0/0/0
