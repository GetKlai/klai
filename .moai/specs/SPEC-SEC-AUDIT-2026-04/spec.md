---
id: SPEC-SEC-AUDIT-2026-04
version: 0.7.0
status: draft
created: 2026-04-24
updated: 2026-04-29
author: Mark Vletter
priority: critical
type: tracker
---

# SPEC-SEC-AUDIT-2026-04: Security Audit Response Tracker

## HISTORY

### v0.7.0 (2026-04-29, late)
- SPEC-SEC-INTERNAL-001 fully shipped (#201): service-wide internal-secret
  surface hardening across portal-api / klai-mailer / klai-connector /
  klai-scribe / klai-knowledge-mcp + new shared library `klai-libs/log-utils/`.
  Closes findings #14, #18, A2, A3, A4 from the original Cornelis audit
  PLUS the 7 internal-wave findings catalogued on 2026-04-24.
  - **B0** new shared lib: 4-symbol public API, 29 tests, ruff + pyright strict.
  - **B1** portal-api: REQ-1.1 (taxonomy compare-digest), REQ-2 (SCAN/UNLINK
    replaces FLUSHALL), REQ-3 (BFF proxy header strip + regex catch-all),
    REQ-5 (rate-limit fail-mode setting, default closed), REQ-4 sweep on
    ~6 portal log sites that survived the AUTH-COVERAGE-001 structured-events
    refactor.
  - **B2** knowledge-mcp: REQ-8 Request-ID return contract (no body to chat UI),
    REQ-9.5 fail-closed startup, REQ-1.5 verify_shared_secret.
  - **B3** connector: REQ-9.3 Settings validators + runtime guards on
    `_headers()` / `__init__`, REQ-10 `sync_runs.error_details` sanitize.
  - **B4** scribe-api: REQ-9.4 validator, drop silent-omit guard, REQ-4
    sweep on transcription-service log path. Dockerfile rewritten to
    repo-root + uv pattern (the previous `uv pip install -r` flow did
    not read `[tool.uv.sources]`).
  - **B5** ast-grep cross-service: 4 rule files (LHS / RHS × == / !=)
    with `kind: identifier` constraint, wired into all 5 service CI
    workflows. Regression fixture at
    `.github/test-fixtures/sec-internal-001/regression.py`.
- Live status table updated: 12 of 13 tracked SPECs shipped (was 11 of 13).
- Estimate revised: 3-6 PRs remaining (was 4-8).
- Three new pitfalls captured in
  `.claude/rules/klai/pitfalls/process-rules.md`:
  - `astgrep-gitignore-shadowed-rules (HIGH)` — ast-grep silently
    respects `.gitignore` during rule-dir discovery; rule files matching
    `*-secret.*` etc. are dropped without an error message.
  - `uv-pip-install-skips-uv-sources (HIGH)` — `uv pip install -r pyproject.toml`
    does NOT read `[tool.uv.sources]`; switch the Dockerfile to
    `uv sync --frozen` to honour path-deps.
  - `parallel-spec-on-overlapping-log-sites (MED)` — when two SPECs
    rewrite the same log call sites, the rebase produces large
    conflicts; prefer the more-thorough version on resolution and
    coordinate scope before opening a wide log-sweep PR.

### v0.6.0 (2026-04-29)
- SPEC-SEC-SESSION-001 shipped: implementation (#197) deployed to core-01
  same day; close-out PR adds CHANGELOG entry, status promotion to
  `done`, and two new Grafana alerts (`session_sso_cookie_key_missing` CRIT,
  `session_totp_redis_unavailable` CRIT) at `portal-session-rules.yaml`.
  - Findings #13 (TOTP per-instance counter), #15 (`klai_idp_pending`
    no binding), #16 (`klai_sso` ephemeral-key fallback) closed.
  - 22 new tests; container restart on core-01 emitted zero
    `sso_cookie_key_missing_startup_abort` events; 0 errors on
    `service:portal-api` in the 20-minute post-deploy scan.
- Live status table updated: 11 of 13 tracked SPECs shipped (was 10 of 13).
- Estimate revised: 4-8 PRs remaining (was 6-10).

### v0.5.0 (2026-04-28, late)
- SPEC-SEC-AUTH-COVERAGE-001 fully shipped: implementation (#195) + alerts/runbook/CHANGELOG follow-through (#198), both deployed to core-01.
  - 14 in-scope auth.py endpoints (totp / passkey / email_otp / idp / password / sso_complete / verify_email) now emit structured `*_failed` events on every documented failure leg + `audit.log_event` on state-changing successes.
  - 16 distinct events covered by 2 new Grafana alerts (`auth_failure_rate_high` warning, `auth_zitadel_5xx_burst` critical) with triage runbook `docs/runbooks/auth-failure-burst.md`.
  - 74 new tests, 80% line coverage on `app.api.auth` (REQ-5.5 PARTIAL — 5% gap in shared helpers documented as follow-up scope, not blocking).
- Live status table updated: 10 of 13 tracked SPECs shipped (was 9 of 12 + 1 NEW queued).
- Estimate revised: 6-10 PRs remaining (was 8-12).

### v0.4.0 (2026-04-28)
- 4-day implementation sprint progressed to ~75% of original audit-response scope
- 9 of 12 original SPECs reach `shipped` or majority-shipped state (see Live Status)
- 4 SPECs still queued or in-flight: IDENTITY-ASSERT-001 Phase B residue
  (REQ-3 / REQ-4 / REQ-6), TENANT-001, SESSION-001, INTERNAL-001
- HYGIENE-001 split into 5 per-service slices: 2 shipped (scribe + retrieval),
  3 queued (connector, portal, knowledge-mcp)
- 1 new SPEC added during execution: SPEC-SEC-AUTH-COVERAGE-001 — derived
  from MFA-001 implementation review when reviewer noticed similar
  fail-closed gaps elsewhere in `auth.py`
- 1 prod incident captured: portal-api 502 from #150 because the Moneybird
  validator landed before its env var existed in SOPS — root cause now
  encoded as pitfall `validator-env-parity (HIGH)` in
  `.claude/rules/klai/pitfalls/process-rules.md`
- Estimate: 8-12 PRs remaining to fully close the audit response

### v0.3.0 (2026-04-24)
- All amendments promised in v0.2.0 have landed in the referenced SPECs
- 6 existing SPECs bumped to v0.3.0 with amendment scope integrated:
  SSRF-001, WEBHOOK-001, CORS-001, TENANT-001, INTERNAL-001, HYGIENE-001
- 3 new SPECs promoted from `stub` to `draft` v0.2.0 with full EARS + research + acceptance:
  IDENTITY-ASSERT-001, MAILER-INJECTION-001, ENVFILE-SCOPE-001
- All 12 remediation SPECs are now at plan-completeness — ready for `/moai run` delegation
- Post-expansion nuances captured in individual SPECs (e.g. MAILER-INJECTION-001 research.md
  documents that `str.format` RCE is latent rather than live in today's templates; HYGIENE-001
  carries an explicit "split permitted" clause since it absorbed 21 additional findings)

### v0.2.0 (2026-04-24)
- Extended with internal-audit wave on six more services (connector, retrieval-api, scribe,
  mailer, knowledge-mcp, focus/research-api) using the new `klai-security-audit` agent
- 63 additional findings surfaced across the internal wave, of which 10 add new CRITICAL
  / HIGH items not covered by the Cornelis-28
- 3 additional SPECs added (IDENTITY-ASSERT, MAILER-INJECTION, ENVFILE-SCOPE) for new
  patterns that do not fit existing remediation SPECs
- 4 existing SPECs receive amendments (see Amendments section)
- Service total now: Cornelis 28 + internal 63 = 91 findings, mapped to 12 remediation SPECs
  (plus one governance decision on klai-focus)

### v0.1.0 (2026-04-24)
- Master tracker created in response to the April 2026 adversarial security audit
  (Cornelis Poppema, 2026-04-22)
- Verified against codebase 2026-04-24 by Claude Opus — see audit response below
- 28 findings grouped into 9 focused SPECs (see breakdown)

---

## Purpose

This document is NOT an implementation SPEC. It is a tracker that links the 91 findings from
the security audits (external Cornelis audit + internal wave) to focused remediation SPECs.
Each finding is mapped to exactly one remediation SPEC, grouped by fix-locality (not by
severity) to minimise merge conflicts and enable parallel execution.

Implementation teams should read the linked sub-SPEC, not this tracker, when picking up work.

---

## Live status (2026-04-29, late)

| SPEC | Prio | Status | PRs |
|---|---|---|---|
| SPEC-SEC-WEBHOOK-001 | P0 | **shipped** (95% — REQ-5.4 + REQ-6 wrapper open as tail) | #155 #157 #159 #161 #150-revert #156 |
| SPEC-SEC-SSRF-001 | P0 | **shipped** | #167 |
| SPEC-SEC-CORS-001 | P0 | **shipped** | #180 + #185 close-out |
| SPEC-SEC-MAILER-INJECTION-001 | P0 | **shipped** | #168 + #173 mailer compose env |
| SPEC-SEC-IDENTITY-ASSERT-001 | P0 | **partial** (Phase A shipped #178; Phase B in flight #190 covers REQ-2 only — REQ-3/4/6 queued) | #178 + #190 (open) |
| SPEC-SEC-TENANT-001 | P1 | **queued** | — |
| SPEC-SEC-IMAP-001 | P1 | **shipped** | #165 #172 #174 #176 #177 |
| SPEC-SEC-MFA-001 | P1 | **shipped** | #181 + #189 db-failure-events refactor |
| SPEC-SEC-ENVFILE-SCOPE-001 | P1 | **shipped** | #163 + #170 (3-vars-dropped fix) + #171 close-out |
| SPEC-SEC-SESSION-001 | P2 | **shipped** | #197 + close-out (alerts/CHANGELOG) |
| SPEC-SEC-INTERNAL-001 | P2 | **shipped** | #201 + close-out (CHANGELOG / pitfalls / tracker) |
| SPEC-SEC-HYGIENE-001 | P3 | **partial** (scribe slice #179 + retrieval slice #188 open; connector / portal / knowledge-mcp slices queued) | #179 + #188 (open) |
| SPEC-SEC-AUTH-COVERAGE-001 | P0 | **shipped** | #184 plan + #186 v0.2 + #195 run + #198 alerts/runbook/CHANGELOG |

**Implementation rate:** ~31 PRs merged in 6 days (audit-response only).
**Remaining:** ~3-6 PRs to close the last fully-queued original (TENANT-001) + IDENTITY-ASSERT-001 residue + HYGIENE-001 slices.

---

## Source artifacts

- External audit: `SECURITY.md` by Cornelis Poppema, 2026-04-22 (28 findings)
- Internal-wave audit: 2026-04-24 via `klai-security-audit` agent on klai-connector,
  klai-retrieval-api, klai-scribe, klai-mailer, klai-knowledge-mcp, klai-focus/research-api
  (63 findings)
- Verification pass on Cornelis-28: Claude Opus, 2026-04-24 (25 VERIFIED, 2 PARTIAL, 1 REFUTED
  + 4 of 5 appendix paths VERIFIED)
- Long-term audit automation: new `klai-security-audit` agent at
  `.claude/agents/klai/security-audit.md` encodes the review lenses that surfaced these findings

---

## Finding index

### P0 — Fix this week

| # | Finding (audit title) | Verdict | SPEC | File reference |
|---|---|---|---|---|
| 6 | SSRF in preview_crawl | VERIFIED | [SPEC-SEC-SSRF-001](../SPEC-SEC-SSRF-001/spec.md) | [crawl.py:125-223](../../../klai-knowledge-ingest/knowledge_ingest/routes/crawl.py#L125) |
| 7 | SSRF via persisted web_crawler connector | VERIFIED | SPEC-SEC-SSRF-001 | [connectors.py:81-149](../../../klai-portal/backend/app/api/connectors.py#L81) |
| 8 | validate_url DNS rebinding TOCTOU | VERIFIED | SPEC-SEC-SSRF-001 | [url_validator.py:34-68](../../../klai-knowledge-ingest/knowledge_ingest/utils/url_validator.py#L34) |
| A1 | SSRF → docker-socket-proxy env dump | VERIFIED (chain) | SPEC-SEC-SSRF-001 | (chain of #6+#7 + [docker-compose.yml:295-306](../../../deploy/docker-compose.yml#L295)) |
| 2 | Vexa webhook IP-bypass | VERIFIED | [SPEC-SEC-WEBHOOK-001](../SPEC-SEC-WEBHOOK-001/spec.md) | [meetings.py:46-58](../../../klai-portal/backend/app/api/meetings.py#L46) |
| 3 | Moneybird webhook fail-open on empty secret | VERIFIED | SPEC-SEC-WEBHOOK-001 | [webhooks.py:24-79](../../../klai-portal/backend/app/api/webhooks.py#L24) |
| 4 | Moneybird webhook non-constant-time compare | VERIFIED | SPEC-SEC-WEBHOOK-001 | [webhooks.py:26](../../../klai-portal/backend/app/api/webhooks.py#L26) |
| 1 | CORS credentialed wildcard | VERIFIED | [SPEC-SEC-CORS-001](../SPEC-SEC-CORS-001/spec.md) | [config.py:212](../../../klai-portal/backend/app/core/config.py#L212) |
| 17 | CSRF-exempt login endpoints | VERIFIED | SPEC-SEC-CORS-001 | [session.py:35-52](../../../klai-portal/backend/app/middleware/session.py#L35) |

### P1 — Fix this sprint

| # | Finding | Verdict | SPEC | File reference |
|---|---|---|---|---|
| 5 | Cross-tenant IDOR offboarding | VERIFIED | [SPEC-SEC-TENANT-001](../SPEC-SEC-TENANT-001/spec.md) | [users.py:436](../../../klai-portal/backend/app/api/admin/users.py#L436) |
| 10 | invite_user hardcoded Zitadel org:owner | VERIFIED (config-dep chain) | SPEC-SEC-TENANT-001 | [users.py:162-166](../../../klai-portal/backend/app/api/admin/users.py#L162) |
| 9 | IMAP From-header trust | VERIFIED | [SPEC-SEC-IMAP-001](../SPEC-SEC-IMAP-001/spec.md) | [imap_listener.py:77-107](../../../klai-portal/backend/app/services/imap_listener.py#L77) |
| 11 | MFA fail-open on Zitadel 5xx | VERIFIED | [SPEC-SEC-MFA-001](../SPEC-SEC-MFA-001/spec.md) | [auth.py:409-422](../../../klai-portal/backend/app/api/auth.py#L409) |
| 12 | MFA skipped when pre-auth lookup fails | VERIFIED | SPEC-SEC-MFA-001 | [auth.py:363-422](../../../klai-portal/backend/app/api/auth.py#L363) |

### P2 — Fix this quarter

| # | Finding | Verdict | SPEC | File reference |
|---|---|---|---|---|
| 13 | TOTP attempt counter per-instance | VERIFIED | [SPEC-SEC-SESSION-001](../SPEC-SEC-SESSION-001/spec.md) | [auth.py:73-97](../../../klai-portal/backend/app/api/auth.py#L73) |
| 15 | klai_idp_pending cookie no binding | VERIFIED | SPEC-SEC-SESSION-001 | [signup.py:243-391](../../../klai-portal/backend/app/api/signup.py#L243) |
| 16 | klai_sso cookie key regen on empty env | VERIFIED | SPEC-SEC-SESSION-001 | [auth.py:106](../../../klai-portal/backend/app/api/auth.py#L106) |
| 14 | Internal rate-limit fails open on Redis | VERIFIED | [SPEC-SEC-INTERNAL-001](../SPEC-SEC-INTERNAL-001/spec.md) | [internal.py:97-128](../../../klai-portal/backend/app/api/internal.py#L97) |
| 18 | FLUSHALL in /internal/librechat/regenerate | VERIFIED | SPEC-SEC-INTERNAL-001 | [internal.py:940-1037](../../../klai-portal/backend/app/api/internal.py#L940) |
| A2 | Taxonomy internal-token timing compare | VERIFIED | SPEC-SEC-INTERNAL-001 | [taxonomy.py:382-388](../../../klai-portal/backend/app/api/taxonomy.py#L382) |
| A3 | BFF proxy header-injection gap | VERIFIED | SPEC-SEC-INTERNAL-001 | [proxy.py:53-70](../../../klai-portal/backend/app/api/proxy.py#L53) |
| A4 | exc.response.text log reflection | VERIFIED | SPEC-SEC-INTERNAL-001 | (20+ sites in klai-portal/backend/app/api/auth.py) |

### P3 — Hygiene / backlog

| # | Finding | Verdict | SPEC | File reference |
|---|---|---|---|---|
| 19 | Background provisioning + weak signup rate-limit | VERIFIED | [SPEC-SEC-HYGIENE-001](../SPEC-SEC-HYGIENE-001/spec.md) | [signup.py:99-209](../../../klai-portal/backend/app/api/signup.py#L99) |
| 20 | \*.getklai.com trusted redirect | VERIFIED (config-dep) | SPEC-SEC-HYGIENE-001 | [auth.py:138-159](../../../klai-portal/backend/app/api/auth.py#L138) |
| 21 | _safe_return_to misses `\\` and %2f | VERIFIED | SPEC-SEC-HYGIENE-001 | [auth_bff.py:399-404](../../../klai-portal/backend/app/api/auth_bff.py#L399) |
| 22 | Password policy 12+ chars only | VERIFIED | SPEC-SEC-HYGIENE-001 | [signup.py:53-58](../../../klai-portal/backend/app/api/signup.py#L53) |
| 23 | Widget Origin-header trust | PARTIAL | SPEC-SEC-HYGIENE-001 | [partner.py:388-481](../../../klai-portal/backend/app/api/partner.py#L388) |
| 24 | Widget JWT HS256 shared secret | VERIFIED | SPEC-SEC-HYGIENE-001 | [widget_auth.py:20-56](../../../klai-portal/backend/app/services/widget_auth.py#L20) |
| 27 | tenant_matcher 5-min plan cache | VERIFIED | SPEC-SEC-HYGIENE-001 | [tenant_matcher.py:23-47](../../../klai-portal/backend/app/services/tenant_matcher.py#L23) |
| 28 | /docs + /openapi.json gated only by DEBUG flag | VERIFIED | SPEC-SEC-HYGIENE-001 | [main.py:167-170](../../../klai-portal/backend/app/main.py#L167) |

### Dismissed (Cornelis)

| # | Finding | Verdict | Rationale |
|---|---|---|---|
| 25 | CORS does not wrap 401 | **REFUTED** | CORSMiddleware is registered FIRST → outermost in LIFO execution order. 401 responses ARE wrapped. See [main.py:172-196](../../../klai-portal/backend/app/main.py#L172). |
| 26 | idp-callback replay | PARTIAL / **MONITOR** | No in-code single-use enforcement, but Zitadel's intent API is typically one-shot server-side. Verify Zitadel config. Not filed as a SPEC — tracked as a config audit item in Zitadel section of platform/zitadel.md. |
| A7 | POST_MEETING_HOOKS URL secret embedding | CANNOT-VERIFY | Reviewer referenced `deploy/docker-compose.yml:912` which is a comment about socat bridge, not POST_MEETING_HOOKS config. Claim is a governance warning, not a bug. No SPEC required — add a `no-secrets-in-POST_MEETING_HOOKS-URL` entry to pitfalls/process-rules.md instead. |

---

## Internal-wave findings (2026-04-24)

Run by `klai-security-audit` agent against six additional services. All findings below are
verified against code at audit time unless marked otherwise.

### New P0 findings → new SPECs

| Service | Finding | Severity | SPEC |
|---|---|---|---|
| knowledge-mcp | Caller-asserted `X-User-ID` / `X-Org-ID` headers forwarded to upstream without verification | CRITICAL | [SPEC-SEC-IDENTITY-ASSERT-001](../SPEC-SEC-IDENTITY-ASSERT-001/spec.md) |
| knowledge-mcp | Fail-open auth when `KNOWLEDGE_INGEST_SECRET` empty | CRITICAL | SPEC-SEC-IDENTITY-ASSERT-001 + amend WEBHOOK-001 |
| scribe | `POST /v1/transcriptions/{id}/ingest` accepts client-supplied `org_id` | CRITICAL | SPEC-SEC-IDENTITY-ASSERT-001 |
| retrieval-api | Internal-secret callers skip `verify_body_identity` — cross-tenant Qdrant read | CRITICAL | SPEC-SEC-IDENTITY-ASSERT-001 |
| retrieval-api | `_search_notebook` missing `user_id` check | HIGH | SPEC-SEC-IDENTITY-ASSERT-001 |
| mailer | `str.format(**variables)` template injection → introspection RCE, env-dump exfil via SMTP | CRITICAL | [SPEC-SEC-MAILER-INJECTION-001](../SPEC-SEC-MAILER-INJECTION-001/spec.md) |
| mailer | No `to_address` allowlist → Klai SMTP as SPF/DKIM-aligned phishing relay | HIGH | SPEC-SEC-MAILER-INJECTION-001 |
| mailer | Webhook replay (5-min window, no nonce) | MEDIUM | SPEC-SEC-MAILER-INJECTION-001 |
| mailer | `/debug` endpoint gated only by `DEBUG=true` | MEDIUM | SPEC-SEC-MAILER-INJECTION-001 |

### New P1 findings → new SPECs

| Service | Finding | Severity | SPEC |
|---|---|---|---|
| scribe | `env_file: .env` pulls every host secret into scribe process environment | MEDIUM (CRITICAL chain) | [SPEC-SEC-ENVFILE-SCOPE-001](../SPEC-SEC-ENVFILE-SCOPE-001/spec.md) |
| all services | Same `env_file: .env` anti-pattern likely widespread; audit required | — | SPEC-SEC-ENVFILE-SCOPE-001 |
| scribe | Path traversal via `audio_path` from Zitadel `sub` (no `.resolve().is_relative_to()`) | HIGH | SPEC-SEC-HYGIENE-001 amendment |
| scribe | `verify_aud: False` in JWT decode — cross-OIDC-app token replay | HIGH | SPEC-SEC-012 (pre-existing SPEC; land it) |

### New findings → amendments to existing SPECs

| Service | Finding | Severity | Amend |
|---|---|---|---|
| connector | Image pipeline SSRF (Notion/Confluence/GitHub/Airtable adapters) | HIGH (CRITICAL if connector ever joins `socket-proxy`) | **SPEC-SEC-SSRF-001** — expand scope to cover image pipeline |
| connector | Confluence `base_url` → atlassian SDK with tenant Basic-auth → blind SSRF | MEDIUM | **SPEC-SEC-SSRF-001** |
| connector | `main.py` registers CORS before AuthMiddleware → 401s without CORS headers | HIGH (DinD) | **SPEC-SEC-CORS-001** — klai-connector fix (opposite of portal-api) |
| connector | `NameError: HTTPException` → 500 on every not-found branch → UUID oracle | MEDIUM | **SPEC-SEC-HYGIENE-001** — also enable ruff F821 CI gate |
| connector | `SyncRun` model has no `org_id` column; `PORTAL_CALLER_SECRET` is effectively global-admin | MEDIUM | **SPEC-SEC-TENANT-001** — add sync-routes org_id enforcement |
| connector | Outbound secrets silently empty-string bypass when env unset | MEDIUM | **SPEC-SEC-INTERNAL-001** — fail-closed on empty outbound secret |
| mailer | `_validate_incoming_secret` uses `!=` not `hmac.compare_digest` | HIGH | **SPEC-SEC-INTERNAL-001** — scope expansion from portal-only to all services |
| mailer | `resp.text[:200]` error-body reflection in logs | LOW-MEDIUM | **SPEC-SEC-INTERNAL-001** — `sanitize_response_body` utility applies here too |
| retrieval-api | XFF-spoof bypass of rate-limit (uvicorn without `--proxy-headers`) | HIGH | **SPEC-SEC-WEBHOOK-001** — uvicorn `--proxy-headers` is service-wide fix |
| retrieval-api | No CORSMiddleware registered at all | MEDIUM (DinD) | **SPEC-SEC-CORS-001** — add retrieval-api to scope |
| retrieval-api | `emit_event` with attacker-controlled `tenant_id` / `user_id` | MEDIUM | SPEC-SEC-IDENTITY-ASSERT-001 (same pattern) |
| scribe, mailer, knowledge-mcp | `resp.text[:200]` / `resp.text[:300]` error-body reflection | MEDIUM each | **SPEC-SEC-INTERNAL-001** |

### Governance decision needed

| Service | Issue | Decision required |
|---|---|---|
| klai-focus/research-api | Service is FROZEN per its own README — not in `docker-compose.yml`. Audit surfaced 6 CRITICAL + 4 HIGH findings that are latent historical issues. | (a) Delete the directory from the monorepo (closes all findings), OR (b) add `[HARD]` rule to `.claude/rules/klai/projects/` that the service must NOT be resurrected without re-running the audit and fixing findings first. **Not a SPEC — a one-line governance call.** |

---

## SPEC summary (v0.3 — plan-completeness reached)

All SPECs below have full EARS requirements, research.md, and acceptance.md. Ready for
`/moai run` delegation. Ship order per Process section.

| SPEC | Version | Priority | Findings | Summary |
|---|---|---|---|---|
| SPEC-SEC-SSRF-001 | v0.3.0 | P0 | #6, #7, #8, A1 + connector image pipeline + Confluence base_url | SSRF guard coverage + TOCTOU-safe DNS + image-URL validation |
| SPEC-SEC-WEBHOOK-001 | v0.3.0 | P0 | #2, #3, #4 + retrieval-api/scribe/knowledge-ingest proxy-headers | Webhook auth + uvicorn `--proxy-headers` service-wide |
| SPEC-SEC-CORS-001 | v0.3.0 | P0 | #1, #17 + connector middleware order + retrieval-api no-CORS | CORS allowlist + middleware order lint across 8 services |
| **SPEC-SEC-IDENTITY-ASSERT-001** | **v0.2.0** | **P0** | **knowledge-mcp M1, scribe S1, retrieval-api R1+R2+R3, klai-docs D1** | **Verify caller-asserted identity on service-to-service calls** |
| **SPEC-SEC-MAILER-INJECTION-001** | **v0.2.0** | **P0** | **mailer-2..mailer-9 (str.format, relay, /debug, replay, !=, WEBHOOK_SECRET empty, signature oracle, permissive parser)** | **Mailer template-injection + relay + debug hardening** |
| SPEC-SEC-TENANT-001 | v0.3.0 | P1 | #5, #10 + connector SyncRun org_id | Tenant scoping + role mapping + sync-routes tenant enforcement |
| SPEC-SEC-IMAP-001 | v0.2.0 | P1 | #9 | DKIM/SPF/ARC enforcement in IMAP listener |
| SPEC-SEC-MFA-001 | v0.2.0 | P1 | #11, #12 | MFA fail-closed in login flow |
| **SPEC-SEC-ENVFILE-SCOPE-001** | **v0.2.0** | **P1** | **4 services with `env_file: .env` (portal, victorialogs, retrieval, scribe)** | **Explicit per-service environment blocks; no shared `env_file: .env`** |
| SPEC-SEC-SESSION-001 | v0.2.0 | P2 | #13, #15, #16 | Session/cookie robustness + externalized counters |
| SPEC-SEC-INTERNAL-001 | v0.3.0 | P2 | #14, #18, A2, A3, A4 + 7 cross-service additions (mailer !=, all resp.text sites, connector empty-secret, knowledge-mcp chat-UI body echo, persisted error-body) | Internal-secret surface hardening (service-wide scope) |
| SPEC-SEC-HYGIENE-001 | v0.3.0 | P3 | #19-#24, #27, #28 + HY-30..HY-50 across 5 services | Grouped hygiene fixes (split permitted per /run review) |

---

## Process

1. Product owner (Mark) reviews this tracker, approves or adjusts priority bucketing
2. For each SPEC, run `/moai plan SPEC-SEC-XXX-001` to expand the stub into a full EARS SPEC
   with acceptance criteria (wave 2 of 2026-04-24 already expanded the Cornelis-9 stubs)
3. After plan approval, run `/moai run SPEC-SEC-XXX-001` using DDD mode (these are brownfield
   fixes, not greenfield features)
4. Ship order (P0 first, parallel where feasible):
   - SPEC-SEC-SSRF-001 (Cornelis A1 chain + connector amendments)
   - SPEC-SEC-WEBHOOK-001 (Vexa IP-bypass + service-wide proxy-headers)
   - SPEC-SEC-MAILER-INJECTION-001 (str.format RCE blocks the chain A from klai-net)
   - SPEC-SEC-IDENTITY-ASSERT-001 (cross-service pattern — 3 CRITICAL at once)
   - SPEC-SEC-CORS-001 (reduces blast radius of any surviving auth issue)
5. Close each SPEC against its findings in this tracker as implementation lands
6. Close SPEC-SEC-AUDIT-2026-04 when all 12 sub-SPECs reach status: done AND klai-focus
   governance decision is made

---

## Cross-cutting concerns

- **Regression tests**: every fix SPEC must include a failing test that would have caught the
  original finding before it was introduced (Rule 4 of CLAUDE.md safeguards)
- **Audit agent validation**: after all 12 SPECs close, re-run the `klai-security-audit` agent
  across the same scope and compare deltas
- **External review**: consider inviting Cornelis or equivalent for a follow-up audit in 6
  months to validate the fixes did not introduce new defects
- **New learnings to pitfalls/process-rules.md**: 8+ new anti-patterns surfaced (fail-open-auth,
  ip-range-trust, empty-secret-fail-open, non-constant-time-compare, missing-org-scope,
  toctou-dns, from-header-trust, hardcoded-zitadel-role, caller-asserted-identity,
  format-string-template-injection, shared-env-file-pattern) — capture via `/klai:retro` in a
  separate session to avoid polluting this tracker
