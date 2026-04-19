# Security Audit — Fix Roadmap

> **Living document.** Groeit mee met elke afgeronde audit-fase. Bevat geconsolideerde fix-groepen uit alle findings.

**Laatst bijgewerkt:** 2026-04-19 na Fase 3 (Tenant isolation)
**Input documenten:**
- `.moai/audit/04-tenant-isolation.md` — Fase 3.1 findings (F-001 t/m F-011)
- `.moai/audit/04-2-query-inventory.md` — Fase 3.2+3.3 findings (F-012 t/m F-016)

## Overzicht — prioriteit matrix

> **Update 2026-04-19 post-Caddy-verify:** F-001 upgraded to CRITICAL (Zitadel org_ids zijn enumereerbaar snowflake numerics). F-009 upgraded to HIGH (klai-connector is publiek). Nieuwe SEC-008 en SEC-009 toegevoegd. Zie `.moai/audit/04-3-prework-caddy.md`.

| Prio | SEC-ID | Titel | Findings | Services | Status |
|---|---|---|---|---|---|
| ~~P0~~ | ~~PRE-A~~ | PG-role `bypassrls` verificatie | F-015 context | — | **DONE** — portal_api has bypassrls=false |
| ~~P0~~ | ~~PRE-B~~ | Zitadel org_id entropy check | F-001 context | — | **DONE** — 18-digit Snowflake, enumereerbaar → F-001 → CRITICAL |
| **P0** | SEC-010 | Retrieval-API hardening (now CRITICAL) | F-001, F-010, F-014 | retrieval-api | escalated from P1 |
| **P1** | SEC-011 | Knowledge-ingest fail-closed auth | F-003, F-012 | knowledge-ingest | — |
| **P1** | SEC-012 | JWT audience verification mandatory | F-002, F-004 | scribe, focus | — |
| **P1** | SEC-008 | Caddy exposure hardening (NEW) | F-017, F-018, F-020, F-022 | connector, dev env, vexa-bots, docs | **connector is PUBLIC** contra SERVERS.md |
| **P2** | SEC-004 | Defense-in-depth auth middleware | F-005, F-006, F-009 | focus, scribe, portal, connector | vereist SEC-012 eerst; F-009 nu HIGH |
| **P2** | SEC-005 | Internal-endpoint hardening | F-007 | portal | — |
| **P3** | SEC-006 | Widget JWT revocation | F-008 | portal partner API | — |
| **P3** | SEC-007 | Code-quality / correctness | F-011, F-015 doc | connector, portal background | — |
| **P3** | SEC-009 | SERVERS.md doc-drift (NEW) | F-017, F-018, F-020, F-022 | klai-infra docs | trivial |

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
