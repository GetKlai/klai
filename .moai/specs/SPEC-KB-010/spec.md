# SPEC-KB-010: LiteLLM Knowledge Hook — always-on KB-integratie

> Status: DONE (2026-03-26)
> Author: Mark Vletter (design) + Claude (SPEC)
> Builds on: SPEC-KB-008 (retrieval-api), SPEC-KB-009 (docs-sync)
> Architecture reference: `docs/architecture/klai-knowledge-architecture.md`
> Research basis: `docs/architecture/knowledge-system-fundamentals.md` §Bevinding 15
> Created: 2026-03-26
> Updated: 2026-03-26 — prerequisite SPEC-KB-010-pre vervallen; lazy MongoDB-mapping ipv aparte sync

---

## Wat bestaat er vandaag

### klai_knowledge.py — werkende hook, verouderde aanroep

`deploy/litellm/klai_knowledge.py` is een `async_pre_call_hook` die al live is en geconfigureerd in `config.yaml`:

```yaml
litellm_settings:
  callbacks:
    - klai_knowledge.klai_knowledge_hook
```

De hook onderschept elke chat-completion request, pakt de laatste user-message, haalt `org_id` op uit de LiteLLM team-key metadata, roept retrieval aan, en injecteert relevante chunks als system-message prefix.

**Wat er mis is:**

| Probleem | Impact |
|---|---|
| Roept `knowledge-ingest:8000/knowledge/v1/retrieve` aan | Deprecated endpoint: geen reranking, geen pre-retrieval gate, geen coreference-resolutie |
| Alleen `kb_slugs: ["org"]` | Persoonlijke kennisbank volledig buiten beeld |
| `RETRIEVE_MIN_SCORE` als kwaliteitsfilter | Wordt vervangen door de retrieval-gate in KB-008 |
| Geen `conversation_history` | Multi-turn queries ("wat zei hij daarin over het budget?") werken niet |
| Geen user-level autorisatie | Elke gebruiker van een tenant krijgt altijd KB-injectie, ongeacht rechten |
| `async_post_call_success_hook` leeg | Geen logging van KB-gebruik |

### retrieval-api — draait, niet aangesloten

KB-008 leverde `retrieval-api` op poort 8040. De hook roept dit endpoint nog niet aan.

### Permissiemodel — volledig uitgebouwd

Het portal-backend heeft een compleet rechtenmodel:

- `portal_user_products` — product-entitlements per user (`product="knowledge"`)
- `portal_group_products` — product-entitlements via groepslidmaatschap
- `get_accessible_kb_slugs(user_id, db)` — retourneert alle KB-slugs die een user mag doorzoeken (direct + via groepen)
- Org-admins bypassen product-checks automatisch

Groepen bepalen welke producten users hebben (zie portal Groups-scherm: "Chat+Focus", "+Scribe", "+Knowledge+Docs"). Knowledge-toegang is per user/group, niet per tenant.

De hook gebruikt dit model momenteel niet.

### ID-mapping — ontbreekt, wordt lazy opgelost

De hook krijgt `data["user"]` = LibreChat MongoDB ObjectId.
`portal_user_products` gebruikt `zitadel_user_id` (de OIDC sub claim).

**Oplossing:** lazy mapping via MongoDB. Het portal-backend voert éénmalig een MongoDB-lookup uit bij de eerste aanroep van een onbekende `librechat_user_id`. Het resultaat wordt gecached in `portal_users.librechat_user_id` zodat alle volgende aanroepen puur PostgreSQL zijn.

**Waarom niet patchen of een aparte sync-flow:** LibreChat stuurt MongoDB ObjectId als `user` field (hardcoded). Een patch op LibreChat-code vereist onderhoud bij elke container-update. Een aparte sync-flow vereist een LibreChat plugin of webhook waarvoor geen ingebouwde trigger bestaat. De lazy MongoDB-lookup vereist geen LibreChat-wijzigingen en werkt automatisch voor alle tenants.

**Netwerkarchitectuur:** `portal-api` staat momenteel alleen op `klai-net`. MongoDB staat op `net-mongodb`. Voor de lazy lookup wordt `portal-api` ook aan `net-mongodb` toegevoegd. `PortalOrg.librechat_container` levert de database-naam per tenant.

---

## Wat deze SPEC bouwt

Vijf wijzigingen verdeeld over portal-backend, LiteLLM hook en docker-compose:

1. **Lazy ID-mapping** — `librechat_user_id` kolom in `portal_users` + Alembic migratie
2. **Intern autorisatie-endpoint** — portal-backend `GET /internal/v1/users/{librechat_user_id}/feature/knowledge?org_id=xxx` met MongoDB-fallback voor onbekende IDs
3. **Retrieval-api aansluiting** — van deprecated `knowledge-ingest` naar `retrieval-api:8040/retrieve`
4. **User-level autorisatie in de hook** — strikt gehandhaafd (fail-closed)
5. **Personal scope, conversation history, provenance en logging**

Na deze SPEC:
- Gebruikers zonder `knowledge`-entitlement krijgen nooit KB-injectie
- Gebruikers mét entitlement krijgen chunks uit persoonlijke én org-kennisbank
- Multi-turn queries werken via `conversation_history`
- KB-gebruik is traceerbaar per user en org
- Eerste aanroep per user: MongoDB-lookup (éénmalig). Alle volgende: PostgreSQL.

---

## Architectuurkeuze: altijd-aan via LiteLLM hook

De hook-architectuur is al live en correct. Ter documentatie van de bewuste keuze:

**Niet: MCP search-tool** — een expliciete tool vereist dat het LLM zelf besluit wanneer KB-context relevant is. Verkeerde inschatting = gebruiker mist context zonder het te weten.

**Wel: LiteLLM pre-call hook** — elke relevante chat-beurt wordt automatisch verrijkt. De gebruiker ervaart een assistent die het gewoon weet. De retrieval-gate (KB-008) filtert queries die geen KB-context nodig hebben.

---

## Autorisatieflow

```
[LibreChat] → POST /v1/chat/completions
                  user: "<librechat_user_id>"       ← LibreChat MongoDB ObjectId
                  Authorization: Bearer <team_key>  ← per-tenant LiteLLM key
                       │
              ┌────────▼──────────────────┐
              │   LiteLLM proxy           │
              │   async_pre_call_hook     │
              │                           │
              │ 1. org_id uit             │
              │    team-key metadata      │
              │                           │
              │ 2. user_id =              │
              │    data["user"]           │
              │    (LibreChat MongoDB ID) │
              │                           │
              │ 3. Heeft user knowledge?  │
              │    GET portal-backend     │──── Nee → data ongewijzigd terug
              │    /internal/v1/users/    │         (geen KB-injectie)
              │    {user_id}/feature/     │
              │    knowledge?org_id=xxx   │──── Fout/down → data ongewijzigd
              │                           │    (fail-closed, log WARNING)
              │ 4. retrieval-api          │
              │    /retrieve              │──── Gate activeert → geen chunks
              │                           │
              │ 5. Inject chunks          │
              │    in system message      │
              └───────────────────────────┘
```

**Portal-backend intern endpoint — lazy mapping:**

```
GET /internal/v1/users/{librechat_user_id}/feature/knowledge?org_id=xxx

→ Stap 1: portal_users WHERE librechat_user_id = ? → gevonden
          → get_effective_products(zitadel_user_id) → return enabled

→ Stap 2 (alleen bij miss): portal_orgs WHERE org_id = ? → librechat_container naam
          → MongoDB: db[container_naam].users.find_one({_id: ObjectId(librechat_user_id)})
          → openid_id = zitadel_user_id
          → portal_users WHERE zitadel_user_id = ? → sla librechat_user_id op
          → get_effective_products(zitadel_user_id) → return enabled

→ Stap 3 (bij MongoDB miss of fout): return {"enabled": false}  ← fail-closed
```

**Fail-closed:** als het portal-autorisatie-endpoint niet bereikbaar is, wordt KB-injectie overgeslagen. Een gebruiker zonder aantoonbare rechten krijgt nooit KB-context.

**Caching:** de autorisatiecheck wordt gecached in LiteLLM's `DualCache` met TTL 300s. Reden: de hook draait op elke LLM-aanroep; zonder cache is dat een portal API-call per chat-beurt. Rechten veranderen zelden mid-sessie; 5 minuten vertraging bij intrekking is acceptabel.

**Scope:** gebruikers mét knowledge-entitlement krijgen automatisch `scope="both"` (persoonlijk + org). Personal scope is niet apart aan/uit te zetten — knowledge-entitlement is de enige schakelaar.

---

## Portal-backend: intern autorisatie-endpoint

Nieuw endpoint in portal-backend (onderdeel van deze SPEC):

```
GET /internal/v1/users/{librechat_user_id}/feature/knowledge?org_id=xxx
Authorization: X-Internal-Secret: <secret>

→ 200 {"enabled": true}
→ 200 {"enabled": false}
```

Implementatie:
1. Lookup `portal_users` op `librechat_user_id` → haal `zitadel_user_id` op
2. Bij miss: lookup `portal_orgs` op `org_id` → haal `librechat_container` op
3. Query MongoDB: `db[librechat_container].users.find_one({"_id": ObjectId(librechat_user_id)})` → `openid_id`
4. Lookup `portal_users` op `zitadel_user_id = openid_id` → sla `librechat_user_id` op (cache)
5. Bij elke MongoDB-fout of miss: return `{"enabled": false}` — fail-closed, log WARNING
6. Roep `get_effective_products(zitadel_user_id, db)` aan
7. Return `enabled: "knowledge" in products`
8. Org-admins: altijd `enabled: true` (bestaand admin-bypass gedrag)

---

## Conversation history

De hook bouwt `conversation_history` op uit de bestaande `data["messages"]`:

```python
history = [
    {"role": m["role"], "content": m["content"]}
    for m in messages[:-1]
    if m["role"] in ("user", "assistant")
    and isinstance(m.get("content"), str)
][-6:]   # maximaal 3 wisselgesprekken
```

Meegegeven aan retrieval-api voor coreference-resolutie ("hij" → "Jan Pietersen").

---

## Provenance en bronlabels

```
[Klai Kennisbank — gebruik dit als primaire informatiebron voor deze vraag]

### Vergadernotitie 14 maart 2025  [org]
Het directieteam besloot het marketingbudget met 15% te verlagen...

### Persoonlijke notitie: Budget-review  [persoonlijk]
Eigen aantekening: finale beslissing hangt af van Q1-cijfers...

[Einde kennisbank-context]
```

Het `[org]` / `[persoonlijk]` label per chunk. De system-prompt instructie zorgt dat het LLM bronnen citeert in zijn antwoord.

---

## Logging

```python
async def async_post_call_success_hook(self, data, user_api_key_dict, response):
    kb_meta = data.get("_klai_kb_meta")
    if kb_meta:
        logger.info(
            "KB injection: org=%s user=%s chunks=%d retrieval_ms=%d gate_bypassed=%s",
            kb_meta["org_id"], kb_meta["user_id"],
            kb_meta["chunks_injected"], kb_meta["retrieval_ms"],
            kb_meta["gate_bypassed"],
        )
```

`_klai_kb_meta` wordt in `async_pre_call_hook` op `data` gezet zodat de post-hook er bij kan.

---

## Implementatieplan

### 1. Alembic migratie

`librechat_user_id TEXT NULLABLE` toevoegen aan `portal_users`, met index.

### 2. PortalUser model

`librechat_user_id: Mapped[str | None]` toevoegen aan de SQLAlchemy klasse.

### 3. docker-compose

- `portal-api` toevoegen aan `net-mongodb` network
- `LIBRECHAT_MONGO_ROOT_URI` env var toevoegen aan `portal-api` service
- `motor` toevoegen aan `klai-portal/backend/pyproject.toml`

### 4. portal-backend: intern endpoint

Nieuw endpoint in `app/api/internal.py` (zie boven).

### 5. klai_knowledge.py — upgrades

**Nieuwe env vars:**
```
KNOWLEDGE_RETRIEVE_URL    http://retrieval-api:8040/retrieve
PORTAL_API_URL            http://portal-backend:8000
PORTAL_INTERNAL_SECRET    (zelfde als KNOWLEDGE_INGEST_SECRET of apart)
KNOWLEDGE_RETRIEVE_TIMEOUT  3.0  (omhoog van 2.0 voor reranking)
```

**Verwijderen:** `RETRIEVE_MIN_SCORE`, `kb_slugs` veld in request body

**Toevoegen:**
1. `_check_user_feature(user_id, org_id, cache) -> bool` — gecachede GET naar portal-backend; bij fout → False (fail-closed)
2. `user_id = data.get("user", "")` — extraheren uit request
3. Early return als `not user_id or not await _check_user_feature(user_id, org_id, cache)`
4. `scope="both"` + `user_id` meegeven aan retrieval-api
5. `conversation_history` opbouwen uit messages
6. Chunk-formattering met `[org]`/`[persoonlijk]` labels
7. `_klai_kb_meta` op data voor post-hook logging

### 6. Tests

**Bestand:** `deploy/litellm/tests/test_klai_knowledge_hook.py` — uitbreiden

- `test_blocked_when_no_knowledge_feature` — gebruiker zonder entitlement, geen retrieval
- `test_blocked_when_no_user_id` — leeg `data["user"]`, geen retrieval
- `test_blocked_when_portal_unreachable` — fail-closed: endpoint down → geen injectie
- `test_feature_check_cached` — tweede aanroep doet geen HTTP-call
- `test_both_scope_and_user_id_in_request` — `scope="both"` en `user_id` in body
- `test_conversation_history_passed` — history correct opgebouwd
- `test_gate_bypass_no_injection` — `retrieval_bypassed=True` → geen chunks
- `test_provenance_labels` — `[org]`/`[persoonlijk]` labels aanwezig in output
- `test_kb_meta_logged` — `_klai_kb_meta` op data na succesvolle injectie

### 7. custom_router.py fix

```python
# KB-context aanwezig → niet downgraden ongeacht token-count
if data.get("_klai_kb_meta"):
    return data
```

---

## Acceptance criteria

### Autorisatie (strikt gehandhaafd)

**AC-010-01** WHERE een user geen `knowledge` product-entitlement heeft,
THEN injecteert de hook geen chunks,
AND wordt retrieval-api niet aangeroepen.

**AC-010-02** WHERE `data["user"]` leeg of afwezig is,
THEN injecteert de hook geen chunks.

**AC-010-03** WHERE het portal-autorisatie-endpoint niet bereikbaar is (fail-closed),
THEN injecteert de hook geen chunks,
AND wordt de fout gelogd op WARNING-niveau.

**AC-010-04** WHERE een org-admin chat,
THEN krijgt de admin KB-injectie ongeacht expliciete product-entitlement.

**AC-010-05** WHERE de autorisatiecheck positief retourneert,
THEN wordt het resultaat 300s gecached per `user_id`,
AND doet de volgende aanroep binnen die window geen HTTP-call naar portal-backend.

### ID-mapping (lazy MongoDB-lookup)

**AC-010-06** WHERE `portal_users.librechat_user_id` onbekend is bij eerste aanroep,
THEN doet portal-backend éénmalig een MongoDB-lookup via `librechat_container` van de org,
AND slaat het resultaat op in `portal_users.librechat_user_id`.

**AC-010-07** WHERE de MongoDB-lookup mislukt of de user niet bestaat,
THEN retourneert het endpoint `{"enabled": false}` (fail-closed),
AND wordt de fout gelogd op WARNING-niveau.

**AC-010-08** WHERE `portal_users.librechat_user_id` al bekend is,
THEN wordt MongoDB niet aangesproken.

### Retrieval

**AC-010-09** WHEN de hook retrieval uitvoert,
THEN roept hij `retrieval-api:8040/retrieve` aan.

**AC-010-10** WHERE een geautoriseerde user retrieval triggert,
THEN bevat de request body `scope="both"` en het `user_id`.

**AC-010-11** WHERE de retrieval-gate activeert (`retrieval_bypassed=True`),
THEN injecteert de hook geen chunks.

### Conversation history

**AC-010-12** WHERE de gesprekshistorie twee of meer turns bevat,
THEN stuurt de hook maximaal de laatste 6 turns als `conversation_history`.

**AC-010-13** WHERE de eerste user-message in een gesprek betreft,
THEN stuurt de hook een lege `conversation_history`.

### Provenance

**AC-010-14** WHERE chunks worden geïnjecteerd,
THEN heeft elke chunk een `[org]` of `[persoonlijk]` label.

**AC-010-15** WHERE chunks worden geïnjecteerd,
THEN opent de block met `[Klai Kennisbank — ...]` en sluit met `[Einde kennisbank-context]`.

### Logging

**AC-010-16** WHERE KB-chunks zijn geïnjecteerd en de LLM-aanroep slaagt,
THEN logt de post-call hook org_id, user_id, chunks_injected en retrieval_ms op INFO.

### Token-router

**AC-010-17** WHERE KB-chunks zijn geïnjecteerd (`_klai_kb_meta` aanwezig),
THEN schakelt de token-router niet naar `klai-fast`, ook niet als token-count > 3000.

---

## TRUST 5 checklist

| Pilaar | Vereiste |
|--------|---------|
| **Tested** | ≥ 85% coverage op gewijzigde code; alle AC's gedekt; fail-closed pad expliciet getest; MongoDB-miss pad getest |
| **Readable** | Commentaar bij fail-closed keuze, cache-TTL redenering, en lazy mapping logica |
| **Unified** | Zelfde `httpx.AsyncClient(timeout=...)` patroon; `logger.warning/info` consistent; `motor` alleen in internal.py |
| **Secured** | `X-Internal-Secret` op portal-backend call; `user_id` niet als PII in logs; MongoDB read-only access via dedicated URI |
| **Trackable** | Commit: `feat(litellm): upgrade knowledge hook — retrieval-api + authz + personal scope (KB-010)` |

---

## Token-router fix

`custom_router.py` schakelt naar `klai-fast` als token-count > 3000. Deze drempel is gebouwd voor web-search detectie (veel scraped content = snel model). KB-context triggert dezelfde drempel, maar is het tegenovergestelde: compact, pre-ranked, hoog-kwalitatief. Downgraden bij KB-context is semantisch onjuist.

**Fix in `custom_router.py`:**

```python
# KB-context aanwezig → niet downgraden ongeacht token-count
if data.get("_klai_kb_meta"):
    return data
```

Dit voegt één check toe vóór de token-count logica. `_klai_kb_meta` wordt door de knowledge hook op `data` gezet zodra KB-injectie heeft plaatsgevonden.

**Test:** `test_token_router_skips_downgrade_when_kb_injected` — `_klai_kb_meta` aanwezig + 4000 tokens → model blijft `klai-primary`.

---

## Niet in scope

- MCP search-tool — hook is de correcte architectuur voor always-on retrieval
- Per-KB slug-filtering in de hook — `get_accessible_kb_slugs` logica zit in retrieval-api (toekomstige SPEC)
- Externe MCP API voor derde-partij koppelingen — aparte SPEC na intern traject
- LibreChat-patches of sync-webhooks — lazy MongoDB-mapping is de gekozen aanpak

---

## Implementation Notes

**Implemented:** 2026-03-26

**Main commit:** `fe9a423` — `feat(litellm): upgrade knowledge hook — retrieval-api + authz + personal scope (KB-010)`

**Fixup commits:**
- `3a071e9` — `style(portal): ruff format internal.py (KB-010)`
- `953f178` — `fix(portal): initialize mongo_client to None before try block (pyright KB-010)`
- `ba8b9f2` — `fix(portal): rename alembic migration to avoid revision ID collision (KB-010)`
- `e9fd128` — `fix(portal): resolve alembic revision cycle — rename duplicate a1b2c3d4e5f6 and fix KB-010 chain`
- `2e48bfb` — `fix(deploy): correct PORTAL_API_URL port for litellm (8000 -> 8010)`

**Divergences from plan:**

1. `PORTAL_API_URL` in SPEC was `http://portal-backend:8000` but actual is `http://portal-api:8010` — service name and port corrected during implementation.
2. Alembic migration chain required resolving a pre-existing duplicate revision ID (`a1b2c3d4e5f6_add_rls_policies.py` from KB-009) — created new `c5d6e7f8a9b0_add_rls_policies.py` clean RLS migration as prerequisite.
3. Fixed a `bool("0") == True` bug in cache check (`_check_user_feature`) — cache check must use `== "1"` not `bool()`.

**Acceptance criteria status:**

| AC | Status |
|----|--------|
| AC-010-01 | DONE |
| AC-010-02 | DONE |
| AC-010-03 | DONE |
| AC-010-04 | DONE |
| AC-010-05 | DONE |
| AC-010-06 | DONE |
| AC-010-07 | DONE |
| AC-010-08 | DONE |
| AC-010-09 | DONE |
| AC-010-10 | DONE |
| AC-010-11 | DONE |
| AC-010-12 | DONE |
| AC-010-13 | DONE |
| AC-010-14 | DONE |
| AC-010-15 | DONE |
| AC-010-16 | DONE |
| AC-010-17 | DONE |

All 17 acceptance criteria met. 12/12 tests passing in CI.
