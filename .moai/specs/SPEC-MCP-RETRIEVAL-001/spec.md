---
id: SPEC-MCP-RETRIEVAL-001
version: "0.1.0"
status: draft
created: 2026-05-07
updated: 2026-05-07
author: Mark Vletter
priority: high
issue_number: null
related:
  - SPEC-MCP-AUTH-001 (OAuth dispatcher + `_VerifiedIdentity` foundation — BLOCKING)
  - SPEC-SEC-IDENTITY-ASSERT-001 (caller-service header contract on retrieval-api)
  - SPEC-SEC-SERVICE-AUTH-001 (`klai:internal:retrieval:query` scope, JWT issuance)
  - SPEC-KB-015-01 (retrieval-log contract; we extend with `caller_client_id`)
  - SPEC-KB-014 (gap-detection event contract; we extend with `caller_client_id`)
---

# SPEC-MCP-RETRIEVAL-001: Knowledge retrieval tool for klai-knowledge-mcp (third-party LLM access)

## HISTORY

| Datum | Versie | Wijziging |
|-------|--------|-----------|
| 2026-05-07 | 0.1.0 | Initial draft. Single tool `search_knowledge(query, top_k)` op klai-knowledge-mcp via OAuth-pad uit MCP-AUTH-001. Géén query-rewrite / taxonomy / system-prompt-injectie — die concerns blijven exclusief in de LiteLLM pre-call hook. Telemetrie (retrieval_log + product_events + gap_events) gelabeld op `caller_client_id` zodat externe traffic onderscheidbaar is. Telemetry-helpers verhuizen van `deploy/litellm/klai_knowledge.py` naar nieuwe `klai-libs/retrieval-telemetry/` package. |

---

## Summary

Voeg één `@mcp.tool` `search_knowledge(query, top_k=8)` toe aan `klai-knowledge-mcp/main.py` zodat externe MCP-clients (Claude Desktop, Cursor, ChatGPT custom connectors) de KB van de gebruiker kunnen doorzoeken via het OAuth-pad uit SPEC-MCP-AUTH-001. De tool routeert via de bestaande dispatcher (`_identify_request`), retrieve't via retrieval-api `/retrieve`, en returnt gestructureerde chunks. Telemetrie loopt mee — gelabeld met de OAuth `client_id` van de caller — zodat externe traffic onderscheidbaar blijft van LibreChat in dashboards, eval-sets, en gap-detectie.

De LiteLLM pre-call hook ([deploy/litellm/klai_knowledge.py](../../deploy/litellm/klai_knowledge.py)) blijft de canonieke retrieval-pad voor LibreChat en wijzigt **niet** in functioneel gedrag. Wel verhuist een viertal telemetrie-helpers naar een nieuwe shared lib `klai-libs/retrieval-telemetry/` zodat zowel de LiteLLM-hook als de MCP-tool dezelfde tabel/event-stream voeden zonder code-duplicatie.

**Eindgebruiker-flow:** in Claude Desktop "Add custom connector" → URL `https://mcp.getklai.com/mcp` → user is al geauthenticeerd via SPEC-MCP-AUTH-001 → user vraagt "wat zegt onze documentatie over X?" → Claude roept `search_knowledge` aan → krijgt chunks met `source_url` → citeert in antwoord.

---

## EARS Requirements

### Ubiquitous (altijd actief)

**REQ-1.** De `klai-knowledge-mcp` **shall** een `@mcp.tool`-functie `search_knowledge(query: str, ctx: Context, top_k: int = 8) -> list[dict]` aanbieden met een tool-description die beschrijft: wanneer aanroepen, parameter-semantiek, return-shape, en een citation-instructie ("cite by source_url when present; never invent URLs"). Description in Engels (consumed by tool-host LLM); user-facing strings via die LLM zijn agnostisch van de description-taal.

**REQ-2.** Alle authenticatie en identity-resolutie **shall** verlopen via de bestaande dispatcher `_identify_request(ctx) → _VerifiedIdentity`. De tool **shall** zelf geen header-parsing, geen secret-validatie, en geen claim-extractie uitvoeren. Bij `_IdentificationFailed` **shall** de exception propagateren naar de MCP-host (zelfde gedrag als de save-tools).

**REQ-3.** De `_VerifiedIdentity` dataclass in `klai-knowledge-mcp/main.py` **shall** uitgebreid worden met één optioneel veld `client_id: str | None = None`. Bestaande save-tools **shall** byte-voor-byte ongewijzigd blijven (zij lezen alleen `user_id`/`org_id`/`org_slug`).

**REQ-4.** Het OAuth-pad (`_identify_via_oauth_token`) **shall** `client_id` invullen met de `portal_oauth_clients.client_id` zoals geretourneerd door portal-api's `/internal/mcp-token/verify`. Het LibreChat-pad (`_identify_via_internal_secret`) **shall** `client_id=None` zetten.

**REQ-5.** De portal-api `VerifyResult` ([app/services/mcp_oauth.py:194](../../klai-portal/backend/app/services/mcp_oauth.py)) **shall** uitgebreid worden met `client_id: str | None = None`. `verify_access_token` **shall** dit veld invullen vanuit `portal_mcp_tokens.client_id` → `portal_oauth_clients.client_id` join. `to_dict()` **shall** het veld in de success-response opnemen. Cache-entries **shall** dit veld bevatten (cache-key blijft `access_token_hash` — geen wijziging).

**REQ-6.** De `klai-libs/identity-assert/klai_identity_assert/mcp_token_client.py::VerifyResult` (client-side wrapper) **shall** spiegelend `client_id: str | None` veld krijgen, gevuld vanuit de portal-response.

**REQ-7.** Een nieuwe Python-package `klai-libs/retrieval-telemetry/` **shall** vier publieke functies aanbieden:
- `fire_retrieval_log(*, org_id, user_id, chunk_ids, reranker_scores, query, caller_client_id=None) -> None`
- `fire_product_event_knowledge_queried(*, org_id, user_id, chunks_returned, retrieval_ms, caller_client_id=None, auth_path) -> None`
- `fire_gap_event(*, org_id, user_id, query_text, gap_type, chunks, retrieval_ms, taxonomy_node_ids=None, caller_client_id=None) -> None`
- `classify_gap(chunks: list[dict]) -> str | None`

Alle functies **shall** fire-and-forget zijn (`asyncio.create_task` met independent task-group of equivalent). Exceptions **shall** intern gelogd worden en nooit propagateren naar de caller.

**REQ-8.** De `deploy/litellm/klai_knowledge.py` **shall** de bestaande inline helpers (`_fire_retrieval_log`, `_fire_gap_event`, `_classify_gap`, en de inline `product_events` POST) vervangen door imports uit `klai-libs/retrieval-telemetry/`. Aanroepen **shall** `caller_client_id=None` doorgeven (LibreChat-pad) — gedrag identiek aan vandaag.

**REQ-9.** De portal-api `/internal/v1/retrieval-log` endpoint **shall** een optioneel `caller_client_id: str | None` veld accepteren in de request-body en opslaan in `retrieval_log.caller_client_id`. Ontbrekend / `null` veld **shall** `NULL` opslaan (LibreChat-default).

**REQ-10.** Een Alembic-migratie **shall** `retrieval_log` uitbreiden met:
```sql
ALTER TABLE retrieval_log ADD COLUMN caller_client_id TEXT NULL;
CREATE INDEX retrieval_log_caller_client_id_idx
  ON retrieval_log (caller_client_id)
  WHERE caller_client_id IS NOT NULL;
```
De migratie **shall** non-blocking zijn (geen `NOT NULL`, geen default-fill van bestaande rijen).

**REQ-11.** `product_events.properties` (JSONB) **shall** voor MCP-callers `caller_client_id` en `auth_path: "oauth_client"` bevatten. Voor LibreChat-callers **shall** `caller_client_id` afwezig zijn en `auth_path: "librechat"` worden gezet — dit is een aanvulling, geen breaking change voor bestaande consumers.

**REQ-12.** De tool **shall** `top_k` clampen naar `[1, 15]` voordat de retrieval-call geplaatst wordt. Out-of-range waardes **shall** stilzwijgend geclamped worden, niet als error gerapporteerd (defensive default; externe LLMs gokken vaak top_k=20).

**REQ-13.** De tool **shall** `httpx.AsyncClient(timeout=3.0)` gebruiken voor de retrieval-api call (zelfde timeout als de LiteLLM-hook).

**REQ-14.** De retrieval-call **shall** identiek auth-pad gebruiken als de LiteLLM-hook: JWT (`klai:internal:retrieval:query` scope) preferred, fallback naar `X-Internal-Secret`, met `X-Caller-Service: knowledge-mcp` header. Het bestaande `_retrieve_jwt_headers` + `_retrieve_legacy_headers` patroon **shall** hergebruikt worden — wordt onderdeel van een gedeelde helper of geïmporteerd uit `klai-libs/retrieval-telemetry/` (zelfde plek omdat het bij dezelfde uitgaande retrieval-api call hoort).

**REQ-15.** De tool-result **shall** een `list[dict]` zijn waarin elke chunk de keys `title: str`, `source_url: str | None`, `text: str`, `score: float | None`, `scope: Literal["personal", "org"]` bevat. Géén markdown system-prompt-blok, géén citation-instructies in de output (die staan in REQ-1's tool-description).

**REQ-16.** De OAuth-scope `mcp:knowledge` (uit SPEC-MCP-AUTH-001) **shall** zowel save-tools als `search_knowledge` autoriseren. Géén nieuwe scope wordt geïntroduceerd in v0.1.0.

### State-driven (conditional)

**REQ-17.** **While** retrieval-api een 4xx-response retourneert (config / auth error), **shall** de tool een `mcp.server.fastmcp.exceptions.ToolError` raisen met een generieke bilingual NL/EN message ("Knowledge base unavailable. Please try again."). De HTTP-statuscode **shall** in de log-line staan, niet in de user-facing message (info-leak prevention).

**REQ-18.** **While** retrieval-api een 5xx-response of een `httpx.TimeoutException` produceert, **shall** de tool een `ToolError` raisen met een log-line die het exception-type en de retrieval_ms bevat.

**REQ-19.** **While** de retrieval-call success retourneert maar `chunks=[]`, **shall** de tool `[]` retourneren (legitieme lege resultaten) en alle telemetrie-emits **shall** alsnog vuren (REQ-22, REQ-23 — ook lege resultaten zijn signaal voor gap-detectie en query-volume).

**REQ-20.** **While** een telemetrie-emit faalt (Redis down, portal-api 5xx, network error), **shall** de fout intern gelogd worden en **shall** de tool het succesvolle resultaat alsnog retourneren. Telemetrie-failures **shall nooit** een geslaagde retrieval kapotmaken.

### Event-driven (trigger-based)

**REQ-21.** **When** `search_knowledge` succesvol een retrieval-call afrondt (incl. `chunks=[]`), **shall** de tool `fire_retrieval_log` aanroepen met `caller_client_id=identity.client_id`, `chunk_ids` en `reranker_scores` uit het retrieval-resultaat, en de raw `query`-string.

**REQ-22.** **When** `search_knowledge` succesvol een retrieval-call afrondt, **shall** de tool `fire_product_event_knowledge_queried` aanroepen met `chunks_returned=len(chunks)`, `retrieval_ms` (gemeten met `time.monotonic`), `caller_client_id=identity.client_id`, `auth_path="oauth_client"` (LiteLLM-hook gebruikt dezelfde helper met `auth_path="librechat"`).

**REQ-23.** **When** `classify_gap(chunks)` een niet-`None` waarde retourneert, **shall** de tool `fire_gap_event` aanroepen met dezelfde tagging (`caller_client_id`, `auth_path`).

**REQ-24.** **When** de telemetry-lib-extractie (Fase 1) gemerged wordt, **shall** de bestaande `deploy/litellm/test_klai_knowledge_*.py` test-suite groen blijven zonder wijzigingen aan test-code (alleen import-paden mogen aanpassen). Dit is de regression-bewijslast dat de extractie geen functionele wijziging doet.

### Optional Features

**REQ-25.** De tool **may** in een toekomstige minor (v0.1.x) een `image_urls`-veld per chunk retourneren wanneer Qdrant-payload `image_urls` bevat. Out of scope voor v0.1.0; vereist een aparte MCP-resource-flow voor het serveren van images.

**REQ-26.** De portal-api admin-UI **may** een dashboard-view tonen van retrieval-volume per `caller_client_id`. Out of scope voor v0.1.0 — de schema-velden zijn er, dashboard-creatie volgt op basis van real traffic.

---

## Architecture decisions

### A1. Eén tool, twee parameters

**Keuze: `search_knowledge(query, top_k=8)`.** Geen `scope`, geen `kb_slugs`, geen `notebook_id`, geen `conversation_history`.

**Reden:** externe LLMs hebben geen UI om scope/KB-filters via te zetten, en RLS in retrieval-api zorgt automatisch dat de tenantscope klopt (`scope="both"` betekent "alles wat deze user mag zien"). `kb_slugs`-filtering is een power-user-feature die zinloos is zonder een `list_knowledge_bases` discovery-tool — beide vallen in dezelfde YAGNI-categorie. Conversation-history-coreference doet de externe LLM zelf (instructie staat in tool-description: "Self-contained: resolve pronouns and references yourself before passing").

### A2. `_VerifiedIdentity` minimaal uitbreiden — geen `auth_path` veld

**Keuze: alleen `client_id: str | None = None` toevoegen.**

**Alternatieve overweging:** een `auth_path: Literal["librechat", "oauth_client"]` veld zou expliciet zijn. Maar dat veld is afgeleide informatie: `client_id is None` betekent al "LibreChat-pad" en `client_id is not None` betekent OAuth-pad. Een redundant veld is een onderhoudslast. Telemetrie-emits stellen `auth_path` zelf vast op basis van `client_id`.

### A3. Telemetry-helpers naar shared lib

**Keuze: `klai-libs/retrieval-telemetry/`** als nieuwe Python-package, geïmporteerd door zowel `deploy/litellm/klai_knowledge.py` als `klai-knowledge-mcp/main.py`.

**Reden:** beide callers schrijven naar **dezelfde** `retrieval_log` tabel en **dezelfde** `product_events.event_type='knowledge.queried'` stream. Een gekopieerde implementatie zou stilletjes uiteenlopen wanneer het schema verandert. Dit is een legitieme abstractie (gedeeld contract), niet een toevallige overlap. Het pakket bevat alleen wat allebei de callers nodig hebben — geen hook-specifieke logica zoals templates of taxonomy.

### A4. Shared lib bevat ook de retrieval-call zelf

**Keuze: `klai-libs/retrieval-telemetry/` exporteert ook een dunne `retrieve_chunks(*, query, org_id, user_id, top_k, scope="both") -> RetrievalResult` functie** die JWT-of-legacy auth doet en de `/retrieve` POST plaatst.

**Reden:** de auth-keuze (JWT met scope `klai:internal:retrieval:query` met fallback naar X-Internal-Secret) is nontrivial en byte-identiek voor beide callers. Niet één auth-keuze duplicaten. De LiteLLM-hook bouwt zijn eigen rijke retrieve-body (raw_query + rewritten + taxonomy_node_ids + history), dus de helper accepteert die als optionele velden — de MCP-tool gebruikt alleen de minimale set.

### A5. Resultaat-shape: gestructureerde dict, geen markdown-blok

**Keuze: `list[dict]` met expliciete velden.**

**Reden:** de MCP-tool-protocol levert het resultaat aan de host-LLM, die het zelf in zijn antwoord rendert. Een markdown-systeem-prompt-blok ("STRIKT: kopieer URL exact") hoort thuis in een **system prompt**, niet in een **tool result**. De citation-instructies leven in de tool-description (REQ-1) waar de host-LLM ze leest tijdens het beslissen wanneer en hoe te citeren — exact zoals de save-tool descriptions het doen voor titel-/content-/tag-gedrag.

### A6. Failure-mode: `ToolError`, niet stilzwijgend lege resultaten

**Keuze: retrieval-api 4xx/5xx/timeout = `ToolError` raise.**

**Reden:** een lege return op upstream-failure zou de host-LLM doen zeggen "ik kon niets vinden in jullie KB", terwijl de KB simpelweg onbereikbaar was — dat is een gevaarlijke hallucination-vector. `ToolError` zorgt dat de host-LLM de failure expliciet rapporteert aan de gebruiker. Dit is bewust strikter dan de LiteLLM-hook (die fail-loud doet via system-prompt-injectie); voor een MCP-tool is exception het juiste contract.

### A7. Géén nieuwe OAuth-scope

**Keuze: `mcp:knowledge` dekt zowel save als search.**

**Reden:** scope-splitsing (`mcp:knowledge:read` vs `mcp:knowledge:write`) zou waarde toevoegen pas wanneer een klant een specifiek read-only-token-use-case heeft (bv. een publishing-tool die nooit mag schrijven). YAGNI tot dat moment. Bij introductie achteraf:
- `mcp:knowledge` blijft super-scope (volledig read+write)
- `mcp:knowledge:read` wordt narrower scope, alleen toegekend aan nieuw geregistreerde clients die expliciet read-only requesten
- consent-UI krijgt een toelichting over scope-niveaus

Backwards-compatible toevoeging.

### A8. Géén query-rewrite of taxonomy-classify in MCP-pad

**Keuze: passthrough van de query zoals de externe LLM hem aanlevert.**

**Reden:** de LiteLLM-hook doet query-rewrite (Mistral-call) omdat LibreChat-input rauwe user-tekst is met pronouns/follow-ups. Externe MCP-callers zijn zélf frontier LLMs (Claude, GPT, Cursor's modelkeuze) — die hebben de query al geformuleerd in plaats van een mens. Een tweede rewrite-stap is verspilling van budget en latency. Taxonomy-classify is hetzelfde verhaal: vereist coverage-data per KB en is afhankelijk van klantspecifieke taxonomie die de externe LLM toch niet kent. De gap-event helper ziet dit verschil niet — gap-detectie is generic en blijft waardevol.

### A9. Caller-attribution via `client_id` (niet via `client_name`)

**Keuze: telemetrie krijgt `caller_client_id` (de DCR-uitgegeven `client_id` string), niet `client_name` of een human-readable label.**

**Reden:** `client_name` kan door de DCR-aanvraag vrij gekozen worden (zie SPEC-MCP-AUTH-001 § "Client_name spoofing" open question) — een attacker kan zich voordoen als "Klai Official". `client_id` is server-uitgegeven en niet-spoofbaar. Een dashboard-view die een mooie label wil tonen kan joinen op `portal_oauth_clients.client_name` voor display, maar de **storage** is op `client_id` zodat aggregaties op de juiste sleutel werken.

---

## Out of scope

1. **LiteLLM pre-call hook functioneel wijzigen.** Hij blijft de canonieke retrieval-pad voor LibreChat. Alleen telemetry-helpers verhuizen naar shared lib (gedrag identiek).
2. **Image-URLs in tool-output.** Vereist een aparte MCP-resource-flow voor image-serving; later via REQ-25.
3. **`list_knowledge_bases()` of `get_document(id)` discovery-tools.** YAGNI tot een klant erom vraagt.
4. **Per-tool of per-KB OAuth-scopes.** YAGNI; super-scope `mcp:knowledge` dekt v0.1.0.
5. **Pagination of result-set.** `top_k` clamp 1-15 is voldoende voor LLM-context-budget.
6. **Conversation-history coreference resolution.** Externe LLMs doen dit zelf voordat ze de tool aanroepen.
7. **Rate-limiting per OAuth-client.** Out of scope; retrieval-api heeft eigen rate-limit en token-issuance is al bounded via SPEC-MCP-AUTH-001 REQ-27 (DCR rate-limit).
8. **Admin-dashboard voor MCP-traffic.** Schema-velden landen, dashboard-creatie volgt op basis van real-data (REQ-26 optional).

---

## Implementation plan (Phases)

Zie `plan.md` voor de volledige fasering. Korte versie:

- **Fase 0** — Wachten op SPEC-MCP-AUTH-001 in `main`. Blocking dependency.
- **Fase 1** — `klai-libs/retrieval-telemetry/` package extracten uit LiteLLM-hook. Regression-bewijs: `deploy/litellm/test_klai_knowledge_*.py` blijft groen ongewijzigd.
- **Fase 2** — Schema-migratie + portal-api endpoint-update + `VerifyResult.client_id` + `_VerifiedIdentity.client_id`.
- **Fase 3** — `search_knowledge` tool + tests.
- **Fase 4** — Manual e2e in Claude Desktop.

---

## Acceptance criteria

Zie `acceptance.md` voor Given/When/Then-scenarios. Kernpunten:

1. **AC-1** — Geauthenticeerde Claude Desktop-user kan `search_knowledge("..."`) aanroepen en krijgt chunks terug met `source_url` waar Qdrant-payload dat heeft.
2. **AC-2** — LibreChat-pad (LiteLLM-hook) blijft byte-functioneel ongewijzigd: bestaande regression-tests groen, retrieval-volume-metrics in productie ongewijzigd in de week na merge.
3. **AC-3** — `retrieval_log` rij na MCP-call heeft `caller_client_id` ingevuld; `product_events.knowledge.queried` rij heeft `properties->>'caller_client_id'` en `auth_path='oauth_client'`.
4. **AC-4** — LibreChat-call schrijft `retrieval_log` rij met `caller_client_id IS NULL` en `product_events.properties->>'auth_path'='librechat'`.
5. **AC-5** — Retrieval-api timeout (3s) → tool raised `ToolError`, geen lege return.
6. **AC-6** — `top_k=20` wordt geclamped naar 15; `top_k=0` naar 1.
7. **AC-7** — Cross-tenant test: token van org A verkrijgt geen chunks van org B (RLS-bewijs via SPEC-MCP-AUTH-001 REQ-22 audience-binding + retrieval-api RLS).
8. **AC-8** — Gap-detectie vuurt op een query met irrelevante chunks; `caller_client_id` zit in de gap-event payload.
9. **AC-9** — Telemetry-failure (Redis simuleer-down): tool retourneert nog steeds chunks, telemetry-fail wordt gelogd.
10. **AC-10** — Save-tools (`save_personal_knowledge` etc.) blijven byte-voor-byte werken; geen wijziging in hun tests.

---

## Risks & mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Telemetry-lib refactor breekt LiteLLM-hook (gedrag-drift) | medium | high | Fase 1 scope: pure code-move, byte-identiek gedrag voor `caller_client_id=None`. Bestaande hook-tests groen ongewijzigd is harde gate. |
| `retrieval_log` migratie blokkeert portal-api boot | low | high | `ADD COLUMN ... NULL` is non-blocking; partial index wordt CONCURRENTLY gebouwd in post_deploy.sql (zelfde patroon als SPEC-MCP-AUTH-001). |
| `client_id` propagation breekt audience-binding | very low | critical | We voegen alleen toe aan success-path; deny-paden (`unknown_token`, `audience_mismatch`, etc.) raken we niet aan. Cache-key blijft `access_token_hash`. |
| Externe LLM vraagt vage queries → slechte resultaten → klant blameert Klai | medium | low | Tool-description zegt expliciet "Self-contained: resolve pronouns yourself"; gap-events surfacing in admin maakt het zichtbaar. |
| Externe client polt elke keystroke (DOS-vector) | low | low | retrieval-api heeft eigen rate-limit; OAuth-token issuance is bounded; revisit als real abuse blijkt. |
| Search-quality regressie t.o.v. LiteLLM-hook (geen rewrite/taxonomy) | medium | medium | Bewust ontwerp; gap-events zullen het signaleren. Als data laat zien dat externe LLMs slechtere queries genereren dan onze rewrite, dan voegen we rewrite-as-option toe in v0.2 (param `rewrite=true`). |
| Multiple parallel tool-calls per request laten retrieval-api over de timeout | low | medium | 3.0s timeout is conservatief; monitor in productie. Verhoogbaar via env var zonder code change. |

---

## Open questions

(Geen open questions in v0.1.0. Alle ontwerpkeuzes zijn vastgelegd in A1-A9.)

Toekomstige beslismomenten (geen blocker voor v0.1.0):

- **Wanneer rewrite-as-option toevoegen?** Trigger: gap-event-rate van OAuth-traffic > 1.5× LibreChat-rate over 30 dagen.
- **Wanneer `mcp:knowledge:read` scope splitsen?** Trigger: eerste klant met expliciete read-only-use-case.
- **Wanneer image-URLs?** Trigger: real productieve traffic waar text-only citaties tekortschieten (verifieerbaar via gap-events met type `images_missing`).

---

## References

- [klai-knowledge-mcp/main.py](../../klai-knowledge-mcp/main.py) — host file voor de nieuwe tool
- [klai-knowledge-mcp/dispatcher.py](../../klai-knowledge-mcp/dispatcher.py) — OAuth/LibreChat branch primitives (SPEC-MCP-AUTH-001)
- [klai-portal/backend/app/services/mcp_oauth.py](../../klai-portal/backend/app/services/mcp_oauth.py) — `VerifyResult` definition (REQ-5 target)
- [klai-libs/identity-assert/klai_identity_assert/mcp_token_client.py](../../klai-libs/identity-assert/klai_identity_assert/mcp_token_client.py) — client-side wrapper (REQ-6 target)
- [klai-retrieval-api/retrieval_api/api/retrieve.py](../../klai-retrieval-api/retrieval_api/api/retrieve.py) — `klai:internal:retrieval:query` scope holder
- [deploy/litellm/klai_knowledge.py](../../deploy/litellm/klai_knowledge.py) — telemetry-helper source (Fase 1 extraction target)
- [.claude/rules/klai/projects/knowledge-ingest.md](../../.claude/rules/klai/projects/knowledge-ingest.md) — multi-layer data threading; result-shape contract
- [.moai/specs/SPEC-MCP-AUTH-001/spec.md](../SPEC-MCP-AUTH-001/spec.md) — blocking foundation
- [MCP Specification — Tools](https://modelcontextprotocol.io/specification/draft/server/tools)
- [MCP Specification — ToolError contract](https://modelcontextprotocol.io/specification/draft/basic/server-features#errors)
