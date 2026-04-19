# Security Audit — Fix Roadmap

> **Living document.** Groeit mee met elke afgeronde audit-fase. Bevat geconsolideerde fix-groepen uit alle findings.

**Laatst bijgewerkt:** 2026-04-19 — Wave 1 + Wave 2 (SEC-009/011) LIVE op main
**Input documenten:**
- `.moai/audit/04-tenant-isolation.md` — Fase 3.1 findings (F-001 t/m F-011)
- `.moai/audit/04-2-query-inventory.md` — Fase 3.2+3.3 findings (F-012 t/m F-016)
- `.moai/audit/04-3-prework-caddy.md` — Pre-work + Caddy verify (F-017 t/m F-022)
- Parallel session output (2026-04-19): `reports/dependency-audit-2026-04-19.md`, `reports/cve-triage-2026-04-19.md`, `docs/runbooks/version-management.md` — dekt Fase 1+2 van het audit-plan indirect (dependency + CVE scanning)

## Overzicht — prioriteit matrix

> **Update 2026-04-19 post-Caddy-verify:** F-001 upgraded to CRITICAL (Zitadel org_ids zijn enumereerbaar snowflake numerics). F-009 upgraded to HIGH (klai-connector is publiek). Nieuwe SEC-008 en SEC-009 toegevoegd.
>
> **Update 2026-04-19 na Wave 1+2 deploy:** SEC-010, SEC-011, SEC-009 LIVE op main. SEC-012 gepauzeerd wegens parallelle scribe-rebuild (SPEC-VEXA-003). Nieuwe bevinding tijdens SEC-011 implementatie: `taxonomy.py:83 _verify_internal_token` heeft analoog fail-open patroon voor `portal_internal_token` (omgekeerde richting ingest → portal) — getrackt als SEC-014 follow-up.

| Prio | SEC-ID | Titel | Findings | Services | Status |
|---|---|---|---|---|---|
| ~~P0~~ | ~~PRE-A~~ | PG-role `bypassrls` verificatie | F-015 context | — | **✅ DONE** — portal_api has bypassrls=false |
| ~~P0~~ | ~~PRE-B~~ | Zitadel org_id entropy check | F-001 context | — | **✅ DONE** — 18-digit Snowflake, enumereerbaar → F-001 → CRITICAL |
| ~~P0~~ | ~~SEC-010~~ | Retrieval-API hardening | F-001, F-010, F-014 | retrieval-api | **✅ LIVE on main** (f48381f4, a1b4d52f, 410965c9, bba52266, f803f687) |
| ~~P1~~ | ~~SEC-011~~ | Knowledge-ingest fail-closed auth | F-003, F-012 | knowledge-ingest | **✅ LIVE on main** (3ca34341) |
| ~~P3~~ | ~~SEC-009~~ | SERVERS.md doc-drift | F-017, F-018, F-020, F-022 | klai-infra docs | **✅ LIVE** (klai-infra 58f0b60) |
| ~~P2~~ | ~~SEC-012~~ | JWT audience verification mandatory | F-002, F-004 | research-api | **✅ IMPLEMENTED** (research-api) — fail-closed pydantic validator + onvoorwaardelijke `audience=` in jwt.decode (geen `verify_aud: False` branch meer). RESEARCH_API_ZITADEL_AUDIENCE in SOPS + live. scribe-api deel superseded door SPEC-VEXA-003 rebuild. |
| **IN_PROGRESS** | SEC-023 | Internal services BFF proxy (NEW, reframed from SEC-012) | F-038 | portal-api, frontend, Caddy, research-api, scribe-api, docs-app | Fixes silent-broken Focus/Scribe/Docs modules sinds AUTH-008. Proxy via portal-api ipv direct Caddy strip_prefix. |
| ~~P1~~ | ~~SEC-008~~ | Caddy exposure hardening (reduced) | F-017, F-018 | connector, dev env | **✅ LIVE on main** (11d6c28f + klai-infra 41ff469). F-020/F-022 → SEC-013. |
| ~~P2~~ | ~~SEC-004~~ | Defense-in-depth auth middleware | F-005, F-006, F-009 | research-api + scribe-api | **✅ IMPLEMENTED** — `AuthGuardMiddleware` in beide services rejected requests zonder Authorization header vóór route handler runs. Exempt: /health, /v1/health, OPTIONS (CORS preflight). Registratie volgorde gecorrigeerd: AuthGuard → RequestContext → CORS (CORS outermost zodat 401s CORS headers dragen). F-006/F-009 al eerder gefixt in SEC-005/007/008. |
| ~~P2~~ | ~~SEC-005~~ | Internal-endpoint hardening | F-007 | portal | **✅ LIVE on main** (739a3177 + klai-infra 41ff469). Smoke-tested: 200 + audit row. |
| ~~P3~~ | ~~SEC-006~~ | Widget JWT revocation | F-008 | portal partner API | **✅ LIVE on main** (78d7ce26) |
| ~~P3~~ | ~~SEC-007~~ | Code-quality / correctness | F-011, F-015 doc | connector, portal background | **✅ LIVE on main** (1e3f9945 + 11d6c28f) |
| ~~P1~~ | ~~SEC-013~~ | Vexa stack hardening (F-030/032/033/035) | F-030, F-032, F-033, F-035 | portal, caddy, compose | **✅ LIVE on main** (681b442a, cc5eff9d + klai-infra 9082cde). F-020/F-022 (vexa-bot-manager + docs-app) nog apart. |
| ~~P3~~ | ~~SEC-016~~ | Fase 4 noqa+encoding cleanup | F-024, F-027 | connector, knowledge-ingest, portal | **✅ LIVE on main** (db2ea155). F-025 n/a (bandit niet configured). |
| ~~P3~~ | ~~SEC-019~~ | Dead-code cleanup (Python+frontend) | DEAD-001..003, 006, 012, 013, 016-019, 023-026 | 6 Python services + frontend | **✅ LIVE on main** (f30c4cf5 + b406994c). ~1880 LOC weg. |
| ~~P3~~ | ~~SEC-014~~ | taxonomy.py portal_internal_token fail-closed | — | knowledge-ingest taxonomy.py:81 | **✅ LIVE on main**. `_verify_internal_token` gebruikt nu `hmac.compare_digest` + pydantic model_validator bij startup faalt als token leeg. |
| ~~P3~~ | ~~SEC-018~~ | Monorepo-wide Dockerfile USER audit | F-029 | 12 Dockerfiles monorepo-breed | **✅ LIVE on main + gpu-01** (8893a00e). 11 van 12 images draaien als non-root: knowledge-ingest uid=1000(app) op core-01 verified; klai-gpu-bge-m3-sparse uid=1000(app) op gpu-01 verified (volume `/models` chowned naar 1000:1000; sparse_embed + /health smoke-tested post-recreate). caddy bewust root (privileged port binding 80/443, accepted risk). |
| ~~P2~~ | ~~SEC-020~~ | Vexa external repo audit (auth contract + attack surface) | F-020, F-022 | extern Vexa-ai/vexa repo | **✅ AUDITED** — rapport in `.moai/audit/10-vexa-external-audit.md`. 3 POSITIVE (fail-closed auth, hmac.compare_digest, Klai integratie correct), 1 MEDIUM (ALLOW_PRIVATE_CALLBACKS=1 SSRF window — follow-up SEC-013 flag-flip), 2 INFO (Chromium surface → SPEC-SEC-022, upstream release cadence → ops calendar). docs-app `lib/auth.ts` eerder afgerond. |
| ~~P2~~ | ~~SEC-021~~ | runtime-api docker-socket-proxy SPEC | F-031 | klai-infra + vexa runtime-api | **✅ SPEC'd** in `.moai/specs/SPEC-SEC-021/spec.md` (draft). EARS requirements, 3-fase rollout, rollback plan. Implementation = apart ticket (runtime-test vereist). |
| ~~P3~~ | ~~SEC-022~~ | vexa-bots network egress SPEC | F-037 | klai-infra | **✅ SPEC'd** in `.moai/specs/SPEC-SEC-022/spec.md` (draft). Live check: `vexa-bots` network `Internal=false` (172.27.0.0/16). 4-fase aanpak: tcpdump → allowlist → iptables enforce → monitoring. Implementation vereist live-ops window. |
| ~~P3~~ | ~~DEAD-* batch review~~ | Config keys + connector internals | DEAD-004/005/009/010/011/020/021/022 | portal, knowledge-ingest, mailer | **✅ RESOLVED** (2026-04-19). Breakdown: DEAD-005 false positive (dynamic getattr); DEAD-020/021 already cleaned in eerdere refactor (crypto.py + adapters/ directory removed); DEAD-010 + DEAD-022 nu verwijderd; DEAD-004/009/011 geannoteerd met @MX:NOTE/TODO en behouden als reserved-future-use. |

## Deploy status (2026-04-19)

| Service | Wave 1+2 changes | Deploy status |
|---|---|---|
| retrieval-api | SEC-010 middleware + bounds + rate-limit | **✅ live, smoke-tested** (401 zonder secret, 200 met secret) |
| portal-api | SEC-010 caller `partner_chat.py` sends X-Internal-Secret | **✅ live** (via SOPS + deploy) |
| focus research-api | SEC-010 caller `retrieval_client.py` sends X-Internal-Secret | **✅ live** |
| knowledge-ingest | SEC-011 fail-closed startup validator + middleware removal | **✅ live** |
| portal-api | SEC-005 internal rate-limit + audit log + hmac.compare_digest | **✅ live, smoke-tested** (audit row persisted) |
| portal-api | SEC-006 widget JWT DB cross-check for revocation | **✅ live** |
| klai-connector | SEC-007 LRU cache + SEC-008 audience check + hmac.compare_digest | **✅ live** — ZITADEL_API_AUDIENCE in SOPS (Zitadel API app 365340910193475592), audience check active, no more warn-only fallback |
| Caddy | SEC-008 F-018 — dev.getklai.com basic-auth gate | **✅ live** — smoke-tested: no auth → 401, correct creds → pass through to backend, wrong pw → 401 |
| klai-infra | 3 rotation runbooks (INTERNAL_SECRET, CONNECTOR_SECRET, CADDY_DEV_HARDENING) | **✅ merged** |
| SOPS | `RETRIEVAL_API_INTERNAL_SECRET` added, 8 dev vars restored | **✅ synced to `/opt/klai/.env`** |
| SERVERS.md | SEC-009 complete Caddy route table | **✅ merged** (klai-infra) |
| Caddy `/bots/*` | SEC-013 F-030 route target: api-gateway (was broken) | **✅ live, smoke-tested** (200 via curl) |
| api-gateway | SEC-013 F-032 CORS scoped to my/widget.getklai.com | **✅ live** (compose synced) |
| meeting-api | SEC-013 F-035 WHISPER_SERVICE_TOKEN from SOPS | **✅ live** (fallback retained) |
| portal-api | SEC-013 F-033 Vexa webhook fail-closed + hmac | **✅ live** (VEXA_WEBHOOK_SECRET already in SOPS) |
| SOPS | SEC-013 WHISPER_SERVICE_TOKEN added | **✅ synced** |
| Python services | SEC-019 dead-code removal (112 LOC) | **✅ merged** |
| Frontend | SEC-019 dead-code removal (1765 LOC + 5 npm deps) | **✅ merged** |

## Parallelle session (2026-04-19) — wat landde tegelijk

Een andere sessie deed tegelijkertijd een volledige **dependency audit + CVE infrastructure overhaul**. Dat raakt audit-plan Fase 1 (secrets-drift) en Fase 2 (deps) **indirect maar grotendeels**. Relevante landings:

- **Python deps bumped**: cryptography 43→46, redis-py 5→7, Python 3.12→3.13, pytest 8→9
- **Docker images**: 26 externe images van `:latest` → expliciete versie-tag
- **LiteLLM CVE-2026-35030** (CRITICAL) gefixt via pin bump
- **6 CVE-detectie lagen** operationeel: pip-audit, npm audit, Trivy per internal build, Trivy weekly external, Dependabot security updates, secret scanning + push protection
- **pytest now runs in CI** (was silent — 13 tests waren stil broken op main)
- **546 tests green, 0 skipped, 0 warnings** (vs 531+9 skipped+5 warnings baseline)
- **Living doc**: `docs/runbooks/version-management.md` v1.1
- **CVE-triage**: 30 findings gedocumenteerd, 1 critical gefixt, 3 critical tracked upstream-blocked, 26 accepted (transitive in internal-network-only services)

**Consequentie voor ons audit-plan:**
- Fase 1 (secrets): gedeeltelijk gedekt door secret scanning + push protection enabling. Gitleaks-pass niet gedaan maar risico lager.
- Fase 2 (deps): grotendeels gedekt door weekly Trivy + Dependabot + pip-audit + npm audit.
- Fase 6 (dead code): nog niet gedaan.

**Prio-legenda:**
- **P0** — Pre-work: beantwoord fundamentele vraag voordat fix-scope scherp is
- **P1** — HIGH impact, small fix (< 1 dag per SPEC); doen in eerstvolgende sprint
- **P2** — MEDIUM impact; plan in
- **P3** — LOW impact of cosmetisch; rollback-safe refactor

## Pre-work (P0) — beantwoord eerst deze vragen

### PRE-A — PG-role `portal_api` bypassrls check

**Waarom blokkerend:** Als de `portal_api` DB-role `BYPASSRLS` heeft, dan is alle Postgres RLS **cosmetisch** voor normaal gebruik — alle queries zien alle rijen ongeacht `set_tenant`. Dat verandert de interpretatie van:
- F-015 (background tasks zonder `set_tenant`) — werkt alleen als bypassrls=true
- De hele tweede laag defense-in-depth in portal-api

**Hoe verifiëren:**
```sql
-- Via psql als superuser op core-01:
SELECT usename, usebypassrls FROM pg_user WHERE usename = 'portal_api';
-- OF:
\du portal_api
```

**Verwachting:** `portal_api` heeft waarschijnlijk `bypassrls=false` want anders zou F-015 niet werken (bot_poller heeft cross-org access nodig). In dat geval: background tasks werken **toevallig** via een andere role/mechanism, of via `AsyncSessionLocal()` connection die wel bypassrls heeft.

**Vervolgactie afhankelijk van uitkomst:**
- `bypassrls=false` → update F-015 met "RLS kan not bypass — background tasks mogelijk broken of gebruiken aparte role". Nieuw finding: welke role gebruiken ze?
- `bypassrls=true` → **nieuwe CRITICAL finding**: RLS is niet layered defense voor portal_api caller; cosmetische policies.

### PRE-B — Zitadel org_id format en entropy

**Waarom blokkerend:** F-001 exploitability hangt af van hoe voorspelbaar Zitadel org_ids zijn. Als numeriek sequentieel (1, 2, 3...) → enumereerbaar in ~milliseconds. Als UUIDv4 → rate-limit + logging maakt dit onpraktisch.

**Hoe verifiëren:**
```sql
-- Steekproef uit prod DB:
SELECT id, zitadel_org_id FROM portal_orgs ORDER BY id LIMIT 5;
```

Kijk naar `zitadel_org_id`:
- **numeriek** (e.g. "123456789123456789") → Zitadel gebruikt een snowflake-ID; sequentieel-genoeg om te enumeren
- **UUID-achtig** (e.g. "550e8400-e29b-41d4-a716-446655440000") → hoge entropy, onpraktisch

**Documentatie:** Zitadel v2+ gebruikt snowflake IDs (int64, timestamp-prefix + sequence). **Waarschijnlijk enumereerbaar binnen een tijdvenster.**

**Vervolgactie:**
- Snowflake/numeric → F-001 upgrade naar **CRITICAL** wegens lage enumeratie-cost
- UUIDv4 → F-001 blijft HIGH zoals nu

## SEC-010 — Retrieval-API hardening [P1]

**Scope:** Los F-001 (no auth), F-010 (no rate limit), F-014 (user_id trust) samen op in één PR.

**Changes:**
1. Voeg `InternalSecretMiddleware` toe aan `klai-retrieval-api/retrieval_api/main.py` (zelfde patroon als knowledge-ingest, **maar fail-closed** bij lege env var)
2. Voeg optionele JWT-validation middleware toe (via `python-jose`, same pattern als `klai-focus/research-api/app/core/auth.py`) voor callers die namens een user queryen
3. In de middleware: als JWT-context aanwezig is, verifieer `request.body.org_id == token.resourceowner` en `request.body.user_id == token.sub` (behalve voor admin-role)
4. Voeg Pydantic bounds toe aan `RetrieveRequest`:
   - `top_k: int = Field(8, ge=1, le=50)`
   - `conversation_history: list[dict] = Field(default_factory=list, max_length=20)`
   - `kb_slugs: list[str] | None = Field(None, max_length=20)`
5. Voeg rate-limit toe (Redis sliding window, zelfde patroon als partner_dependencies.py — ook voor internal callers)

**Fix-effort:** klein — 1 middleware-bestand + pydantic bounds + 3 tests
**Blast radius:** retrieval-api callers (portal-api, focus, LiteLLM hook) moeten X-Internal-Secret header meesturen. Requires coordinated deploy.
**Test-aandachtspunten:**
- Token-confusion test: JWT voor app X afgewezen bij call naar retrieval-api
- Cross-user test: user A kan geen `user_id=B` passen tenzij admin
- Bounds-test: `top_k=100000` returnt 422

**Acceptatiecriteria (EARS):**
- **WHILE** retrieval-api receives a request **THE** system **SHALL** require a valid X-Internal-Secret header OR a valid Zitadel JWT.
- **WHEN** a request contains `org_id` or `user_id` in the body **IF** a JWT is present **THE** system **SHALL** reject the request if `body.org_id != token.resourceowner` OR `body.user_id != token.sub` (unless caller role is admin).
- **WHEN** the service starts **IF** no INTERNAL_SECRET is configured **THE** service **SHALL** fail to start.

## SEC-011 — Knowledge-ingest fail-closed auth [P1]

**Scope:** F-003 (middleware) + F-012 (route-helper).

**Changes:**
1. `knowledge_ingest/config.py` — add `model_validator(mode="after")` dat crasht bij lege `knowledge_ingest_secret`
2. `knowledge_ingest/middleware/auth.py` — verwijder de fail-open guard (lines 19-21); secret is nu altijd gezet
3. `knowledge_ingest/routes/ingest.py:54-60` — idem: verwijder de `if not settings.knowledge_ingest_secret: return` branch
4. Check alle andere routes in `knowledge_ingest/routes/*.py` op hetzelfde patroon (mogelijk aanwezig in crawl.py, knowledge.py, personal.py, stats.py, taxonomy.py)

**Fix-effort:** extreem klein — 3 regels verwijderen, 1 validator toevoegen
**Blast radius:** deploys met lege env var crashen bij startup — **wil je weten**
**Test-aandachtspunten:**
- Startup-test: service crasht als `KNOWLEDGE_INGEST_SECRET` leeg is
- Runtime-test: 401 bij lege/foute header (na config gezet)

**Acceptatiecriteria (EARS):**
- **WHEN** knowledge-ingest starts **IF** `KNOWLEDGE_INGEST_SECRET` is empty **THE** service **SHALL** log an error and exit non-zero.
- **WHILE** the service is running **THE** middleware en route-level helpers **SHALL** return 401 for any request without a valid `X-Internal-Secret` header.

## SEC-012 — JWT audience verification mandatory [P1]

**Scope:** F-002 (scribe), F-004 (focus).

**Changes per service:**
1. **scribe-api** (`klai-scribe/scribe-api/app/core/auth.py:69`): vervang `options={"verify_aud": False}` met expliciete `audience=settings.zitadel_api_audience`. Voeg config-validator toe dat de env var verplicht maakt.
2. **research-api** (`klai-focus/research-api/app/core/auth.py:67-74`): verwijder de `if/else` — maak audience-verificatie verplicht via pydantic-settings validator.

**Fix-effort:** klein — <20 LOC per service
**Blast radius:** Elke Zitadel-app die callers maakt moet dezelfde audience in de token request hebben. Requires checking existing client configs.
**Test-aandachtspunten:**
- Token issued for app A → call to scribe-api → 401
- Token issued for scribe audience → call to scribe-api → 200
- Server start zonder audience env var → crash bij startup

**Acceptatiecriteria (EARS):**
- **WHEN** a service starts **IF** its ZITADEL_API_AUDIENCE is not configured **THE** service **SHALL** fail to start.
- **WHILE** a service receives a request with a Bearer token **IF** the token's `aud` claim does not match the configured audience **THE** service **SHALL** return 401.

## SEC-004 — Defense-in-depth auth middleware [P2]

**Scope:** F-005 (focus+scribe no middleware), F-006 (moneybird), F-009 (connector).

**Changes:**
1. **focus + scribe** — voeg een `AuthMiddleware` toe (zelfde patroon als klai-connector) die alles behalve `/health` forceert. `Depends(get_current_user)` blijft bestaan voor user-object-access.
2. **portal webhooks** (`app/api/webhooks.py`): token-check fail-closed maken, `hmac.compare_digest` gebruiken, 401 returnen (niet 200), log bron-IP
3. **klai-connector** (`app/middleware/auth.py:75-78`): `hmac.compare_digest` voor portal_secret vergelijking

**Fix-effort:** medium — nieuwe middleware classes in 2 services, kleinere fixes in andere 2
**Blast radius:** bij deploy: alles via nieuwe middleware — tests moeten blanket auth-test hebben op elke route
**Dependencies:** SEC-012 moet eerst (audience config ter plaatse voor middleware)

**Acceptatiecriteria (EARS):**
- **WHILE** focus-api and scribe-api receive any request **IF** the path is not `/health` **THE** middleware **SHALL** verify a valid Zitadel JWT before handler runs.
- **WHEN** portal webhooks receive a Moneybird event **IF** token check fails OR token is not configured **THE** endpoint **SHALL** return 401 and log source IP.

## SEC-005 — Internal-endpoint hardening [P2]

**Scope:** F-007 (portal internal endpoints trust query-param org_id).

**Changes:**
1. INTERNAL_SECRET rotation-schema documenteren in `deployment.md` (klai-infra) — target: kwartaal-rotatie
2. Rate-limiting toevoegen op internal endpoints (Redis sliding window, bijv. 100 req/min per IP)
3. Audit-log van alle internal calls naar `portal_audit_log` (org_id uit request, caller_ip, endpoint, timestamp)
4. Overwegen: vervangen van single shared secret door mTLS tussen portal-api en callers

**Fix-effort:** medium-groot — observability stack + evt. mTLS
**Blast radius:** elke internal caller moet secret kennen (al geldt); audit-log mogelijk volume (keep 30d)

**Acceptatiecriteria (EARS):**
- **WHEN** an internal endpoint is called **THE** service **SHALL** write an entry to `portal_audit_log` with org_id, caller_ip, endpoint path, timestamp.
- **WHILE** internal endpoints are enabled **THE** rate-limiter **SHALL** enforce max 100 requests per minute per caller IP.

## SEC-006 — Widget JWT revocation [P3]

**Scope:** F-008.

**Keuze tussen 3 opties** (discussie met user):

| Optie | Aanpak | Voor- | Nadelen |
|---|---|---|---|
| A | Korter TTL (5-15min) + refresh-endpoint | Simpel | Meer traffic; complexer widget code |
| B | Cross-check kb_ids tegen DB bij elke call | Real-time revocation | Extra DB-hit per chat-request |
| C | JWT-blacklist (Redis) bij revoke-operatie | Minimale impact per call | Extra complexity; Redis dependency |

**Aanbeveling:** Optie B — 1 extra DB-query per chat-call is acceptabel binnen de 2-3s SLA, en de simpelste fix.

**Fix-effort:** klein — 1 query in `_auth_via_session_token`
**Blast radius:** alle widget-calls krijgen 1 extra DB roundtrip

## SEC-007 — Code-quality / correctness [P3]

**Scope:** F-011 (connector cache), F-015 MEDIUM-documentation.

**Changes:**
1. `klai-connector/app/middleware/auth.py:37-41` — vervang insertion-order eviction met `collections.OrderedDict` + `move_to_end()` voor echte LRU; of accepteer insertion-order en documenteer het
2. `klai-portal/backend/app/services/bot_poller.py`, `invite_scheduler.py`, `connector_credentials.py:165` — add `@MX:NOTE: cross-org system task — intentional RLS bypass` comment

**Fix-effort:** extreem klein — comments + 5 LOC
**Blast radius:** geen functional change

## Implementation sequentie

Voorgestelde volgorde om SPECs op te stellen en te laten reviewen:

```
[PRE-A, PRE-B]  — beantwoorden voordat SPECs definitief worden
    ↓
[SEC-011, SEC-012]  — parallel; klein en onafhankelijk
    ↓
[SEC-010]  — afhankelijk van PRE-B (Zitadel format) voor test-scenarios
    ↓
[SEC-004]  — na SEC-012 (audience config aanwezig)
    ↓
[SEC-005]  — onafhankelijk, kan parallel met SEC-004
    ↓
[SEC-006, SEC-007]  — laatste; rollback-safe
```

## Open pick-up points

Als deze audit gepauzeerd wordt:

1. **Voor het fixen begint**: beantwoord PRE-A en PRE-B — zonder deze verandert mogelijk de severity/scope van SEC-010 en SEC-007-annotaties.
2. **Voor SEC-004**: lees `klai-focus/research-api/app/api/notebooks.py _get_notebook_or_404` om te weten of route-dep pattern echt consistent is, of dat middleware nodig is als safety-net.
3. **Als deze hele fix-roadmap executed wordt**: run Fase 4 (injection/SAST) daarna — sommige SEC-fixes raken ook injection-surface (bounded inputs = minder payload-fuzzing mogelijk).

## Nog te doen uit eerdere fases (uit 00-plan.md)

Deze audit heeft Fase 3 grondig gedaan. Overig ligt nog open:

- **Fase 0** — Inventaris & risicokaart (niet strikt gedaan; deels gedekt door scope-sectie in 04-tenant-isolation.md)
- **Fase 1** — Secrets & config audit (gitleaks, SOPS consistency) — todo
- **Fase 2** — Dependencies audit (pip-audit, npm audit, trivy) — todo
- **Fase 4** — Input validation & injection (semgrep/bandit) — todo
- **Fase 5** — API hardening (CORS, rate limiting, Caddy headers) — todo
- **Fase 6** — Dead code (vulture, knip) — todo
- **Fase 7** — Synthesis (**deze doc IS een living versie van 7** — blijft groeien)

Fase 1+2+6 kunnen parallel met SEC-004 t/m SEC-012 fixes. Fase 4+5 bij voorkeur na SEC-010..SEC-012 + SEC-004.

## Changelog

| Datum | Wijziging |
|---|---|
| 2026-04-19 | Initial roadmap — 9 fix-groepen + 2 pre-work items. Gebaseerd op Fase 3 (findings F-001 t/m F-016). |
| 2026-04-19 (later) | SEC-001/002/003 IDs hernoemd naar SEC-010/011/012 wegens ID-collision met bestaande SPECs (NEN 7510, ISO 27001, RLS coverage). Bestaande SPECs blijven unchanged. |
| 2026-04-19 Wave 1 deploy | SEC-010 LIVE op main — retrieval-api CRITICAL-fix. 5 commits on main + klai-infra SOPS update. Smoke-tested: 401 zonder X-Internal-Secret, 200 met, /health public. |
| 2026-04-19 Wave 2 deploy | SEC-011 + SEC-009 LIVE op main — knowledge-ingest fail-closed + SERVERS.md complete. 1 parent commit (3ca34341) + klai-infra commit (58f0b60). 31 new tests pass. |
| 2026-04-19 Wave 2C PAUSE | SEC-012 (JWT audience) uitgesteld: parallel session startte major scribe rebuild (SPEC-VEXA-003). Re-visit na scribe-rebuild. |
| 2026-04-19 New findings | SEC-013 (vexa-bot supply-chain HIGH + docs-app auth) + SEC-014 (taxonomy.py:83 portal_internal_token fail-open) toegevoegd als follow-ups. |
| 2026-04-19 Parallel session | Dependency audit + CVE scanning infrastructure overhaul in aparte sessie landde op main: 26 images pinned, 6 CVE-detectielagen active, 1 critical CVE gefixt (LiteLLM), pytest in CI, 546 green tests. Dekt Fase 1+2 van het audit-plan grotendeels. |
| 2026-04-19 Wave 3 | SEC-005/006/007/008 gebouwd (4 parallel agents), gegroepeerd naar 5 commits op main (739a3177, 78d7ce26, 1e3f9945, 11d6c28f, 0dcd8047) + klai-infra 41ff469 (3 runbooks). Smoke-tested: portal-api internal endpoint schrijft audit row naar portal_audit_log, klai-connector warnt bij ontbrekende audience, widget JWT revocation via DB cross-check. 73+ new tests, alle groen. |
| 2026-04-19 Fase 4 | SAST scan afgerond. 0 critical, 7 findings (F-023..F-029, all MEDIUM/LOW). F-026 false positive. Rapport in .moai/audit/05-injection.md. |
| 2026-04-19 Ops rollout | SEC-008 defense-in-depth geactiveerd: KLAI_CONNECTOR_ZITADEL_AUDIENCE (Zitadel app 365340910193475592) en CADDY_DEV basic-auth (bcrypt 14, 32-char random password) in SOPS. Caddyfile + compose bijgewerkt. Gotcha gevonden: sync-env.yml doet automatische `$` → `$$` escape voor docker-compose, SOPS moet dus single-\$ bcrypt opslaan (niet pre-escaped). Smoke-tested: dev.getklai.com basic-auth werkt, klai-connector audience actief. |
| 2026-04-19 Fase 6 | Dead-code scan afgerond (vulture + knip + CodeIndex orphans). 22 Python findings + 7 TS findings. DEAD-008 was false positive (deferred feature per SPEC-KB-007 AC-7). Rapport in .moai/audit/07-dead-code.md. |
| 2026-04-19 Vexa audit | Vexa stack audit na SPEC-VEXA-003 rollout. 8 findings (V-001..V-008 / F-030..F-037). 2 HIGH (dode /bots route, docker.sock mount). Rapport in .moai/audit/08-vexa.md. |
| 2026-04-19 Wave 4 | SEC-013 + SEC-016 + SEC-019 landed op main (5 commits). F-030/032/033/035 Vexa hardening, noqa+encoding cleanup, ~1880 LOC dead code weg. Lesson learned: parallel Agent subprocesses kunnen elkaars working tree mid-run resetten — sequentieel of worktree-isolation nodig bij file-overlap. 4 van 5 agents moesten handmatig re-applied. Smoke-tests groen: /bots/ → api-gateway 200, webhook wrong token 401, dev.getklai 401/passthrough. |
| 2026-04-19 Doc sync | Roadmap gesynct na alle deploys. SEC-012 deblokkeerd (scribe rebuild klaar). Open tickets hernummerd: SEC-020 (vexa-bot+docs-app extern audit, was SEC-013 scope), SEC-021 (runtime-api docker-socket-proxy, F-031), SEC-022 (vexa-bots network egress, F-037). |
| 2026-04-19 SEC-018 / SEC-020 | Dockerfile USER audit afgerond: knowledge-ingest + bge-m3-sparse draaien nu als non-root `app` (uid 1000). caddy intentional-root gedocumenteerd (privileged-port binding is accepted risk — single edge reverse-proxy, isolated network). docs-app `lib/auth.ts` source-inspection: JWKS Bearer-validatie tegen Zitadel issuer werkt voor BFF-proxy flow (portal forwards access_token). Audience-check ontbreekt maar valt onder SEC-012 scope. vexa-bot-manager audit deferred (externe repo). |
| 2026-04-19 DEAD-batch triage | 8 open dead-code items definitief getrieerd. DEAD-005 = false positive (dynamic getattr voor moneybird_product_*). DEAD-020/021 = al-verwijderd (crypto.py + adapters/ directory weg in eerdere refactor). DEAD-010 (reranker_url/model in knowledge-ingest) + DEAD-022 (preferred_language method in mailer) daadwerkelijk verwijderd. DEAD-004/009/011 geannoteerd met @MX:NOTE/TODO en behouden als reserved-future-use (Vexa admin API, docs-app service calls, sparse-index-on-disk flag). Fase 6 formeel afgesloten. |
| 2026-04-19 SEC-018 gpu-01 | bge-m3-sparse image herbouwd op gpu-01 met nieuwe Dockerfile (USER app, uid 1000). Volume `/var/lib/docker/volumes/klai-gpu_bge-models/_data` eerst chowned naar 1000:1000 (BGE-M3 modelgewichten). Container recreated, health=healthy, /embed_sparse smoke-test 200 met sparse vector response. Dockerfile op gpu-01 heeft geen git repo (handmatig beheerd) — future-follow-up: CI workflow voor deze image analoog aan retrieval-api.yml. |
| 2026-04-19 follow-up tasks | 7 nieuwe tasks op TODO: #25 bge-m3 (DONE), #26 SEC-012 defense-in-depth, #27 SEC-004, #28-30 vexa externe audits, #31 SEC-023 Playwright E2E, #32 BFF streaming upload, #33 CSRF review (DONE — al afgedekt via SessionMiddleware), #34 PORTAL_INTERNAL_TOKEN runtime check (DONE), #35 DEAD-022 rules entry (DONE, mailer.md aangemaakt). |
| 2026-04-19 SEC-012 deploy | research-api image rebuild + deploy via CI (commit 655f168a). Container op core-01 recreated 17:57, startup complete zonder pydantic error (validator passes — audience env var set). Smoke-test via portal-api container: `/v1/health` 200, `/v1/notebooks` met bogus Bearer → 401 "Ongeldig of verlopen token". Fail-closed audience check actief. |
| 2026-04-19 SEC-004 deploy | AuthGuardMiddleware in research-api + scribe-api LIVE (commit eb71018a). CI groen, containers recreated 18:07/18:08. Smoke-test: /v1/notebooks + /v1/transcriptions zonder header → 401 "Authorization required" (middleware block vóór route), met bogus Bearer → 401 (doorgepakt door auth-dep). /health → 200 (exempt). Middleware order correct: CORS → RequestContext → AuthGuard → route. |
| 2026-04-19 SEC-020/021/022 | Infra SPECs + externe audit afgerond (commit 1a8f09b5). `.moai/audit/10-vexa-external-audit.md`: Vexa auth-contract solide, 1 MEDIUM (ALLOW_PRIVATE_CALLBACKS=1 SSRF), 2 INFO. `.moai/specs/SPEC-SEC-021` (runtime-api → docker-socket-proxy, EARS + rollback plan) en `.moai/specs/SPEC-SEC-022` (vexa-bots egress allowlist, 4-fase ipset/iptables). Implementation apart ticket wegens runtime-risk + ops-window. |
