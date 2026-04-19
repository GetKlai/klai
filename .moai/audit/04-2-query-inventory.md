# Fase 3.2 + 3.3 — Query Inventaris & Org_ID Coverage

**Datum:** 2026-04-19
**Scope:** klai-portal (100+ queries), klai-retrieval-api (Qdrant filters), klai-knowledge-ingest (routes)
**Vervolg op:** `.moai/audit/04-tenant-isolation.md`

## TL;DR

**Portal-api: solide.** De meerderheid van de ~100 tenant-scoped queries volgt één van de safe patterns:
- Expliciete `.where(Model.org_id == org_id)` (pattern A — grootste groep)
- Via `_get_X_or_404(id, org_id, db)` helper (pattern B)
- Via `_get_caller_org` → `set_tenant()` + RLS-strict tabel (pattern C — defense in depth)

**Retrieval-api: zwak.** F-001 was al HIGH; query-analyse voegt F-014 toe: binnen-tenant cross-user leak via body `user_id`. Qdrant-filter enforce'd `org_id` (F-013 positief), maar geen filter op `scope`/`user_id` authenticiteit.

**Knowledge-ingest: dubbel fail-open.** F-003 (middleware) + F-012 (route-level helper) — beide skip auth als env var leeg is.

## Nieuwe findings (aanvulling op `.moai/audit/04-tenant-isolation.md`)

### F-012 — knowledge-ingest heeft dubbele fail-open auth [HIGH]

**Locatie:**
1. `klai-knowledge-ingest/knowledge_ingest/middleware/auth.py:19-21` (al gelogd als F-003)
2. `klai-knowledge-ingest/knowledge_ingest/routes/ingest.py:54-60`

**Bewijs (nieuwe layer in ingest.py):**
```python
def _verify_internal_secret(request: Request) -> None:
    """Verify X-Internal-Secret header for service-to-service calls."""
    if not settings.knowledge_ingest_secret:
        return   # ← FAIL OPEN — zelfde bug als middleware
    secret = request.headers.get("x-internal-secret", "")
    if not hmac.compare_digest(secret, settings.knowledge_ingest_secret):
        raise HTTPException(status_code=401, detail="Unauthorized")
```

**Impact:** Twee onafhankelijke laag-auth, beide fail-open. Middleware en route-helper horen elkaars failures op te vangen — hier doen ze het dezelfde verkeerde ding.

**Positief:** De route-helper gebruikt wel `hmac.compare_digest` (constant-time).

**Aanbeveling:** Beide locaties fail-closed maken. Start-time config-check in `config.py`:
```python
class Settings(BaseSettings):
    knowledge_ingest_secret: str  # No default — required
    
    @model_validator(mode="after")
    def _validate(self):
        if not self.knowledge_ingest_secret:
            raise ValueError("KNOWLEDGE_INGEST_SECRET must be set")
```

---

### F-013 — retrieval-api Qdrant filter bevat wel org_id [POSITIVE / DEFENSIVE]

**Locatie:** `klai-retrieval-api/retrieval_api/services/search.py:74-75`, `:122`

**Bewijs:**
```python
# _scope_filter (klai_knowledge collection):
conditions: list[FieldCondition | Filter] = [
    FieldCondition(key="org_id", match=MatchValue(value=request.org_id)),
]

# _search_notebook (klai_focus collection):
must_conditions: list[FieldCondition | Filter] = [
    FieldCondition(key="tenant_id", match=MatchValue(value=request.org_id)),
]
```

**Waarom positief:** Zelfs als F-001 geëxploiteerd wordt (geen auth op retrieval-api), kan de caller alleen chunks lezen **voor de org_id die ze in de body zetten**. Cross-tenant is niet een free-for-all — je moet de target org_id kennen.

**Waarom niet volledig sluitend:**
- Zitadel org_ids zijn potentieel enumereerbaar (numerieke sequentie?)
- Org_ids worden zichtbaar in logs, URLs (`*.getklai.com/api/...` resolveert via Caddy wildcard)
- Een bekende bedrijfsnaam → makkelijk de Zitadel org_id resolveren
- Geen rate limit (F-010) = enumeratie is goedkoop

**Downgrading F-001:** Op basis van F-013 is F-001's impact meer **"known-org access"** dan **"any-org access"**. Blijft HIGH wegens gebrek aan defense in depth; escaleert tot CRITICAL als org_id predictable blijkt (checken — zie parking lot).

---

### F-014 — retrieval-api trusts body `user_id` → binnen-tenant cross-user leak [HIGH]

**Locatie:** `klai-retrieval-api/retrieval_api/services/search.py:78-81`, `_scope_filter` voor scope="personal"

**Bewijs:**
```python
if request.scope == "personal":
    if request.user_id:
        conditions.append(
            FieldCondition(key="user_id", match=MatchValue(value=request.user_id))
        )
```

`request.user_id` komt direct uit `RetrieveRequest` body (`models.py:12`). Geen verificatie dat de caller deze user_id mag opvragen.

**Attack scenario:**
1. Attacker heeft legitieme toegang tot tenant X als user A (normale klant-flow)
2. Via F-001: attacker kan retrieval-api direct aanroepen
3. Attacker zet in body: `org_id=X`, `scope=personal`, `user_id=<target_user_B>`
4. Qdrant returnt user B's persoonlijke chunks
5. **Resultaat:** binnen-tenant cross-user data leak

**Impact:**
- Als alleen portal-api met retrieval-api praat (netwerk-isolatie houdt), geen directe exploit. Maar: **portal-api `partner_chat.py` en LiteLLM-hook geven `user_id` door uit user-controlled bronnen**.
- Combined met F-001: **full privileged-to-any-user escalation binnen tenant**

**Aanbeveling:**
1. Auth middleware op retrieval-api (F-001 fix)
2. Cross-check: in middleware uit token `sub` extracten; verwerp request als `body.user_id != token.sub` tenzij caller role=admin
3. Qdrant filter voor scope=personal/both **mag alleen** de geverifieerde user_id gebruiken

---

### F-015 — background tasks draaien zonder `set_tenant()` [MEDIUM]

**Locaties:**
- `klai-portal/backend/app/services/bot_poller.py:112, 117` — VexaMeeting polling zonder tenant context
- `klai-portal/backend/app/services/invite_scheduler.py:64, 97, 121` — iCal dedupe across orgs
- `klai-portal/backend/app/services/connector_credentials.py:165` — alle orgs met DEK

**Waarom intentional:** Deze tasks hebben **cross-org access nodig** voor systeem-functies (alle meetings pollen, alle iCal UIDs deduppen, alle DEK's roteren). `AsyncSessionLocal()` zonder `set_tenant()` bypass't RLS.

**Waarom risicovol:** Als `portal_api` DB user toch RLS-bypass heeft (bijv. `BYPASSRLS` attribuut), dan doen deze queries precies wat ze moeten doen. Als NIET: queries returneren leeg (silent failure).

**Controle nodig:** Wat is de PG role config voor `portal_api`? Heeft het `BYPASSRLS`?

**Aanbeveling:**
1. Documenteer expliciet welke tasks cross-org mogen zijn (`# @MX:NOTE: cross-org system task — intentional RLS bypass`)
2. PG role audit: `\du portal_api` — confirm of `bypassrls` gezet is
3. Als `portal_api` `bypassrls` heeft: **all RLS is cosmetic for this role** — dat is een apart ernstig punt.

---

### F-016 — raw `text()` INSERT op RLS-split tabellen is correct [POSITIVE]

**Locaties:**
- `klai-portal/backend/app/api/internal.py:537` (portal_feedback_events INSERT)
- `klai-portal/backend/app/api/partner.py:279` (portal_feedback_events INSERT)
- `klai-portal/backend/app/api/partner_dependencies.py:63-68` (partner_api_keys UPDATE)

**Bewijs (internal.py:537):**
```python
await db.execute(
    text("""
        INSERT INTO portal_feedback_events
        (org_id, conversation_id, message_id, rating, tag, feedback_text,
         chunk_ids, correlated, model_alias, occurred_at)
        VALUES (:org_id, :conversation_id, :message_id, :rating, :tag,
                :feedback_text, :chunk_ids, :correlated, :model_alias, NOW())
    """),
    { "org_id": org.id, ... },
)
```

**Waarom positief:**
1. Parametrized (geen SQL-injection)
2. Gebruikt `org.id` die al verified is via tenant-lookup
3. Raw SQL bewust gekozen wegens ORM `RETURNING` issue met RLS split-policies (zie `portal-backend.md`)

**Aanbeveling:** Geen fix nodig. Wel: mogelijk hulpfunctie toevoegen om deze INSERT te centraliseren zodat nieuwe feedback-events niet opnieuw raw SQL copy-pasten en mogelijk de org_id skippen.

---

## Query-categorisatie

Alle ~100 tenant-scoped queries in portal-backend vallen in één van deze patterns:

| Pattern | Omschrijving | # queries ongeveer | Voorbeeld |
|---|---|---|---|
| **A** | Expliciete `.where(*.org_id == org_id)` | ~65% | `select(Widget).where(Widget.org_id == org.id)` |
| **B** | Via `_get_X_or_404(id, org_id, db)` helper | ~15% | `kb = await _get_kb_for_org(slug, org.id, db)` |
| **C** | Geen org_id in WHERE, maar RLS-strict tabel beschermt | ~10% | `select(PortalGroupKBAccess).where(kb_id == X)` op RLS-strict tabel |
| **D** | Post-validatie: query op reeds-gevalideerde IDs | ~5% | `admin_widgets.py:197` — na `_validate_kb_ids()` |
| **E** | Cross-org by design (internal endpoints / background tasks) | ~5% | `internal.py`, `bot_poller.py`, `connector_credentials.py:165` |

Geen Pattern F gevonden (query zonder org_id EN zonder RLS-bescherming EN zonder pre-validatie). Dat is goed nieuws.

## Verdachte queries die ik heb gespot-checkt

| File:line | Query | Verdict |
|---|---|---|
| `admin_widgets.py:197` | `select(PortalKnowledgeBase).where(id.in_(body.kb_ids))` | Pattern D — safe via line 162 `_validate_kb_ids(body.kb_ids, org.id, db)` |
| `app_gaps.py:186` | `select(PortalTaxonomyNode).where(id.in_(node_ids))` | Pattern C — `portal_taxonomy_nodes` is RLS-strict; safe mits `set_tenant` door `_get_caller_org` |
| `auth.py:857` | `select(PortalOrgAllowedDomain).where(domain == email_domain)` | Pattern E by design — signup-flow zoekt welke org bij domein hoort |
| `auth_select.py:64` | `select(PortalOrg).where(id.in_(org_ids))` | Pattern E — `org_ids` uit verified Zitadel session; safe |
| `auth_select.py:105` | `select(PortalOrg).where(id == body.org_id)` | Pattern A-ish — `body.org_id` wordt daarna gecheckt tegen session's `org_ids` |
| `bot_poller.py:112, 117, 118` | `select(VexaMeeting).where(status == X)` | Pattern E — background maintenance; cross-org by design |
| `connector_credentials.py:165` | `select(PortalOrg).where(connector_dek_enc.isnot(None))` | Pattern E — system-level DEK admin |
| `internal.py:138, 205, 260` | `db.get(PortalConnector, connector_id)` | Pattern E — internal endpoint resolves connector → set_tenant; safe mits INTERNAL_SECRET houdt |
| `internal.py:216` | `select(PortalOrg).where(id == connector.org_id)` | Pattern B — org_id komt uit al-geresolveerde connector |
| `invite_scheduler.py:64, 97, 121` | `select(VexaMeeting).where(ical_uid == uid)` | Pattern E — cross-org iCal dedupe |

**Geen Pattern D-queries zonder valide voorafgaande validatie gevonden.**

## Downstream services — query patterns

### klai-retrieval-api (Qdrant, niet SQL)

- **Elke Qdrant query krijgt `org_id` of `tenant_id` filter** (F-013 positief)
- **`user_id` filter komt uit request body zonder verificatie** (F-014 negatief)
- `kb_slugs` filter komt uit body zonder verificatie — binnen ingevulde org mogelijk cross-KB als slug-format voorspelbaar (`personal-<user_id>`, `org`, `group:<id>`)

### klai-knowledge-ingest

- Alle routes hebben `_verify_internal_secret(request)` call — **maar fail-open bug** (F-003 + F-012)
- Queries zelf gebruiken `org_id` parameter uit de request body na verified auth

### klai-focus (research-api)

- `get_current_user()` resolved `tenant_id = str(zitadel_org_id)` uit JWT (lookup `portal_users`)
- Chat endpoint check `str(nb.tenant_id) != user.tenant_id` (regel 56) — **parking lot item: verify type-match**
- Queries op `Notebook` via `select(Notebook).where(Notebook.id == nb_id)` zonder org filter — relies on RLS of on `_get_notebook_or_404` helper — nog niet gevolledigd

### klai-scribe

- `get_current_user_id()` returnt alleen `sub` — **geen tenant-binding** in auth-helper
- Transcription queries moeten dan `owner_user_id == user_id` gebruiken om scoping af te dwingen
- **Te verifiëren**: elke query op `Transcription` filtert op `owner_user_id`

## Open items (escalated to parking lot in `04-tenant-isolation.md` A.5)

- [ ] **PG role `bypassrls` check** — hoogste prioriteit; bepaalt of RLS echt layered defense is (F-015 context)
- [ ] **Zitadel org_id entropy** — numeriek sequentieel of UUID-achtig? (F-013 context)
- [ ] **klai-scribe query scoping** — alle transcription-queries filteren op `owner_user_id`? (geen route gelezen)
- [ ] **klai-focus `_get_notebook_or_404`** — tenant filter aanwezig? (impliceert F-005 route-dep reliance)
- [ ] **retrieval-api kb_slugs slug-format voorspelling** — kan attacker binnen eigen org een andermans `personal-<user_id>` kb_slug raden?
- [ ] **connector.py:345, 403, 440, 491** — allen gebruiken `_get_kb_with_owner_check` pre-validation. Correctheid van dat helper nog niet gelezen.
- [ ] **`portal_users` permissive RLS** — bewust? Waarom niet strict? Implicaties voor internal.py queries die users cross-org ophalen?

## Update F-lijst

Samenvatting van alle findings na Fase 3 (3.1 t/m 3.3):

| ID | Severity | Service | Kern |
|---|---|---|---|
| F-001 | HIGH | retrieval-api | Geen auth; downgraded door F-013 maar blijft HIGH |
| F-002 | HIGH | scribe-api | JWT `verify_aud: False` hard-coded |
| F-003 | HIGH | knowledge-ingest | Middleware fail-open |
| **F-012** | **HIGH** | **knowledge-ingest** | **Route-level auth OOK fail-open (ingest.py)** |
| **F-014** | **HIGH** | **retrieval-api** | **Body `user_id` vertrouwd → cross-user leak binnen tenant** |
| F-004 | MEDIUM | research-api | Audience-check opt-in |
| F-005 | MEDIUM | focus + scribe | Geen auth middleware |
| F-006 | MEDIUM | portal webhooks | Moneybird token-check skippable |
| F-007 | MEDIUM | portal internal | Query-param `org_id` vertrouwd op basis van single INTERNAL_SECRET |
| F-008 | MEDIUM | partner widget JWT | Geen revocation |
| F-009 | MEDIUM | klai-connector | Long-lived portal bypass-secret |
| **F-015** | **MEDIUM** | **portal background tasks** | **Draaien zonder `set_tenant` — RLS-dependent** |
| F-010 | LOW | retrieval-api | Geen rate-limit / size caps |
| F-011 | LOW | klai-connector | Token-cache eviction suboptimal |
| **F-013** | **POSITIVE** | **retrieval-api** | **Qdrant filter enforce'd `org_id`** |
| **F-016** | **POSITIVE** | **portal raw SQL** | **`text()` INSERT op RLS-split tabellen correct** |

**HIGH count: 3 → 5** (F-012, F-014 new)

## CodeIndex memory updates

Top-2 nieuwe findings opgeslagen:
- F-012: knowledge-ingest route-level fail-open
- F-014: retrieval-api user_id trust (cross-user leak)

## Volgende stappen

1. **PG-role check** — de allerhoogste prio: log `\du portal_api` en zoek `bypassrls`. Zonder deze context is F-015 niet scherp en F-013 mogelijk waardeloos.
2. **Zitadel org_id format** — bepaalt echte blast radius van F-001 / F-013 / F-014.
3. **Fix-proposal SPECs** voor de 5 HIGH-findings, gegroepeerd:
   - SEC-001: Retrieval-API hardening (F-001, F-010, F-014)
   - SEC-002: Knowledge-ingest fail-closed auth (F-003, F-012)
   - SEC-003: JWT audience mandatory (F-002, F-004)
   - SEC-004: Auth middleware defense-in-depth (F-005, F-006, F-009)

## Changelog

| Datum | Wijziging |
|---|---|
| 2026-04-19 | Fase 3.2 + 3.3 — Query inventaris + org_id coverage. F-012, F-013, F-014, F-015, F-016 toegevoegd. |
