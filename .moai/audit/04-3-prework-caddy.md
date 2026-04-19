# Fase 3 — Pre-work (PRE-A + PRE-B) + Caddy verify

**Datum:** 2026-04-19
**Scope:** Beantwoorden van open vragen uit `04-tenant-isolation.md` appendix A.5 + `99-fix-roadmap.md` pre-work. Verifiëren van Caddy-config op core-01.

## PRE-A — PG-role `bypassrls` check — RESULT

**Bevinding:** `portal_api` heeft **`bypassrls=false`** — RLS is echte defense-in-depth ✓

```
Rolename          | bypassrls | superuser
------------------|-----------|----------
klai              | t         | t
portal_api        | f         | f
portal_api_dev    | f         | f
zitadel           | f         | f
litellm           | f         | f
glitchtip         | f         | f
gitea             | f         | f
vexa              | f         | f
grafana_reader    | f         | f
```

**Portal-api connecteert als `portal_api`** (bevestigd via pitfall `"password authentication failed for user 'portal_api'"`). RLS policies worden dus daadwerkelijk afgedwongen voor runtime queries.

**Implicatie voor F-015 (background tasks):**
- `bot_poller.py`, `invite_scheduler.py`, `connector_credentials.py:165` gebruiken `AsyncSessionLocal()` → zelfde engine → zelfde user `portal_api`
- Dus ze zijn **ook** subject aan RLS
- Maar die tasks moeten cross-org lezen (alle meetings, alle iCal UIDs, alle DEKs)
- Hoe werkt dit? **Te verifiëren**:
  - Optie 1: De betreffende tabellen hebben permissive RLS (bv. `vexa_meetings` is "split: SELECT scoped, INSERT permissive" per docs — maar dan zou bot_poller niets zien)
  - Optie 2: `app.current_org_id` is NULL → standaard "all rows visible" via een `IS NULL OR org_id = X` policy
  - Optie 3: Background tasks gebruiken een apart engine/user — maar ik vond dat niet in de code

→ **Nieuwe parking-lot item**: inspecteer de echte RLS policies op `vexa_meetings`:
```sql
SELECT tablename, policyname, cmd, qual FROM pg_policies WHERE tablename = 'vexa_meetings';
```

## PRE-B — Zitadel org_id entropy — RESULT

**Bevinding:** Zitadel org_ids zijn **18-digit Snowflake-numerics** — ENUMEREERBAAR.

```sql
SELECT id, zitadel_org_id, LENGTH(zitadel_org_id) FROM portal_orgs ORDER BY id LIMIT 5;
-- 1 | 362757920133283846 | 18
-- 8 | 368884765035593759 | 18
```

**Analyse:**
- 18 digits → past in int64 — past op Zitadel's snowflake-schema (timestamp-prefix)
- Leading digits `36...` gemeenschappelijk = orgs gecreëerd binnen ~1 jaar van elkaar
- Snowflake encoding: `[timestamp_ms : 42 bits] [machine : 10 bits] [sequence : 12 bits]`
- Space-between-sequential-orgs: ~10¹⁶ mogelijke IDs per dag, maar **alleen timestamps binnen je org-creation range** zijn valide
- Voor een actieve platform: blast radius binnen eventuele fuzzing = **enkele honderden tot duizenden geldige IDs per maand**

**Impact:**
Met rate-limit-loze retrieval-api (F-001) en 60ms Qdrant-queries, kan een attacker in uur ~100k probes doen → ruwweg alle actieve org_ids in een tijdvenster vinden. Zodra ze er één hebben → volledig retrieval-access voor die tenant.

**F-001 severity → CRITICAL** (upgrade van HIGH).

## Caddy verify — FINDINGS

De gepubliceerde public routes in SERVERS.md waren incompleet. Complete lijst uit live `/opt/klai/caddy/Caddyfile`:

| Route | Target | Auth layer | Rate limit | Commentaar |
|---|---|---|---|---|
| `auth.getklai.com` | Zitadel | Zitadel zelf | intern | OK |
| `llm.getklai.com/health/liveliness` | LiteLLM | — | — | Public health OK |
| `grafana.getklai.com` | Grafana | basic auth (Caddy) | — | OK |
| `errors.getklai.com` | GlitchTip | GlitchTip auth | — | OK |
| `logs-ingest.getklai.com` | VictoriaLogs | token-check | — | **F-021** — single-secret gate |
| **`connector.getklai.com`** | **klai-connector:8200** | **Zitadel introspection + portal bypass (F-009)** | **60/min/IP** | **F-017 NEW** — contradicts "internal only" in SERVERS.md |
| `dev.getklai.com/*` | portal-api-dev:8010 | Zitadel (same as prod?) | — | **F-018 NEW** — dev public |
| `dev.getklai.com` (spa) | static | — | — | idem |
| `*.getklai.com/partner/*` | portal-api:8010 | partner key or widget JWT | per-IP | OK — documented |
| `*.getklai.com/api/signup, /api/billing/*` | portal-api:8010 | sensitive rate limit 10/min | 10/min | OK |
| `*.getklai.com/api/*` | portal-api:8010 | Zitadel via `_get_caller_org` | — | OK |
| `*.getklai.com/research/*` | research-api:8030 | `Depends(get_current_user)` | 50MB body | F-005 |
| `*.getklai.com/scribe/*` | scribe-api:8020 | `Depends(get_current_user_id)` | 20/min, 100MB | F-002, F-005 |
| `*.getklai.com/bots/*` | vexa-bot-manager:8000 | **UNKNOWN** | 10/min/IP | **F-020 NEW** |
| `*.getklai.com/docs/*` | docs-app:3010 | **UNKNOWN** | 60/min/IP | **F-022 NEW** |
| `*.getklai.com/kb-images/*` | garage:3902 | anonymous reads (by design) | — | **F-019** — intentional (browser access) |
| `*.getklai.com` (catch-all) | portal SPA | — | — | OK |

**NIET publiek:** retrieval-api, knowledge-ingest, klai-mailer, klai-knowledge-mcp, Qdrant, FalkorDB, databases, Ollama, LibreChat-tenants (verborgen achter `chat-{slug}` specifiek) — ✓ F-001 en F-003/F-012 blast radius blijft Docker-intern.

## Nieuwe findings

### F-017 — klai-connector is PUBLIEK via `connector.getklai.com` [HIGH]

**Contradictie met documentatie:** SERVERS.md (regel 110) zegt `klai-net` voor connector = intern. Caddy exposed het op een aparte subdomain.

**Auth layers:**
1. Zitadel introspection (`klai-connector/app/middleware/auth.py:_introspect`)
2. Portal bypass via `portal_caller_secret` (F-009) — **now reachable from public internet**

**Risico's:**
- F-009 severity upgrade: portal bypass-secret leak = full internet-reachable auth bypass
- Audience-verificatie bij Zitadel introspection: onduidelijk — introspection endpoint returnt claims maar of `aud`-check actief is hangt af van Zitadel config
- Token-reuse cross-application analog to F-002 — te verifiëren

**Aanbeveling (toevoegen aan SEC-004):**
1. Verifieer of Zitadel introspection de `aud` claim meegeeft en of de connector middleware hem ook echt checkt tegen een configured audience
2. Rotatie-schema voor `portal_caller_secret` (eerder al in SEC-005 scope — verplaats naar SEC-004 gezien publieke surface)
3. `hmac.compare_digest` voor bypass-secret compare
4. Overwegen: mTLS tussen portal-api en klai-connector in plaats van single secret

### F-018 — `dev.getklai.com` is publiek bereikbaar [MEDIUM]

**Route:** `dev.{$DOMAIN}` → portal-api-dev:8010 + dev-spa static.

**Risico's:**
- Dev env heeft vaak zwakkere auth (dev keys, debug endpoints enabled, test users met zwakke passwords)
- DB-role `portal_api_dev` bestaat (uit PRE-A) — **verdient aparte DB?** (zo ja: dev data zou niet overlappen met prod — te verifiëren)
- Als dev env dezelfde Zitadel-app gebruikt: cross-env token-reuse

**Open vragen:**
- Is dev env elke PR/branch een nieuwe instance, of één gedeelde dev env?
- Auth: zelfde Zitadel of aparte Zitadel-app (andere `aud`)?
- Data: eigen database `klai_dev`, of RLS-separated binnen `klai`?

**Aanbeveling:**
1. Dev env achter IP-allowlist of basic auth (Caddy-laag)
2. Audit: wat is er anders aan dev vs prod?

### F-020 — `/bots/*` → vexa-bot-manager publiek [MEDIUM]

**Auth status:** Niet onderzocht in deze audit (geen code-review op `klai-core-vexa-bot-manager-1` container). Rate-limit 10/min/IP.

**Risico als geen auth:** Attacker kan Vexa-bots spawnen (gebruikt resources, mogelijk meeting-join manipulatie).

**Aanbeveling:** apart audit-item Fase 3-deep.

### F-021 — `logs-ingest.getklai.com` is token-gated [LOW]

**Code in Caddy:**
```caddy
@logs-ingest host logs-ingest.{$DOMAIN}
handle @logs-ingest {
    @no_token {
        # check
    }
    respond @no_token 401
    reverse_proxy victorialogs:9428 {
        # ...
    }
}
```

Enkel token-gate (single secret). Als token leakt → log-injection mogelijk. Acceptabel als log-integrity niet critical is (zie Alloy die écht intern logt).

### F-022 — `/docs/*` → docs-app publiek [UNKNOWN severity]

**Auth status:** `docs-app:3010` niet onderzocht. Is dit de KB-reader voor ingelogde users, of een marketing-docs site?

**Te doen:** Check `klai-docs` repo voor endpoints en auth-middleware.

## Severity updates na pre-work

| ID | Was | Nu | Reden |
|---|---|---|---|
| **F-001** | HIGH | **CRITICAL** | Zitadel org_ids zijn enumereerbaar snowflake numerics; exploit-cost laag |
| **F-009** | MEDIUM | **HIGH** | Connector is public via Caddy — bypass-secret leak = internet-reachable |
| **F-014** | HIGH | **HIGH** (same) | Impact reikt over tenant-grens met lage cost door F-001 upgrade — blijft HIGH; combined-severity CRITICAL |

## SEC-roadmap updates

**Nieuwe fix-groep:**

### SEC-008 — Caddy exposure hardening [P1]

**Scope:** F-017 (connector public), F-018 (dev public), F-020+F-022 (unknown auth)

**Changes:**
1. **F-017**: Move klai-connector routing achter portal-api proxy OF behouden public maar hardening:
   - mTLS tussen portal-api en connector
   - Audience-verificatie expliciet in connector middleware
   - Constant-time compare voor bypass-secret
2. **F-018**: Dev env hardening:
   - Caddy basic auth voor `dev.{$DOMAIN}` OF IP-allowlist OF aparte DNS-naam
   - Aparte Zitadel-app voor dev (`aud=klai-dev`)
   - Verifieer dat `portal_api_dev` een aparte DB gebruikt
3. **F-020**: vexa-bot-manager auth-audit; voeg auth middleware toe als ontbrekend
4. **F-022**: docs-app auth-audit; idem

**Severity:** P1 — publiek bereikbaar tabelvorm zonder documented audit

### SEC-009 — SERVERS.md documentatie-drift [P3]

SERVERS.md is out-of-date t.o.v. Caddyfile. Minimaal opnemen:
- `connector.{DOMAIN}` → klai-connector (public)
- `dev.{DOMAIN}` → portal-api-dev (public)
- `/bots/*` → vexa-bot-manager (public)
- `/docs/*` → docs-app (public)
- `/kb-images/*` → garage (public)
- `logs-ingest.{DOMAIN}` → VictoriaLogs (token-gated)

Fix-effort: trivial (doc update).

## Open items (nieuwe parking lot)

- [ ] Verifieer RLS policy voor `vexa_meetings` — hoe werkt `bot_poller.py` als `portal_api` bypassrls=false?
- [ ] Controleer dev-env DB: apart database of RLS-separation?
- [ ] Audit `vexa-bot-manager` auth (F-020)
- [ ] Audit `docs-app` auth (F-022)
- [ ] Meten: welke Zitadel-apps delen de audience? Token-confusion cross-app
- [ ] Connector audience-check in introspection middleware

## Changelog

| Datum | Wijziging |
|---|---|
| 2026-04-19 | PRE-A + PRE-B uitgevoerd via SSH core-01 (read-only). Caddyfile gereviewed. F-017 t/m F-022 toegevoegd. F-001 → CRITICAL, F-009 → HIGH. SEC-008 + SEC-009 toegevoegd aan roadmap. |
