## Task Decomposition
SPEC: SPEC-SEC-CORS-001
Generated: 2026-04-25 (Phase 1.5)
Harness: thorough
Methodology: TDD (RED-GREEN-REFACTOR per AC)
Worktree: /c/Users/markv/.moai/worktrees/klai/SPEC-SEC-CORS-001
Branch: feature/SPEC-SEC-CORS-001

| Task ID | Description | REQ | AC | Dependencies | Planned Files | Status |
|---|---|---|---|---|---|---|
| T-000A | SOPS pre-flight + compose env-var: verify zero `CORS_ALLOW_ORIGIN_REGEX` references; add `CORS_ORIGINS: ${CORS_ORIGINS:-https://my.getklai.com}` to portal-api compose env block; add `CORS_ORIGINS=https://my.getklai.com` to klai-infra/core-01/.env.sops via SSH SOPS workflow. | REQ-1.6 | n/a (pre-flight) | none | deploy/docker-compose.yml, klai-infra/core-01/.env.sops | pending |
| T-000B | ast-grep rule scaffolding: rules/cors_middleware_last.yml + good/bad fixtures + pytest invocation + portal-api workflow lint step (initial wiring). | REQ-6.2 | AC-18 | T-000A | rules/cors_middleware_last.yml, rules/tests/test_cors_middleware_last_lint.py, rules/tests/fixtures/good_middleware_order.py, rules/tests/fixtures/bad_middleware_order.py, .github/workflows/portal-api.yml | pending |
| T-000C | Test harness scaffolding: empty conftest.py and __init__.py for klai-connector/tests and klai-retrieval-api/tests. | n/a | n/a | none | klai-connector/tests/__init__.py, klai-connector/tests/conftest.py, klai-retrieval-api/tests/__init__.py, klai-retrieval-api/tests/conftest.py | pending |
| T-001 | RED+GREEN: test_cors_blocks_evil_origin_on_api_me + REQ-1 impl (remove cors_allow_origin_regex field; module-level fixed regex `^https://([a-z0-9][a-z0-9-]*\.)?getklai\.com$`). | REQ-1.1, REQ-1.5, REQ-1.6 | AC-1 | T-000A, T-000B, T-000C | klai-portal/backend/app/core/config.py, klai-portal/backend/app/main.py, klai-portal/backend/.env.example, klai-portal/backend/tests/test_cors_allowlist.py | pending |
| T-002 | RED+GREEN: test_cors_blocks_evil_origin_on_auth_login_preflight. | REQ-1.1 | AC-2 | T-001 | klai-portal/backend/tests/test_cors_allowlist.py | pending |
| T-003 | RED+GREEN: test_cors_allows_first_party_on_api_me (https://my.getklai.com). | REQ-1.2, REQ-1.5 | AC-3 | T-001 | klai-portal/backend/tests/test_cors_allowlist.py | pending |
| T-004 | RED+GREEN: test_cors_allows_tenant_subdomain (acme accepted, evil.my multi-label rejected). | REQ-1.2 | AC-4 | T-001 | klai-portal/backend/tests/test_cors_allowlist.py | pending |
| T-005 | RED+GREEN: test_cors_rejects_plaintext_http_getklai. | REQ-1.2 | AC-5 | T-001 | klai-portal/backend/tests/test_cors_allowlist.py | pending |
| T-006 | RED+GREEN: test_cors_allows_dev_origin_localhost_5174. | REQ-1.2 | AC-6 | T-001 | klai-portal/backend/tests/test_cors_allowlist.py | pending |
| T-007 | RED+GREEN: test_cors_no_unlisted_origin_echo (table test paths × attacker origins). | REQ-1 (group) | AC-7 | T-001 | klai-portal/backend/tests/test_cors_allowlist.py | pending |
| T-008 | RED+GREEN: test_acac_never_with_wildcard_origin invariant scanner. | REQ-1.5 | AC-8 | T-007 | klai-portal/backend/tests/test_cors_allowlist.py | pending |
| T-009 | RED+GREEN: REQ-2 partner CORS — second router-level Starlette middleware for /partner/v1/*; remove ACAC headers in partner.py:470-474 + 508-515; test_partner_cors_widget_origin_no_credentials. | REQ-2.1, REQ-2.2, REQ-2.3 | AC-9 | T-001, T-008 | klai-portal/backend/app/main.py, klai-portal/backend/app/api/partner.py, klai-portal/backend/tests/test_partner_cors.py | pending |
| T-010 | RED+GREEN: test_partner_cors_blocks_unlisted_origin (403 + no ACAO). | REQ-2.1 | AC-10 | T-009 | klai-portal/backend/tests/test_partner_cors.py | pending |
| T-011 | RED+GREEN: test_bff_cookie_rejected_on_partner_endpoint. | REQ-3.1 | AC-11 | T-009 | klai-portal/backend/tests/test_partner_cors.py | pending |
| T-012 | RED+GREEN: REQ-4 + test_csrf_exempt_prefixes_have_rationale. Add inline rationale comment block to each entry of _CSRF_EXEMPT_PREFIXES; AST-parse-based test verifies. Audit /widget/ — delete if no mounted handlers. | REQ-4.1, REQ-4.2, REQ-4.3, REQ-4.4, REQ-4.5 | AC-12 | T-008 | klai-portal/backend/app/middleware/session.py, klai-portal/backend/tests/test_csrf_exempt_rationale.py | pending |
| T-013 | RED+GREEN: REQ-1 NFR observability — `event="cors_origin_rejected"` structlog entry on every preflight where origin doesn't match. Implement OriginObservabilityMiddleware. | REQ-1 NFR | AC-13 | T-001, T-008 | klai-portal/backend/app/main.py, klai-portal/backend/tests/test_cors_allowlist.py | pending |
| T-014 | RED+GREEN: REQ-1 fail-closed startup + test_cors_regex_compile_failure. | REQ-1 NFR | AC-14 | T-001 | klai-portal/backend/app/main.py, klai-portal/backend/tests/test_cors_allowlist.py | pending |
| Q1-FIX | Move portal-api CORSMiddleware to be the LAST add_middleware call (extend REQ-6.4 to portal-api). 3-line move + comment update. | REQ-6.7 (added) | AC-1..AC-13 (re-verified) | T-001..T-014 | klai-portal/backend/app/main.py | pending |
| T-015 | RED+GREEN: REQ-6.4 connector reorder (CORS → Auth → RequestContext → CORS) + test_connector_401_carries_cors_headers (AC-15). | REQ-6.4, REQ-6.6 | AC-15 | T-000C, Q1-FIX | klai-connector/app/main.py, klai-connector/tests/test_cors_middleware_order.py | pending |
| T-016 | RED+GREEN: REQ-7.1 + REQ-7.2 retrieval-api CORSMiddleware deny-by-default (allow_origins=[], allow_credentials=False, etc.) as LAST add_middleware. test_cors_middleware_present_and_last via app.user_middleware introspection. | REQ-7.1, REQ-7.2 | AC-16 | T-000C | klai-retrieval-api/retrieval_api/main.py, klai-retrieval-api/tests/test_cors_presence.py | pending |
| T-017 | RED+GREEN: test_retrieval_api_cors_deny_by_default (OPTIONS /retrieve from various origins → no ACAO). | REQ-7.2, REQ-7.4 | AC-17 | T-016 | klai-retrieval-api/tests/test_cors_presence.py | pending |
| T-018 | REQ-6.2/REQ-6.3 lint wiring per service: ast-grep step in 6 service workflows (klai-connector, retrieval-api, scribe-api, knowledge-ingest, klai-mailer, klai-knowledge-mcp). Extend lint-rule pytest with CI-wiring assertion. | REQ-6.2, REQ-6.3 | AC-18 | T-000B, T-015, T-016 | .github/workflows/klai-connector.yml, .github/workflows/retrieval-api.yml, .github/workflows/scribe-api.yml, .github/workflows/knowledge-ingest.yml, .github/workflows/klai-mailer.yml, .github/workflows/klai-knowledge-mcp.yml, rules/tests/test_cors_middleware_last_lint.py | pending |
| T-099A | Cross-link .claude/rules/klai/lang/python.md "Starlette middleware registration order" → SPEC-SEC-CORS-001 REQ-6 + lint enforcement note. | REQ-6.5 | n/a (doc) | T-018 | .claude/rules/klai/lang/python.md | pending |
| T-099B | Widget integration runbook update: `credentials: 'omit'` mandate per REQ-3.3. | REQ-3.3 | n/a (doc) | T-009 | docs/runbooks/widget-integration.md (or equivalent) | pending |
| T-099C | Drift report + close-out: git diff --stat against planned files; verify zero unplanned files; document VictoriaLogs 7-day monitoring task in retro. | n/a (close-out) | n/a (verification) | T-099A, T-099B | n/a | pending |

**Total**: 25 tasks. Drift target: 28 files (17 modified + 11 created).
