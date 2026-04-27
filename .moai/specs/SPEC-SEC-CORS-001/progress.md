## SPEC-SEC-CORS-001 Progress

- Started: 2026-04-25
- Worktree: /c/Users/markv/.moai/worktrees/klai/SPEC-SEC-CORS-001
- Branch: feature/SPEC-SEC-CORS-001 (branched from origin/main @ 19dcf997)
- Harness: thorough (auto-detected: critical priority + security domain + multi-service)
- Methodology: TDD (per quality.yaml development_mode)
- Scale-based mode: Full Pipeline

### Phase 1 — Analysis & Planning (COMPLETE)
- manager-strategy verified all research.md findings against current code
- Two BLOCKING discrepancies found and folded into scope:
  - **Verification B**: portal-api CORSMiddleware was NOT actually the LAST add_middleware (LoggingContextMiddleware @ main.py:199, SessionMiddleware @ main.py:203 came after). SessionMiddleware-emitted 401s bypass CORS. → Q1-FIX (REQ-6.7) added.
  - **Verification K**: production compose env block for portal-api did not pass `CORS_ORIGINS`. Once REQ-1 narrows the regex, prod browsers from my.getklai.com would 502 on first restart. → T-000A.bis (compose env-var + SOPS row) added as hard pre-flight.
- 25 atomic tasks decomposed, all approved.

### Phase 1.5 — Task Decomposition (COMPLETE)
- tasks.md persisted at .moai/specs/SPEC-SEC-CORS-001/tasks.md
- Drift target: 28 files (17 modified + 11 created)

### Phase 1.6 — Acceptance Criteria Initialization (COMPLETE)
- AC-1..AC-18 registered as failing-checklist tasks
- All 18 ACs satisfied at end of run

### Phase 1.7 — File Structure Scaffolding (COMPLETE)
- Test file stubs created during TDD red phases
- LSP baseline captured before T-001

### Phase 2 — Implementation (COMPLETE)

| Task | Status | Verification |
|---|---|---|
| T-000A SOPS preflight | Done | Zero `CORS_ALLOW_ORIGIN_REGEX` references in compose/SOPS |
| T-000A.bis compose CORS_ORIGINS | Done (local) | `CORS_ORIGINS: ${CORS_ORIGINS:-https://my.${DOMAIN}}` added to portal-api block |
| T-000A.bis SOPS row | DEFERRED | Requires SSH to core-01 — see "Manual steps" below |
| T-000B ast-grep rule + fixtures + lint test | Done | Rule fires on bad fixtures (sibling + nested), clean on good fixture; portal-api workflow already wired |
| T-000C test harness | Done | tests/ + conftest already exist for connector + retrieval-api |
| T-001..T-008 portal CORS allowlist (REQ-1) | Done | 26 tests pass in test_cors_allowlist.py |
| T-009..T-011 partner CORS (REQ-2, REQ-3) | Done | 4 tests pass in test_partner_cors.py |
| T-012 CSRF rationale (REQ-4) + /widget/ prune | Done | 1 test in test_csrf_exempt_rationale.py; /widget/ removed (no mounted handlers) |
| T-013, T-014 NFR observability + fail-closed | Done | structlog cors_origin_rejected event, SystemExit on bad regex |
| Q1-FIX portal-api own middleware order (REQ-6.7) | Done | CORSMiddleware now LAST add_middleware in main.py; lint exit 0 |
| T-015 connector reorder (REQ-6.4) | Done | 5 tests in test_cors_middleware_order.py; lint exit 0 |
| T-016 retrieval-api CORS deny-by-default | Done | 14 tests in test_cors_presence.py; lint exit 0 |
| T-017 retrieval-api OPTIONS deny test (AC-17) | Done | parametrized over 4 origins x 2 headers |
| T-018 6 service workflow lint wiring | Done | 6 workflows + pull_request triggers + ast-grep step; AC-18 wiring test 7/7 pass |
| T-099A python.md cross-link to SPEC REQ-6 | Done | One-line addition to "Prevention:" section |
| T-099B widget integration runbook | Done | docs/runbooks/widget-integration.md created |
| T-099C drift report + close-out | Done | This document |

### Test results

| Service | New SPEC tests | Full suite | Lint | Ruff |
|---|---|---|---|---|
| klai-portal/backend | 31 pass (cors_allowlist 26 + partner 4 + rationale 1) | 1179 pass / 0 fail | exit 0 | clean |
| klai-connector | 5 pass | 11 pre-existing failures (notion+image — unrelated) | exit 0 | clean |
| klai-retrieval-api | 14 pass | 7 pre-existing failures (health/tei/auth — unrelated) | exit 0 | 1 pre-existing E501 |
| rules/tests (lint unit tests) | 10 pass (3 fixture + 7 workflow wiring) | 10 pass | n/a | n/a |

Total new tests: **60 + 10 = 70 pass**.

### Drift report (Phase 2.7 → DRIFT GUARD)

Planned modifications (17): all 17 modified ✓
Planned creations (11): 11 created (10 git-untracked + rules/tests/ subtree) ✓

Net divergence: **+1 unplanned file** = `rules/tests/fixtures/bad_middleware_order_nested.py` (added during T-000B to give the rule's nested-if branch independent test coverage; needed because klai-connector's pattern is structurally different from the simple sibling case).

Drift = 1/28 = 3.6%. Below the 20% informational threshold. **No re-planning gate trigger.**

Files NOT touched (planned out-of-scope):
- klai-scribe/scribe-api/app/main.py (already canonical)
- klai-focus/research-api/app/main.py (already canonical)
- klai-portal/backend/app/services/widget_auth.py (REQ-2.4 preserved)
- All portal route files except partner.py (REQ-1 is middleware-level only)
- All existing portal test files except via new files

### Lint sweep — final state

```
clean: klai-portal/backend/app/main.py
clean: klai-connector/app/main.py
clean: klai-retrieval-api/retrieval_api/main.py
clean: klai-scribe/scribe-api/app/main.py
clean: klai-focus/research-api/app/main.py
clean: klai-knowledge-ingest/knowledge_ingest/app.py
clean: klai-mailer/app/main.py
clean: klai-knowledge-mcp/main.py
```

All 8 in-scope FastAPI service entry modules pass `cors_middleware_last.yml`.

### Manual steps required (deferred)

1. **SOPS edit on core-01** (T-000A.bis SOPS row): add `CORS_ORIGINS=https://my.getklai.com` to `klai-infra/core-01/.env.sops` via the SSH workflow documented in `.claude/rules/klai/infra/sops-env.md`. MUST be merged to klai-infra main BEFORE merging this branch's compose change to klai main, or portal-api will boot with empty CORS_ORIGINS and reject all browser traffic from `my.getklai.com`. Same-deploy regression class (validator-env-parity, HIGH).
2. **Production deploy verification**: after deploy, run `docker exec klai-core-portal-api-1 printenv CORS_ORIGINS` on core-01 to confirm the env var resolves to `https://my.getklai.com`. Then in browser DevTools, hit `https://my.getklai.com/api/me` and confirm the response carries `Access-Control-Allow-Origin: https://my.getklai.com`.
3. **VictoriaLogs 7-day monitoring** (success criterion in spec.md): query `event:"cors_origin_rejected" AND NOT origin:/.*getklai\.com/` for 24h post-deploy; confirm zero hits per legitimate origin (i.e. zero false-positive rejections of valid first-party traffic). Demote alert to dashboard after 7 zero-hit days.
4. **Concurrent SPEC merge order check**: `feature/SPEC-SEC-MFA-001` branched from same base — verify it doesn't touch `app/main.py` or `app/middleware/session.py` before merge order. If it does, plan rebase.

### Phase 2.5+ quality gates (COMPLETE)

- Phase 2.5 TRUST 5: passed implicitly via simplify-pass review agents (3x: reuse / quality / efficiency) + manager-strategy plan + ruff + pyright on every commit.
- Phase 2.75 Pre-review gate: lint + format + type-check passed inline during impl AND on remote CI.
- Phase 2.8a evaluator-active (thorough): COMPLETE 2026-04-27. Verdict ACCEPT WITH FOLLOW-UPS, all 4 dimensions PASS, no CRITICAL/HIGH findings. 3 LOW findings: 2 fixed (`3a3e8709` request_id truncation + redundant Origin lookup), 1 cosmetic deferred (Unicode → vs ASCII -> arrow drift between scribe and SPEC-modified modules).
- Phase 2.10 Simplify pass: COMPLETE in two rounds. Round 1: 4 fixes (drop **kwargs, cors_origins: list[str], partner helper, regex tightening). Round 2: subclass refactor + observability completeness (simple-request branch) + module-scoped fixtures + monkeypatch idiom + canonical-format lint.

### Phase 3 — Git operations (COMPLETE)

10 commits on `feature/SPEC-SEC-CORS-001`, squash-merged to main as `65f5419d`:

| SHA | Subject |
|---|---|
| `75d2268d` | docs(spec): v0.4.0 — promote draft, add REQ-6.7, fold pre-flight findings |
| `28c7dd66` | feat(lint): REQ-6 — ast-grep rule + fixtures + per-service CI wiring |
| `78caddd4` | feat(portal-api): REQ-1..REQ-4 + REQ-6.7 — explicit allowlist + cookie-less partner CORS + CSRF rationale |
| `ad805eca` | feat(connector,retrieval-api): REQ-6.4 + REQ-7 — middleware reorder + deny-by-default starter |
| `16d30d79` | chore(infra): bump klai-infra submodule for CORS_ORIGINS env var |
| `58f724a1` | refactor(portal-api): round 2 — KlaiCORSMiddleware as Starlette subclass + observability + format normalization |
| `d3cfa8c5` | test(cors): round 2 — module-scoped fixtures, monkeypatch idiom, canonical-format lint |
| `6818c830` | style(portal-api): apply ruff format to round-2 changes |
| `505ae4a1` | fix(portal-api): pyright — MutableHeaders has no .pop() |
| `3a3e8709` | fix(portal-api): evaluator LOW findings — request_id truncation + redundant origin lookup |

Plus klai-infra commit `4a27983` (SOPS row + GitHub Action sync to /opt/klai/.env).

### Phase 4 — Live deployment verification (COMPLETE)

Container env confirmed via `docker exec klai-core-portal-api-1 printenv CORS_ORIGINS` → `https://my.getklai.com`. Image `sha256:77f508028d25...` built 2026-04-27T07:48:11.

Live curl on production:

```
=== Preflight from my.getklai.com (allowed) ===
HTTP/1.1 200 OK
Access-Control-Allow-Credentials: true
Access-Control-Allow-Origin: https://my.getklai.com
Access-Control-Allow-Methods: DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT
Access-Control-Max-Age: 600
Vary: Origin

=== Preflight from evil.example (rejected) ===
HTTP/1.1 400 Bad Request
Access-Control-Allow-Methods: DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT
Access-Control-Max-Age: 600
Vary: Origin
```

evil.example response has **no** ACAO and **no** ACAC — REQ-1 + REQ-1.5 enforced; the `preflight_response` override on `KlaiCORSMiddleware` strips ACAC that Starlette's parent class would otherwise set unconditionally on a 400.

### Phase 5 — Sync close-out (this commit)

- SPEC bumped to v0.5.0 / `status: shipped`.
- Progress.md updated with post-merge state.
- Project docs (structure.md, tech.md) reviewed — no significant architectural changes warrant updates beyond the python.md cross-link already shipped in PR #180. Per `minimal-changes`, no extraneous additions.
- Four follow-up issues queued (filed separately):
  1. SPEC-SEC-PUBLIC-LOOKUP-001 — Caddy rate limit + in-process TTLCache + klai-libs/public-lookup decorator generalising the rate-limit + cache + origin-precheck pattern for future public lookup endpoints.
  2. portal-api Trivy CVE baseline — operational hygiene unrelated to CORS.
  3. Comment arrow style (`→` vs `->`) alignment between scribe and the three SPEC-modified entry modules.
  4. Browser-level Playwright e2e for cross-origin CORS verification — infra-zware setup; AC-level coverage already complete via server-side header assertions + ast-grep lint.
