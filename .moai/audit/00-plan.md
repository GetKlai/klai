# Klai — Security, Code & Dead Code Audit Plan

**Start:** 2026-04-19
**Laatst bijgewerkt:** 2026-04-22 — SEC-021 runtime-api via socat sidecar LIVE op dev + E2E geverifieerd via Playwright. AUTH-008-F (RLS-gap in meetings.py) + AUTH-008-G (sweep 24 post-commit refreshes op category-D RLS tables) LIVE. Alle originele SEC-tickets gesloten behalve SEC-022 (live-ops window vereist).
**Werklocatie:** `.moai/audit/`
**Scope:** hele klai-monorepo (13 sub-repos)

## Status-dashboard

| Fase | Status | Artefact | # Findings |
|---|---|---|---|
| 0 — Inventaris | partial (via scope-tabel) | — | — |
| 1 — Secrets & config | **✅ covered** | `reports/cve-triage-2026-04-19.md` + gitleaks config (commit 1fe61b66) | secret scanning + push protection + gitleaks-config LIVE; nieuwe commits gedekt |
| 2 — Dependencies | **grotendeels gedekt door parallelle session** | `reports/dependency-audit-2026-04-19.md`, `docs/runbooks/version-management.md` | 26 images pinned, 1 CRITICAL CVE gefixt (LiteLLM), 6 CVE-detectielagen actief, 3 critical upstream-blocked |
| **3 — Tenant isolation** | **✅ completed** | `04-tenant-isolation.md`, `04-2-query-inventory.md`, `04-3-prework-caddy.md` | **22** (2 CRITICAL, 5 HIGH, 7 MEDIUM, 5 LOW, 2 POSITIVE, 1 unknown) + RLS silent-failure class (AUTH-008-E, 2026-04-20) |
| **4 — Input validation / injection** | **✅ completed** | `05-injection.md` | **7** (0 CRITICAL, 0 HIGH, 5 MEDIUM, 2 LOW, F-026 false positive) |
| 5 — API hardening | **grotendeels gedekt** (Caddy verify + X-XSS-Protection header via 1fe61b66) | `06-api-hardening.md` | dekt F-018, F-020, F-022; CORS + rate-limit + XSS header LIVE; CSP-audit en HSTS-preload check nog open |
| **6 — Dead code** | **✅ completed** | `07-dead-code.md` | **29** (22 Python + 7 TS; DEAD-008 false positive — deferred feature) |
| **Vexa audit** | **✅ completed** | `08-vexa.md` | **8** (V-001..V-008 / F-030..F-037; 2 HIGH) |
| **BFF-proxy gap** | **✅ identified + fixed** | `09-bff-proxy-gap.md` | F-038 (SEC-023 LIVE) |
| **Post-audit follow-ups** | **✅ tracked in roadmap** | `.moai/specs/SPEC-SEC-024/`, `.../SPEC-PROV-001/`, `.../SPEC-OBS-001/`, `.../SPEC-INFRA-005/` | 4 SPECs: 2 LIVE (SEC-024, PROV-001), 1 in_progress (INFRA-005 Phase 1+2 LIVE), 1 draft (OBS-001) + AUTH-008 phase E/F (RLS + auth surface) LIVE |
| 7 — Synthesiseer | **✅ completed (living)** | `99-fix-roadmap.md` | 21 fix-groepen DONE + 2 open (SEC-022 live-ops window, OBS-001 implementation) + 1 in_progress (INFRA-005 Phase 3+) + 1 user-action (#31 Playwright E2E) + 1 deferred (#32) |

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
- [x] **SEC-004** Defense-in-depth AuthGuardMiddleware in research-api + scribe-api — LIVE, smoke-tested (401 zonder header, 200 op /health)
- [x] **SEC-020** Vexa external repo audit — DONE in `.moai/audit/10-vexa-external-audit.md`. Vexa auth-contract solide (fail-closed, hmac.compare_digest). 1 follow-up: `ALLOW_PRIVATE_CALLBACKS=1` flip.
- [x] **SEC-021** runtime-api docker-socket-proxy — **LIVE on dev 2026-04-22** (078cc0f2). Vexa runtime-api kan geen TCP spreken (hardcoded `requests_unixsocket`), dus via `alpine/socat:1.7.3.4-r1` sidecar die Unix socket forwardt naar `docker-socket-proxy:2375`. Hardening verified: EXEC/IMAGES/VOLUMES/SYSTEM geven 403, CONTAINERS/NETWORKS werken. SPEC v0.3.0 in `.moai/specs/SPEC-SEC-021/spec.md`. Portal-api scope eerder al gesloten via SPEC-SEC-024. **End-to-end verified via Playwright** op voys.getklai.com: bot-spawn werkt, meeting-api → runtime-api → socat → proxy → daemon keten gevalideerd.
- [x] **AUTH-008-F** RLS-gap in `meetings.py` — **LIVE on main 2026-04-22** (b64d70dc). Post-commit `db.refresh` verwijderd in start/stop_meeting. Tenant context is transaction-scoped, refresh opende nieuwe transactie zonder context → 500. Ontdekt tijdens SEC-021 E2E test.
- [x] **AUTH-008-G** Sweep post-commit refresh op category-D RLS tables — **LIVE on main 2026-04-22** (486336a1 + ddb6cbc5). 5 bestanden / 24 sites. UPDATE endpoints: refresh verwijderd. CREATE endpoints: refresh verplaatst naar vóór commit (server_default kolommen zoals `created_at`). `expert-refactoring` agent voerde de sweep uit met `RLS_DML_TABLES` als source-of-truth.
- [ ] **SEC-022** vexa-bots network egress — SPEC'd in `.moai/specs/SPEC-SEC-022/spec.md`. Implementation vereist live-ops window.
- [x] **SEC-024** docker-socket-proxy compliance audit — LIVE. M1 exec_run audit + M2 proxy-pin/forbidden-verbs + M3 ast-grep CI-guard + M4 smoke-test + Grafana alert/dashboard + deploy-compose sync. Zero-tolerance alerting op denial-endpoint.
- [x] **PROV-001** Transactional tenant provisioning — LIVE (71b9c973). AsyncExitStack rollback + idempotent retry + startup stuck-detector.
- [ ] **OBS-001** Grafana Unified Alerting + e-mail — DRAFT v0.2.0. Getriggerd door FLUSHALL observability gap. Implementation open.
- [~] **INFRA-005** Stateful service persistence + backup — IN_PROGRESS. Phase 1 (volume inventory) + Phase 2 (audit-compose CI) LIVE. Phase 3+ (healthchecks, FalkorDB/Qdrant/Garage backup, research-uploads retention) open.
- [x] **AUTH-008-E/F** RLS silent-failure + auth surface cleanup — LIVE (849c7117 + 42fd9f06 + a2a6a0be + ba7861be + 47e51685 + f05f59b4 + eb067246 + 48559c79 + c5da5c17 + ce145a58). Class-of-bug: `SET LOCAL app.tenant_id` kon silently falen zonder pinned connection. Fix: pin + fail-loud.
- [x] **Task #31** SEC-023 end-to-end Playwright verify — DONE (user confirmed 2026-04-21)
- [x] **Task #32** BFF proxy streaming upload body — DEFERRED (geen upload-pad gaat nu via BFF proxy; wordt relevant zodra dat verandert)
- [x] **DEAD-* batch** config+connector dead code — RESOLVED (zie roadmap 2026-04-19 DEAD-batch triage entry)

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
