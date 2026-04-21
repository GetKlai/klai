# Fase 3 — Tenant Isolation Findings

**Datum:** 2026-04-19
**Scope onderzocht:** klai-portal, klai-retrieval-api, klai-knowledge-ingest, klai-connector, klai-focus, klai-scribe, klai-mailer
**Status:** initiële scan afgerond — dieptescan per query-site nog todo

> ⚠️ Deze doc bevat geen exploit-code en geen secrets. Blijft in de repo acceptabel.

## TL;DR

De portal-api heeft een **solide defense-in-depth** (app-laag `_get_X_or_404(id, org_id)` helper + Postgres RLS via `app.current_org_id`). De downstream services zijn **uneven qua auth**:

- `retrieval-api`: **zero auth** (geen middleware, geen route deps). Intern-only — acceptabel mits netwerk segregatie houdt, maar geen defense-in-depth.
- `knowledge-ingest`: X-Internal-Secret middleware, maar **fail-open als env var leeg**.
- `connector`: Zitadel introspection + bypass-secret voor portal. Single-factor, long-lived.
- `focus` (research-api) en `scribe-api`: **publiek bereikbaar** via Caddy, auth op route-niveau (geen middleware-safety-net).
- `scribe-api`: JWT **audience verification is hard-coded disabled** (`verify_aud: False`).
- `focus`: audience verification **optioneel** (disabled als env var leeg).

Geen actieve cross-tenant leak bevestigd. Wel meerdere defense-in-depth zwaktes die één logicabug ver kunnen versterken.

---

## Architectuur — wat staat er

### Portal-API: twee-laags isolatie (goed)

| Laag | Mechanisme | Bron |
|---|---|---|
| 1 — app | `_get_{model}_or_404(id, org_id, db)` helpers | `app/services/access.py`, route-bestanden |
| 2 — DB | Postgres RLS via `app.current_org_id` | `app/core/database.py:51` `set_tenant()` |

`_get_caller_org` (`app/api/dependencies.py:43`, `@MX:ANCHOR fan_in=8`):
1. Valideer Bearer token via Zitadel introspection
2. Lookup `PortalOrg + PortalUser` via `zitadel_user_id`
3. Call `set_tenant(db, org.id)` → PG session var
4. Bind `org_id` / `user_id` aan structlog contextvars

Kritieke connectie-pinning in `get_db()`: eerste `session.connection()` koppelt verbinding voor de hele sessie, zodat `set_config('app.current_org_id', ..., false)` zichtbaar blijft voor RLS (bug-fix van eerdere SPEC-KB-020).

### RLS coverage (van `portal-security.md`)

| Modus | Tabellen |
|---|---|
| Strict | portal_groups, portal_knowledge_bases, portal_group_products, portal_group_memberships, portal_group_kb_access, portal_kb_tombstones, portal_user_kb_access, portal_retrieval_gaps, portal_taxonomy_nodes, portal_taxonomy_proposals, portal_user_products |
| Permissive | portal_users, portal_connectors |
| Split (SELECT scoped, INSERT permissive) | portal_audit_log, product_events, vexa_meetings |

### Caddy routing (publieke oppervlak — van `SERVERS.md`)

| Route | Service | Auth layer |
|---|---|---|
| `*.getklai.com/api/*` | portal-api | Zitadel Bearer + `_get_caller_org` |
| `*.getklai.com/research/*` | klai-focus | Route-level `Depends(get_current_user)` |
| `*.getklai.com/scribe/*` | klai-scribe | Route-level `Depends(get_current_user_id)` |
| `*.getklai.com/partner/v1/*` | portal-api (partner router) | `Depends(get_partner_key)` + widget JWT |
| `*.getklai.com/internal/*` | portal-api internal router | `_require_internal_token()` X-Internal-Secret |

**Niet publiek bereikbaar (Docker-intern):**
- retrieval-api (:8040)
- knowledge-ingest (:8000)
- klai-connector (:8200)
- klai-mailer
- klai-knowledge-mcp

---

## Findings

### F-001 — retrieval-api heeft geen enkele authenticatie [HIGH]

**Locatie:** `klai-retrieval-api/retrieval_api/main.py:59`, `api/retrieve.py:55`, `api/chat.py:22`

**Bewijs:**
```python
# main.py
app.add_middleware(RequestContextMiddleware)  # alleen logging — geen auth
app.include_router(retrieve_router, prefix="")
app.include_router(chat_router, prefix="")
```

```python
# api/retrieve.py
@router.post("/retrieve", response_model=RetrieveResponse)
async def retrieve(req: RetrieveRequest) -> RetrieveResponse:
    # Geen Depends(...), geen middleware-check
```

`RetrieveRequest.org_id: str` komt direct uit de POST-body en wordt gebruikt om Qdrant collecties te queryen.

**Blast radius:** Als retrieval-api bereikt wordt (netwerk-compromise, future Caddy misconfig, compromised portal-api), kan de caller **elke tenant's Qdrant data lezen** door een andere `org_id` mee te geven.

**Mitigerend:**
- SERVERS.md bevestigt: port 8040, **niet** in publieke Caddy routes
- Docker-intern netwerk isolatie

**Aanbeveling:**
1. Voeg X-Internal-Secret middleware toe (zelfde patroon als knowledge-ingest), met `hmac.compare_digest`
2. Fail-closed default (leeg secret = reject, niet skip)
3. Optioneel: Zitadel JWT validation + cross-check `req.org_id` tegen token `resourceowner:id`

---

### F-002 — scribe-api accepteert tokens van elke Zitadel-applicatie [HIGH]

**Locatie:** `klai-scribe/scribe-api/app/core/auth.py:64-70`

**Bewijs:**
```python
payload = jwt.decode(
    token, key,
    algorithms=["RS256"],
    issuer=settings.zitadel_issuer,
    options={"verify_aud": False},   # ← hard-coded disabled
)
```

**Impact:** Een JWT dat is uitgegeven voor een **andere Zitadel-app** (bijv. een 3rd-party integratie op dezelfde Zitadel instance) wordt geaccepteerd door scribe-api, zolang issuer klopt. Token-reuse attack mogelijk.

**Aanbeveling:** Voeg `ZITADEL_API_AUDIENCE` toe aan de scribe config (Zitadel project-ID voor klai-scribe), en verplicht audience-verificatie.

---

### F-003 — knowledge-ingest faalt open als secret niet geconfigureerd is [HIGH]

**Locatie:** `klai-knowledge-ingest/knowledge_ingest/middleware/auth.py:19-21`

**Bewijs:**
```python
if not settings.knowledge_ingest_secret:
    return await call_next(request)   # ← geen auth
```

**Impact:** Nieuwe deploy die per ongeluk `KNOWLEDGE_INGEST_SECRET` niet zet = service volledig open. Ook: test-config met lege var kan per ongeluk op prod belanden.

**Aanbeveling:** Change to fail-closed:
```python
if not settings.knowledge_ingest_secret:
    raise RuntimeError("KNOWLEDGE_INGEST_SECRET not configured")  # fail at startup
```

---

### F-004 — focus (research-api) audience-check is opt-in [MEDIUM]

**Locatie:** `klai-focus/research-api/app/core/auth.py:67-74`

**Bewijs:**
```python
if settings.zitadel_api_audience:
    decode_kwargs["audience"] = settings.zitadel_api_audience
else:
    logger.error("ZITADEL_API_AUDIENCE not set — JWT audience verification is DISABLED...")
    decode_kwargs["options"] = {"verify_aud": False}
```

**Impact:** Zelfde risico als F-002 als env-var niet gezet is. Wel een error-log, maar geen startup-fail.

**Aanbeveling:** `RESEARCH_API_ZITADEL_AUDIENCE` verplicht maken — fail at startup, niet enkel loggen.

---

### F-005 — research-api en scribe-api hebben geen auth middleware (alleen route-deps) [MEDIUM]

**Locatie:** `klai-focus/research-api/app/main.py:62`, `klai-scribe/scribe-api/app/main.py:36`

**Bewijs:** Beide services gebruiken `app.add_middleware(RequestContextMiddleware)` maar geen auth middleware. Auth is per-route via `Depends(get_current_user)`.

**Impact:** Een PR die een nieuwe route toevoegt zonder die Depends = publiek endpoint (services staan wel achter Caddy `/research/*` en `/scribe/*`). Easy to miss in review.

**Aanbeveling:** Voeg een auth middleware toe die alles behalve `/health` forceert (patroon van klai-connector). De `Depends(get_current_user)` kan blijven voor toegang tot de user-object; de middleware is de safety net.

---

### F-006 — Moneybird webhook token-check kan volledig worden overgeslagen [MEDIUM]

**Locatie:** `klai-portal/backend/app/api/webhooks.py:24-28`

**Bewijs:**
```python
if settings.moneybird_webhook_token:
    token = payload.get("webhook_token", "")
    if token != settings.moneybird_webhook_token:
        logger.warning("Moneybird webhook: invalid token")
        return Response(status_code=200)
```

**Issues:**
1. Als `MONEYBIRD_WEBHOOK_TOKEN` niet gezet is, is er **geen auth** — endpoint accepteert elke Moneybird-event-structuur
2. Zelfs bij invalid token: response 200 + warning-log zonder IP/UA → scanning-attempts niet detecteerbaar
3. Token-vergelijking is **niet constant-time** (`!=` vervanging met `hmac.compare_digest`)

**Impact:** Iemand kan billing_status flippen naar `active`/`cancelled`/`payment_failed` door payloads met valide `moneybird_contact_id` te sturen — per-org willekeurige billing-state.

**Aanbeveling:**
1. Fail hard als token niet gezet is (service-unavailable of startup-fail)
2. Return 401 bij invalid token (maar zie [Moneybird-docs](https://developer.moneybird.com/) voor retry-gedrag)
3. `hmac.compare_digest` voor token-vergelijking
4. Log bron-IP bij warnings

---

### F-007 — Internal endpoints vertrouwen query-param `org_id` [MEDIUM]

**Locatie:** `klai-portal/backend/app/api/internal.py`:
- `get_knowledge_feature(org_id: str)` (regel 307)
- `notify_page_saved(org_id: int)` (regel 408)
- `create_gap_event(payload.org_id)` (regel 597)
- `post_kb_feedback(body.librechat_tenant_id)` (regel 493)

**Bewijs:** Caller levert `org_id` mee in de request; service doet `set_tenant(db, org_id)` op basis daarvan, gate enkel via `_require_internal_token()` (single shared secret).

**Impact:** Als `INTERNAL_SECRET` lekt, kan caller willekeurige `org_id` meegeven en:
- kb-feature-status van elke user opvragen (+ pivot naar LibreChat MongoDB lookup)
- gap-events schrijven voor willekeurige org
- feedback-events aan willekeurige tenant koppelen

**Mitigerend:**
- Internal secret is lang en (hopelijk) SOPS-encrypted
- Docker-net only
- Fail-closed gedrag bij onbekende org_id (returns `enabled=False`)

**Aanbeveling:**
1. INTERNAL_SECRET rotation-schema documenteren en implementeren
2. Rate-limiting op internal endpoints (beperkt blast radius bij leak)
3. Audit-log van alle internal calls (`org_id`, timestamp, endpoint, caller-IP)

---

### F-008 — Widget JWT heeft geen revocation-mechanisme [MEDIUM]

**Locatie:** `klai-portal/backend/app/api/partner.py:452` → `generate_session_token` (widget_auth.py), `partner_dependencies.py:_auth_via_session_token`

**Bewijs:** JWT bevat `kb_ids: list[int]` als claim, signed met `WIDGET_JWT_SECRET`, TTL 1h. In `_auth_via_session_token`:
```python
kb_ids: list[int] = payload.get("kb_ids", [])
# ...
kb_access = {kb_id: "read" for kb_id in kb_ids}
```
De kb_ids uit de JWT worden **niet gevalideerd tegen de huidige DB-state** — JWT is source-of-truth voor 1h.

**Impact:** Als een widget in de DB wordt beperkt (KB toegang ingetrokken), blijven bestaande JWTs **tot 1u geldig** met de oude, ruimere scope.

**Aanbeveling:** Óf korter TTL (5-15min met refresh-endpoint), óf cross-check tegen `widget_kb_access` in `_auth_via_session_token`, óf JWT-JTI blacklist bij revoke-operatie.

---

### F-009 — klai-connector portal-bypass secret heeft geen rotatie [MEDIUM]

**Locatie:** `klai-connector/app/middleware/auth.py:75-78`

**Bewijs:**
```python
if self._portal_secret and token == self._portal_secret:
    request.state.from_portal = True
    request.state.org_id = None   # <-- NO TENANT CONTEXT
    return await call_next(request)
```

**Issues:**
1. Single long-lived secret = single point of compromise
2. `org_id = None` — downstream code moet dat handlen; als een handler toch `request.state.org_id` leest en het pushes naar een query, kan dat unexpected gedrag geven
3. Niet constant-time vergelijking (`token == self._portal_secret`)

**Aanbeveling:**
1. `hmac.compare_digest` voor token-compare
2. Secret-rotation schema
3. Audit wat `org_id=None` ingewikkeld maakt — liever `from_portal=True` + verplicht expliciete org_id in request body

---

### F-010 — Retrieval-API heeft geen rate limiting of request-size caps [LOW]

**Locatie:** `klai-retrieval-api/retrieval_api/models.py:8-17`

**Bewijs:** `RetrieveRequest` accepteert `top_k: int = 8`, `conversation_history: list[dict]`, `kb_slugs: list[str] | None`, `taxonomy_node_ids: list[int] | None` — **geen max-bounds**.

**Impact:** Caller kan grote queries doen (top_k=10000, enormous conversation_history). Beperkt mitigerend: alleen intern bereikbaar, maar een bug in caller = accidental DoS.

**Aanbeveling:** Pydantic `Field(..., le=50)` voor top_k, `max_length` voor lijsten, max body-size op Caddy-niveau.

---

### F-011 — connector TTL-cache evictie is suboptimaal [LOW]

**Locatie:** `klai-connector/app/middleware/auth.py:37-41`

**Bewijs:** LRU-achtige eviction via `next(iter(_token_cache))` — dit evict arbitrary entries (insertion-order), niet echt LRU. Geen per-worker isolation check.

**Impact:** Minor correctness issue, niet security-kritiek.

---

## Parking lot — nog te onderzoeken (Fase 3 vervolg)

- [ ] **DB-query inventaris (Fase 3.2)**: CodeIndex cypher query voor alle `session.execute(select(...))` en `session.get(...)` op tenant-scoped modellen. Flag die zonder `.org_id ==`.
- [ ] **`db.get(PortalConnector, id)` patroon** (internal.py:138, 205, 260) — `portal_connectors` is RLS-permissive dus deze `get()` trusts de id alleen. Verifiëren dat er geen raden-en-pokken mogelijk is.
- [ ] **Raw SQL via `text()` audit**: 7+ plekken in portal-api. Elke check: org_id in WHERE, geen SQL injection.
- [ ] **research-api notebook-tenant-check**: `str(nb.tenant_id) != user.tenant_id` — zijn beide kanten consistent `zitadel_org_id` (string)? Of UUID-string vs. numeric string? Type-mismatch zou silently "altijd 404" geven, of erger: altijd match.
- [ ] **Widget-origin check**: `origin_allowed()` robuust tegen subdomain-takeovers en origin-spoofing?
- [ ] **partner API rate limiting**: per-key, redis sliding window — correct implementatie gevalideerd?
- [ ] **scribe-api uploads path traversal**: `POST /v1/transcribe` met file + filename → pad-injectie?
- [ ] **Qdrant filter enforcement**: retrieval-api bouwt Qdrant filters op basis van `req.org_id` en `req.kb_slugs`. Als filter-bouw buggy is, cross-tenant chunks lekken.
- [ ] **Caddy exposure audit**: alleen core-01 server heeft de echte Caddyfile. Pull en review of retrieval-api of knowledge-ingest per ongeluk geëxposed zijn.

## Volgende stappen

1. **F-001, F-002, F-003 acteren** — alle HIGH-severity, allen kleine code-change (<100 LOC) per fix. Voorstel: 3 aparte SPECs of één SEC-001 parent.
2. **Caddy config pullen** en verifiëren wat echt publiek is.
3. **Fase 3.2 uitvoeren** — systematische DB-query inventaris per service, beste via `expert-security` subagent met deze findings als context.

## Referenties

- `.claude/rules/klai/projects/portal-security.md` — multi-tenant security patterns (reference implementation)
- `.claude/rules/klai/projects/portal-backend.md` — RLS + SQLAlchemy gotchas
- `.claude/rules/klai/projects/knowledge.md` — portal→ingest auth header pitfalls
- SPEC-KB-019/020 — historische IDOR + key-mismatch issues
- SPEC-WIDGET-002 — widget JWT flow
- SPEC-API-001 — partner API

---

# Appendix — Resumable Context

Alles hieronder is voor hervatten van deze audit in een latere sessie (potentieel met leeg context-window). Bedoeld als "start hier" voor future-me of een subagent.

## A.1 Resume-checklist — doe dit eerst na `/clear`

1. **Lees dit document** (`.moai/audit/04-tenant-isolation.md`) volledig.
2. **Lees het plan** (`.moai/audit/00-plan.md`) voor Fase-context.
3. **CodeIndex freshness**: run `codeindex status`; als behind, `codeindex update`.
4. **Recall top-3 findings** in memory:
   ```
   recall({query: "retrieval-api auth", type: "bug"})
   recall({query: "tenant isolation", type: "bug"})
   ```
5. **Verifieer findings zijn nog valide** — run de checks in A.3. Als een HIGH-finding al gefixt is, markeer hier met `[RESOLVED 2026-XX-XX commit-sha]`.
6. **Lees laatste commits** op de affected files:
   ```bash
   git log --oneline -20 -- klai-retrieval-api/retrieval_api/main.py klai-scribe/scribe-api/app/core/auth.py klai-knowledge-ingest/knowledge_ingest/middleware/auth.py
   ```

## A.2 Call-chain per HIGH-finding

### F-001 — wie roept retrieval-api aan?

```
Callers van retrieval-api:8040:
├── klai-portal/backend/app/services/partner_chat.py:84 retrieve_context()
│     └── gebruikt auth.org_id uit PartnerAuthContext (verified)
├── klai-focus/research-api → retrieval_client.py retrieve_broad()/retrieve_narrow()
│     └── gebruikt user.tenant_id uit get_current_user() (verified JWT)
├── LiteLLM knowledge hook (buiten repo)
│     └── gebruikt Zitadel org-claim uit LiteLLM team key metadata
└── knowledge-ingest (indirect via portal) — nvt, ingest call niet retrieval
```

**Verificatie-commando (CodeIndex):**
```cypher
MATCH (n)-[:CodeRelation {type: 'CALLS'}]->(m:Function)
WHERE m.name IN ['retrieve_context', 'retrieve_narrow', 'retrieve_broad']
RETURN n.name, n.filePath
```

### F-002 / F-004 — JWT audience verification

Affected tokens: Zitadel access tokens uit **elke app** op de Zitadel tenant. Check welke apps bestaan:
```bash
# via Zitadel admin UI of zitadel-update-tenant.py script
ls klai-infra/scripts/zitadel-*.py
```

### F-003 — wie zet `KNOWLEDGE_INGEST_SECRET`?

```bash
# Controleer huidige prod-waarde (zonder te lekken):
grep -l "KNOWLEDGE_INGEST_SECRET" klai-infra/core-01/*.sops klai-portal/backend/.env.example
# Controleer dat waarde genest niet leeg is:
docker exec knowledge-ingest printenv KNOWLEDGE_INGEST_SECRET | wc -c  # >0
```

## A.3 Verificatie-commands per finding

**F-001 (retrieval-api no auth)** — herhaal de scan:
```bash
# Should be zero auth imports in main.py:
grep -n "middleware\|Depends.*auth\|HTTPBearer\|get_current_user" klai-retrieval-api/retrieval_api/main.py
# Should be zero Depends(get_current_user) in API files:
grep -rn "Depends\|get_current_user" klai-retrieval-api/retrieval_api/api/
```
Nog steeds waar als output leeg is.

**F-002 (scribe audience)** — herhaal:
```bash
grep -n "verify_aud" klai-scribe/scribe-api/app/core/auth.py
# Finding still valid if: "verify_aud": False  present
```

**F-003 (knowledge-ingest fail-open)** — herhaal:
```bash
grep -B1 -A3 "if not settings.knowledge_ingest_secret" klai-knowledge-ingest/knowledge_ingest/middleware/auth.py
# Finding still valid if: return await call_next(request)  in the if-branch
```

**F-005 (scribe/focus no auth middleware)** — herhaal:
```bash
grep -n "add_middleware" klai-focus/research-api/app/main.py klai-scribe/scribe-api/app/main.py
# Finding still valid if: only RequestContextMiddleware and CORSMiddleware present (no AuthMiddleware)
```

**F-006 (moneybird webhook)** — herhaal:
```bash
grep -B1 -A5 "moneybird_webhook_token" klai-portal/backend/app/api/webhooks.py
# Finding still valid if: the 'if settings.moneybird_webhook_token' branch uses != (not hmac.compare_digest)
```

**F-008 (widget JWT revocation)** — herhaal:
```bash
grep -A15 "_auth_via_session_token" klai-portal/backend/app/api/partner_dependencies.py | grep -E "kb_ids|DB|query"
# Finding still valid if: kb_ids komt uit payload zonder DB-lookup tegen current widget_kb_access
```

## A.4 Fase 3.2 — concrete start (DB-query inventaris)

**Doel:** Voor elke `select(<TenantModel>).where(...)` in de portal-backend + downstream services, verifieer dat `org_id` in de WHERE-clause zit of dat RLS het afdekt.

### A.4.1 CodeIndex cypher — queries zonder expliciete org_id filter

```cypher
-- Vind alle functies die op tenant-modellen queryen zonder 'org_id' in de buurt
MATCH (f:Function)-[:CodeRelation {type: 'CALLS'}]->(m:Function)
WHERE m.name IN ['execute', 'scalar_one_or_none', 'scalars']
  AND f.filePath CONTAINS 'klai-portal/backend/app'
  AND NOT f.description CONTAINS 'org_id'
RETURN f.name, f.filePath
ORDER BY f.filePath
```

```cypher
-- Vind db.get(Model, id) calls — bypassen WHERE-clause, vertrouwen op RLS
MATCH (f:Function)-[r:CodeRelation {type: 'CALLS'}]->(m:Method)
WHERE m.name = 'get'
  AND f.filePath CONTAINS 'klai-portal'
RETURN f.name, f.filePath, r.reason
```

### A.4.2 Grep-based fallback

```bash
# Alle select()-calls op tenant-modellen:
grep -rn "select(Portal\|select(Vexa\|select(Widget" klai-portal/backend/app/api klai-portal/backend/app/services | grep -v ".venv"

# Cross-check: zonder org_id in nabije context
grep -B1 -A5 "select(Portal" klai-portal/backend/app/api/*.py | grep -v "org_id" | grep "select(Portal"
```

### A.4.3 Tenant-scoped modellen (lijst om te checken)

Uit `klai-portal/backend/app/models/`:
- `portal.PortalOrg`, `portal.PortalUser`
- `groups.PortalGroup`, `groups.PortalGroupMembership`
- `knowledge_bases.PortalKnowledgeBase`, `PortalUserKBAccess`, `PortalGroupKBAccess`, `PortalKbTombstone`
- `meetings.VexaMeeting`
- `connectors.PortalConnector`
- `products.*` (system groups, user_products, group_products)
- `widgets.Widget`, `WidgetKbAccess`
- `partner_api_keys.PartnerAPIKey`, `PartnerApiKeyKbAccess`
- `retrieval_gaps.PortalRetrievalGap`
- `taxonomy.*`
- `feedback_events.*`
- `audit.*`

### A.4.4 Downstream services te scannen

- `klai-focus/research-api/app/models/*.py` — notebooks, sources, chat_messages
- `klai-scribe/scribe-api/app/models/*.py` — transcriptions
- `klai-knowledge-ingest/knowledge_ingest/pg_store.py` — ingest queries
- `klai-connector/app/models/*.py` — connector, sync_run

## A.5 Open items die nog onderzocht moeten worden

- [ ] **Partner-chat retrieval flow** — `klai-portal/backend/app/services/partner_chat.py:84 retrieve_context()`. Volledig lezen om te bevestigen dat `auth.org_id` altijd uit de partner-key komt, nooit uit request body.
- [ ] **research-api `str(nb.tenant_id) != user.tenant_id`** — lees `klai-focus/research-api/app/models/notebook.py` om type van `tenant_id` column te verifiëren. Als UUID vs. string → always-404 of always-pass bug.
- [ ] **Widget `origin_allowed()`** — lees `klai-portal/backend/app/services/widget_auth.py`. Check: wildcard-matching? subdomain-suffix check robust? null-origin accepted?
- [ ] **`db.get(PortalConnector, id)` in internal.py:138, 205, 260** — connector-id is UUID, maar als iemand INTERNAL_SECRET heeft, kan die UUIDs enumeren. Enumeratie-resistentie: UUIDv4 entropy + rate limit.
- [ ] **Caddy config** — SSH naar core-01, `cat /etc/caddy/Caddyfile` of waar 't staat. Bevestig dat retrieval-api/knowledge-ingest/connector niet per-ongeluk via een wildcard-route public zijn.
- [ ] **Raw `text()` SQL audit** — grep `text("INSERT\|text("UPDATE\|text("DELETE` in portal-backend. Elk resultaat: org_id in WHERE? Parameters in dict (geen string-interp)?
- [ ] **Qdrant filter enforcement** — `klai-retrieval-api/retrieval_api/services/search.py hybrid_search()` — hoe wordt `org_id` doorgegeven als Qdrant filter? Is het een `must` filter of `should`?
- [ ] **Scribe file-upload path traversal** — `klai-scribe/scribe-api/app/api/transcribe.py POST /v1/transcribe` verwerkt `UploadFile`. Bevestig dat filenames gesaniseerd worden (geen `../`).
- [ ] **LiteLLM knowledge hook** — hoe komt org_id daar vandaan? Team-key metadata — is die onveranderlijk door de klant?

## A.6 Affected files — quick reference

| Finding | File(s) | Regels |
|---|---|---|
| F-001 | `klai-retrieval-api/retrieval_api/main.py` | 58-61 |
| F-001 | `klai-retrieval-api/retrieval_api/api/retrieve.py` | 55-56 |
| F-001 | `klai-retrieval-api/retrieval_api/api/chat.py` | 22-23 |
| F-002 | `klai-scribe/scribe-api/app/core/auth.py` | 64-70 |
| F-003 | `klai-knowledge-ingest/knowledge_ingest/middleware/auth.py` | 17-21 |
| F-004 | `klai-focus/research-api/app/core/auth.py` | 67-74 |
| F-005 | `klai-focus/research-api/app/main.py` | 45-62 |
| F-005 | `klai-scribe/scribe-api/app/main.py` | 19-36 |
| F-006 | `klai-portal/backend/app/api/webhooks.py` | 22-28 |
| F-007 | `klai-portal/backend/app/api/internal.py` | 48-55, 307, 408, 597 |
| F-008 | `klai-portal/backend/app/api/partner_dependencies.py` | 74-141 |
| F-008 | `klai-portal/backend/app/services/widget_auth.py` | (lezen — niet gezien) |
| F-009 | `klai-connector/app/middleware/auth.py` | 73-78 |
| F-010 | `klai-retrieval-api/retrieval_api/models.py` | 8-17 |
| F-011 | `klai-connector/app/middleware/auth.py` | 37-41 |

## A.7 Suggested next-session prompt

Als je deze audit later voortzet, gebruik iets als:

> "Ik wil Fase 3 van de security audit afronden. Lees eerst `.moai/audit/04-tenant-isolation.md` en `.moai/audit/00-plan.md`. Start daarna met A.4 (Fase 3.2 DB-query inventaris) via CodeIndex cypher. Focus op tenant-scoped modellen uit A.4.3. Schrijf resultaten in een nieuw findings-bestand `.moai/audit/04-tenant-isolation-phase-3-2.md` en voeg nieuwe findings toe aan de bestaande F-lijst."

Of korter, als je direct fixes wilt:

> "Lees `.moai/audit/04-tenant-isolation.md`. Maak SPECs voor F-001, F-002, F-003 via /moai plan. Gebruik `expert-security` subagent voor review van elke SPEC."

## A.8 Changelog

| Datum | Wijziging |
|---|---|
| 2026-04-19 | Initiële audit — F-001 t/m F-011 gedocumenteerd. Top-3 findings in CodeIndex memory. |
| | (volgende update) |
