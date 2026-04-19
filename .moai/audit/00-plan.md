# Klai — Security, Code & Dead Code Audit Plan

**Start:** 2026-04-19
**Laatst bijgewerkt:** 2026-04-19 — SEC-012 + SEC-014 + SEC-018 (core-01 + gpu-01) + SEC-023 LIVE. Kritische scope dicht. Resterende 5 tickets zijn defense-in-depth (SEC-004), externe repo (SEC-020), infra SPEC (SEC-021/022) of E2E-verificatie (#31) + non-urgent (#32).
**Werklocatie:** `.moai/audit/`
**Scope:** hele klai-monorepo (13 sub-repos)

## Status-dashboard

| Fase | Status | Artefact | # Findings |
|---|---|---|---|
| 0 — Inventaris | partial (via scope-tabel) | — | — |
| 1 — Secrets & config | **grotendeels gedekt door parallelle session** | `reports/cve-triage-2026-04-19.md` (indirect) | secret scanning + push protection LIVE; gitleaks sweep niet strikt gedaan (accept risk — push protection dekt nieuwe commits) |
| 2 — Dependencies | **grotendeels gedekt door parallelle session** | `reports/dependency-audit-2026-04-19.md`, `docs/runbooks/version-management.md` | 26 images pinned, 1 CRITICAL CVE gefixt (LiteLLM), 6 CVE-detectielagen actief, 3 critical upstream-blocked |
| **3 — Tenant isolation** | **✅ completed** | `04-tenant-isolation.md`, `04-2-query-inventory.md`, `04-3-prework-caddy.md` | **22** (2 CRITICAL, 5 HIGH, 7 MEDIUM, 5 LOW, 2 POSITIVE, 1 unknown) |
| **4 — Input validation / injection** | **✅ completed** | `05-injection.md` | **7** (0 CRITICAL, 0 HIGH, 5 MEDIUM, 2 LOW, F-026 false positive) |
| 5 — API hardening | partial (via Caddy verify) | `06-api-hardening.md` | (reeds dekt F-018, F-020, F-022) |
| **6 — Dead code** | **✅ completed** | `07-dead-code.md` | **29** (22 Python + 7 TS; DEAD-008 false positive — deferred feature) |
| **Vexa audit** | **✅ completed** | `08-vexa.md` | **8** (V-001..V-008 / F-030..F-037; 2 HIGH) |
| **BFF-proxy gap** | **✅ identified + fixed** | `09-bff-proxy-gap.md` | F-038 (SEC-023 LIVE) |
| 7 — Synthesiseer | **in_progress (living)** | `99-fix-roadmap.md` | 15 fix-groepen DONE + 5 open (SEC-004 defense-in-depth middleware, SEC-020 externe repo, SEC-021/022 infra SPEC, #31 E2E verify) |

**Pre-work status:**
- [x] PRE-A — PG-role `bypassrls` = false voor `portal_api` ✓
- [x] PRE-B — Zitadel org_ids zijn 18-digit Snowflake (enumereerbaar) → F-001 CRITICAL

**Implementatie-status per SEC-fix-groep:**
- [x] **SEC-005** Internal-endpoint hardening — LIVE (audit row persisted)
- [x] **SEC-006** Widget JWT revocation via DB cross-check — LIVE
- [x] **SEC-007** Code-quality (connector LRU + portal @MX annotations) — LIVE
- [x] **SEC-008** Connector hardening (audience + hmac + LRU) + dev env basic-auth — LIVE
- [x] **SEC-009** SERVERS.md doc drift — LIVE
- [x] **SEC-010** Retrieval-API hardening (F-001 CRITICAL) — LIVE, smoke-tested
- [x] **SEC-011** Knowledge-ingest fail-closed auth — LIVE
- [x] **SEC-013** Vexa hardening F-030/032/033/035 — LIVE
- [x] **SEC-014** taxonomy.py portal_internal_token fail-closed — LIVE
- [x] **SEC-016** Fase 4 noqa+encoding — LIVE
- [x] **SEC-018** Monorepo-wide Dockerfile USER audit — LIVE (11 van 12 non-root; caddy intentional-root gedocumenteerd)
- [x] **SEC-019** Dead-code cleanup Python+frontend (~1880 LOC) — LIVE
- [x] **SEC-023** Internal services BFF proxy (F-038) — LIVE (portal-api proxy voor Focus/Scribe/Docs) + CSRF review (al afgedekt)
- [x] **SEC-012** JWT audience research-api — LIVE, smoke-tested (401 op bogus bearer). Scribe-deel superseded door SPEC-VEXA-003.
- [ ] **SEC-004** Defense-in-depth middleware focus/scribe — OPEN, nu unblocked (dependency SEC-012 landed). Low-risk: huidige routes hebben al auth-deps; middleware is safety-net voor toekomstige PRs.
- [ ] **SEC-020** vexa-bot-manager external auth audit — OPEN (externe repo, aparte pass). docs-app `lib/auth.ts` deel afgerond (BFF-flow werkt).
- [ ] **SEC-021** runtime-api docker-socket-proxy migratie (F-031) — OPEN, infra SPEC nodig
- [ ] **SEC-022** vexa-bots network egress check (F-037) — OPEN
- [ ] **Task #31** SEC-023 end-to-end Playwright verify — OPEN (requires user login credentials)
- [ ] **Task #32** BFF proxy streaming upload body — OPEN, non-urgent (huidige uploads gaan niet via BFF proxy)
- [ ] **DEAD-* batch** config+connector dead code — OPEN, pending user review

## Principe

Werk in lagen van *cheap-en-breed* naar *duur-en-diep*. Elke fase levert een eigen artefact op; pas door naar de volgende fase als de findings van de vorige getrieerd zijn. Geen mega-eindrapport dat niemand leest — per-fase output die meteen actionable is.

## Scope — sub-repos in klai monorepo

| Repo | Stack | Security-relevantie |
|---|---|---|
| `klai-portal` | Python (backend) + TS/React (frontend) | Auth, multi-tenant, user-facing APIs |
| `klai-retrieval-api` | Python / asyncpg | Multi-tenant reads, vector search |
| `klai-knowledge-ingest` | Python | File uploads, external fetch, tenant writes |
| `klai-knowledge-mcp` | Python / MCP | MCP server surface |
| `klai-connector` | Python | External integrations (SSRF risk) |
| `klai-scribe` | Python | Meeting transcription |
| `klai-mailer` | Python | Email (SMTP, templates) |
| `klai-focus` | Python (research-api) | Notebook/sources, SQLAlchemy |
| `klai-widget` | TS/JS | Client-side widget (XSS risk) |
| `klai-website` | TS/Next.js/Astro | Marketing site |
| `klai-docs` | Docs site | Low risk, quick scan |
| `klai-infra` | Docker/Caddy/Alloy | Container, network, headers |
| `klai-private` | Tools + internal | Don't leak, don't distribute |

## Fases

### Fase 0 — Inventaris & risicokaart
Attack surface per service: endpoints, inputs, secrets, externe deps, tenant boundaries. Basis voor alle andere fases.

- **Output:** `01-inventory.md`
- **Status:** todo

### Fase 1 — Secrets & config (parallel-capable)
- `gitleaks` op git history + working tree per repo
- SOPS-consistency: alles wat secret hoort te zijn, is ook encrypted
- Env-var lekken in logs, Docker layers, frontend bundles

- **Tools:** `gitleaks detect`, `trufflehog git`, grep op SOPS patterns
- **Output:** `02-secrets.md` met severity-lijst
- **Status:** todo

### Fase 2 — Dependencies (parallel-capable)
- Python: `pip-audit` per service
- Node: `npm audit` op frontend + website + widget
- Docker base images: `trivy image`

- **Output:** `03-deps.md` met CVE-lijst en fixability
- **Status:** todo

### Fase 3 — Tenant isolation *(Klai-specifiek, hoogste risico)*
Elke DB-query door: is `org_id` gefilterd? Cross-tenant leakage is hier de killer-bug.

- CodeIndex cypher + manuele review van asyncpg/SQLAlchemy calls
- Auth middleware mapping (`RequestContextMiddleware`, `get_current_user`, JWT claims)
- Endpoint-per-endpoint: welke vragen org_id, welke vertrouwen op middleware
- RLS (row-level security) check — wordt het gebruikt?

- **Outputs:**
  - `04-tenant-isolation.md` — Fase 3.1 findings (F-001 t/m F-011) + resumable appendix
  - `04-2-query-inventory.md` — Fase 3.2 + 3.3 query inventaris (F-012 t/m F-016)
  - `04-3-prework-caddy.md` — Pre-work resultaten + Caddy verify (F-017 t/m F-022)
- **Status:** **✅ completed 2026-04-19**
- **Result:**
  - 22 findings: **F-001 CRITICAL** (retrieval-api no auth + Snowflake enumerable), **F-009/F-012/F-014/F-017 HIGH**, 7 MEDIUM, 5 LOW, 2 POSITIVE (F-013, F-016)
  - Portal-api tenant isolation is solide (2-laags: helper + RLS, `portal_api` bypassrls=false)
  - Retrieval-api en knowledge-ingest zijn de zwakke services
  - Caddy onthulde dat `connector.getklai.com` publiek is (contra SERVERS.md)
- **Open parking-lot items:** zie `04-3-prework-caddy.md` § Open items

### Fase 4 — Input validation & injection
- `semgrep --config=p/owasp-top-ten --config=p/python --config=p/typescript`
- `bandit -r` per Python service
- SSRF in `klai-connector` (user-controlled URLs)
- Path traversal in `klai-knowledge-ingest` (file uploads)
- XSS in `klai-widget` / `klai-portal` frontend (`dangerouslySetInnerHTML`, `innerHTML`)

- **Output:** `05-injection.md` gecategoriseerd per CWE
- **Status:** **✅ completed 2026-04-19**
- **Result:** 7 findings, 0 critical/high. F-023 (Scribe lockdir path traversal, LOW), F-024 (LibreChat hardcoded JWT, contained), F-025 (bandit noqa — n/a, tool niet in CI), F-026 (false positive), F-027 (encoding), F-028 (Python 3.13 upgrade al gedaan), F-029 → SEC-018. Fix-commit db2ea155 (SEC-016). Rapport in `05-injection.md`.

### Fase 5 — API hardening
- CORS policy per service
- Rate limiting (waar / waar niet)
- Auth-coverage per endpoint (is elke route beschermd?)
- Caddy security headers (HSTS / CSP / X-Frame-Options / Referrer-Policy)

- **Output:** `06-api-hardening.md` met hardening checklist
- **Status:** todo

### Fase 6 — Dead code (parallel aan 1+2)
- Python: `vulture` per service (confidence ≥80)
- TS: `knip` op frontend + website + widget
- CodeIndex cypher: `MATCH (n:Function) WHERE NOT ()-[:CALLS]->(n)` → 0-fan_in functies
- Dode endpoints (geroute maar nooit aangeroepen) — apart risico: staan wel in productie

- **Output:** `07-dead-code.md` met:
  - Verwijder-kandidaten per repo
  - Blast radius per item
  - Aanbevolen SPEC voor cleanup
- **Status:** **✅ completed 2026-04-19**
- **Result:** 29 findings (22 Python + 7 TS). DEAD-008 = false positive (deferred feature per SPEC-KB-007 AC-7, @MX:TODO geannoteerd). Cleanup via SEC-019: ~1880 LOC verwijderd (f30c4cf5, b406994c). Resterende 8 items (DEAD-004/005/009/010/011/020/021/022) zijn config-keys + connector internals die owner-review nodig hebben.

### Fase 7 — Synthesiseer & prioriteer
- CVSS-achtige scoring van alle findings
- Top-N critical
- Fix-roadmap: `quick-win` / `SPEC-waardig` / `accepteren-met-reden`

- **Output:** `99-fix-roadmap.md` — executive summary + action list (**living doc** — groeit mee met elke fase)
- **Status:** **in_progress (living)** — 13 SEC-tickets LIVE op main, 6 open (waarvan 4 externe repo of infra SPEC-waardig, 1 defense-in-depth low-prio, 1 batch-review).

## Parallellisatie

- Fase 1, 2, 6 kunnen tegelijk (geen onderlinge dep)
- Fase 3, 4, 5 na fase 0
- Fase 7 als laatste

## Artefact-conventies

- Findings krijgen severity: `critical` / `high` / `medium` / `low` / `info`
- Elke finding verwijst naar file:line
- Kritieke findings worden ook met `codeindex remember` vastgelegd zodat toekomstige refactors context hebben
- Geen geheime info / exploit-code in publiek leesbare artefacten

## Buiten scope

- Actieve pentesting (authenticated/unauthenticated)
- Social engineering / phishing simulatie
- Compliance-audits (SOC2, ISO27001 — apart traject)
- Performance/load testing
