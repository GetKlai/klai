# Regular chat: knowledge retrieval en citations

> **Let op — regelnummers verouderd (2026-06-08).** Dit document is geschreven tegen
> `origin/main` commit `516483a`. Op 2026-06-07 zijn twee god-modules opgesplitst:
> `deploy/litellm/klai_knowledge.py` (commit `dd4225695`, 1614→~1250 regels) en
> `klai-retrieval-api/.../api/retrieve.py` (commit `91e8db29b`, 929→759 regels). **Het
> gedrag is ongewijzigd, alleen de locaties zijn verplaatst.** Alle
> `klai_knowledge.py:33xx-42xx`- en `retrieve.py:2xx-8xx`-regelverwijzingen hieronder
> wijzen nu voorbij het einde van die bestanden. Gebruik de symbool-/modulenamen in plaats
> van de regelnummers. De verplaatste logica woont nu in (zie ook § Bronbestanden):
> - scopevertaling → `deploy/litellm/klai_kb_scope_policy.py` (`build_retrieve_body`, `resolve_kb_retrieval_scope`)
> - query-rewrite → `deploy/litellm/klai_kb_query_rewrite.py`
> - antwoordbeleid / `_klai_kb_meta` → `deploy/litellm/klai_kb_answer_policy.py` (`KbAnswerPolicy.to_kb_meta`)
> - context-block → `deploy/litellm/klai_kb_context_prompt.py`
> - citation-rendering → `deploy/litellm/klai_kb_citation_render.py` (`compose_(non_)streaming_kb_response`, `render_evidence_context`)
> - retrieve-pipeline → `retrieval_api/api/retrieve.py` (handler op `:82`) + `api/ranking.py` + `api/page_context.py`

## Onderzoeksmethode

Dit document volgt alleen broncode, tests en runtimeconfiguratie. Bestaande docs,
specs en runbooks zijn niet gebruikt als inhoudelijke bron.

CodeIndex is gebruikt na een clean main-index repair. `scripts/codeindex-health.sh
--repair` bouwde de shared base index opnieuw vanuit
`/Users/mvletter/.codeindex/_worktrees/klai-main`; de index kwam uit op
`origin/main` commit `516483a`. Omdat deze Conductor worktree branch-local
wijzigingen bovenop main kan hebben, is de lokale broncode steeds leidend
geweest en is CodeIndex alleen als navigatiehulp gebruikt.

## Korte conclusie

In het reguliere chatpad is knowledge retrieval een LiteLLM pre-call concern.
De portal bewaart per gebruiker welke KB-scope en mode actief is. LibreChat
stuurt chatverkeer naar LiteLLM. De LiteLLM `klai_knowledge` callback beslist
per request of er retrieval nodig is, roept `/retrieve` aan met de juiste
scope, injecteert eventueel KB-context in de system prompt, en zet `_klai_kb_meta`
voor de post-call citation guard. De citation engine citeert niet uit de
modeltekst en niet uit ruwe chunk-URLs; zichtbare KB-bronnen komen uit
`EvidencePack.sources`.

Web Search staat naast dit pad. Web aan of uit verandert de KB-retrieval body
niet. In de "geen KB geselecteerd" branch voegt de hook alleen een runtime block
toe als Web Search beschikbaar is. Met een KB in scope loopt retrieval gewoon
door; eventuele webresultaten zijn toolcontext, geen KB-citation sources.

## Reguliere chat entrypoint

De portalroute `/app/chat` rendert `ChatPage` en laadt LibreChat in een iframe.
De src gaat naar de tenant-specifieke LibreChat host en altijd naar
`/oauth/openid`. De topbar rendert `ChatConfigBar`, de UI waarmee de gebruiker
collections en Open/Strict kiest.

Bronnen:

- `klai-portal/frontend/src/routes/app/chat.tsx:48-74`
- `klai-portal/frontend/src/routes/app/chat.tsx:197-204`
- `klai-portal/frontend/src/routes/app/_components/ChatConfigBar.tsx:42-50`
- `deploy/librechat/librechat.yaml:121-128`
- `deploy/litellm/config.yaml:85-87`

LiteLLM voert callbacks in deze volgorde uit:

1. `klai_knowledge.klai_knowledge_hook`
2. `custom_router.token_router`

Daardoor injecteert de KB-hook eerst context en metadata; daarna kan de router
op `_klai_kb_meta` reageren.

## UI state en voorkeuren

De voorkeuren zitten in `KBPref`:

- `kb_retrieval_enabled`
- `kb_personal_enabled`
- `kb_slugs_filter`
- `kb_narrow`
- `kb_pref_version`
- `active_template_ids`

`ChatConfigBar` gebruikt een tri-state contract voor org-KBs:

| `kb_slugs_filter` | Betekenis in UI en backend |
| --- | --- |
| `null` | Alle org-KBs geselecteerd. |
| `[]` | Geen org-KBs geselecteerd. |
| `["slug"]` | Expliciete subset geselecteerd. |

Persoonlijk staat los via `kb_personal_enabled`. "Geen knowledge base
geselecteerd" is dus niet `kb_slugs_filter=null`, maar:

```text
kb_personal_enabled = false
kb_slugs_filter = []
```

De UI toont dan `Algemene AI` en `toggleAll` zet beide scopes uit. De portal
PATCH bewaart `[]` expliciet; de codecomment in de docstring noemt nog een oude
normalisatie naar `null`, maar de werkelijke code bewaart `[]`.

Open/Strict is `kb_narrow`:

- Open: `kb_narrow=false`
- Strict: `kb_narrow=true`

Bronnen:

- `klai-portal/frontend/src/routes/app/_components/ChatConfigBar.tsx:15-22`
- `klai-portal/frontend/src/routes/app/_components/ChatConfigBar.tsx:69-73`
- `klai-portal/frontend/src/routes/app/_components/ChatConfigBar.tsx:93-100`
- `klai-portal/frontend/src/routes/app/_components/ChatConfigBar.tsx:110-118`
- `klai-portal/frontend/src/routes/app/_components/ChatConfigBar.tsx:258-283`
- `klai-portal/backend/app/api/app_account.py:64-79`
- `klai-portal/backend/app/api/app_account.py:734-816`

## Portal naar LiteLLM feature lookup

Voor elk chatrequest vraagt de LiteLLM hook de portal om de knowledge feature
voor de LibreChat user:

```text
GET /internal/v1/users/{librechat_user_id}/feature/knowledge?org_id={org_id}
```

De portal mapt de LibreChat Mongo ObjectId naar een portal user en geeft terug:
entitlement (`enabled`), KB preferences, `kb_pref_version`,
`zitadel_user_id`, en tenant `telemetry_level`.

De LiteLLM hook cachet de versiepointer 30 seconden, featuredata 300 seconden,
en een latest fallback 86400 seconden. Bij portal failure gebruikt de hook een
stale feature cache als die er is; anders faalt entitlement gesloten
(`enabled=false`).

Bronnen:

- `klai-portal/backend/app/api/internal.py:753-773`
- `klai-portal/backend/app/api/internal.py:776-891`
- `deploy/litellm/klai_knowledge.py:1595-1667`

## LiteLLM pre-call flow

De pre-call hook verwerkt het request in deze volgorde:

1. Skip niet-chatcompletion of triviale/no-query requests.
2. Sanitize provider context.
3. Safety scan op de laatste user input en actieve tool results.
4. Skip title generation.
5. Fetch actieve templates.
6. Meta-query early return. Vragen over Klai zelf krijgen geen retrieval.
7. Fetch feature en preferences.
8. Bepaal KB-scope en mode.
9. Rewrite/classify taxonomy alleen als er expliciete org-KBs in scope zijn.
10. Roep retrieval-api `/retrieve` aan.
11. Injecteer KB/system prompt en `_klai_kb_meta`.

Bronnen:

- `deploy/litellm/klai_knowledge.py:3330-3369`
- `deploy/litellm/klai_knowledge.py:3371-3420`
- `deploy/litellm/klai_knowledge.py:3475-3530`
- `deploy/litellm/klai_knowledge.py:3532-3686`
- `deploy/litellm/klai_knowledge.py:3695-3699`

### Scopevertaling

De hook vertaalt preferences naar retrieval-api scope:

| Preferences | Retrieval-api request |
| --- | --- |
| `personal=false`, `slugs=[]` | Geen retrieval. Open wordt General AI; Strict weigert KB-only. |
| `personal=true`, `slugs=[]` | `scope="personal"`, geen `kb_slugs`. Retrieval-api forceert canonieke persoonlijke slug. |
| `personal=false`, `slugs=null` | `scope="org"`, geen `kb_slugs`: alle org-KBs. |
| `personal=false`, `slugs=[...]` | `scope="org"`, `kb_slugs=[...]`. |
| `personal=true`, `slugs=null` | `scope="both"`, geen `kb_slugs`, `include_owned_private_kbs=true`: alle org-KBs plus eigen private KBs. |
| `personal=true`, `slugs=[...]` | `scope="both"`, `kb_slugs=[...]`: subset org-KBs plus persoonlijke scope. |

Bronnen:

- `deploy/litellm/klai_knowledge.py:3475-3530`
- `deploy/litellm/klai_knowledge.py:3654-3686`
- `klai-retrieval-api/retrieval_api/services/search.py:70-163`

### Geen KB geselecteerd

Als entitlement ontbreekt, retrieval uit staat, of alle scopes uit staan:

- Open/default krijgt `GENERAL_CHAT_SYSTEM_PROMPT`.
- Strict krijgt een KB-only no-scope notice en mag niet naar algemene kennis
  degraderen.
- Er wordt geen `/retrieve` call gedaan.
- Er komt geen gewone KB-citation pipeline, behalve waar de strict notice als
  promptcontract werkt.

Bronnen:

- `deploy/litellm/klai_knowledge.py:3371-3420`
- `deploy/litellm/klai_knowledge.py:3492-3514`
- `deploy/litellm/klai_chat_prompts.py:228-260`

### KB geselecteerd

Als er een KB-scope is:

- De hook vereist `zitadel_user_id`. Zonder die ID faalt de hook luid.
- De request body bevat `query`, `raw_query`, `org_id`, `user_id`, `scope`,
  `top_k`, `conversation_history`, `telemetry_level`, en `kb_narrow`.
- `kb_narrow=true` gaat mee naar retrieval-api zodat Strict niet door de
  retrieval gate kan worden gebypasst.

Bronnen:

- `deploy/litellm/klai_knowledge.py:3422-3473`
- `deploy/litellm/klai_knowledge.py:3654-3673`
- `klai-retrieval-api/retrieval_api/api/retrieve.py:334-342`

## Retrieval-api `/retrieve`

Het retrieval endpoint valideert eerst scope en identiteit:

- `scope in ("personal", "both")` vereist `user_id`.
- Personal-role callers worden naar `scope="personal"` geforceerd.
- `verify_body_identity` verifieert org/user tegen de caller.
- Telemetry level wordt verlaagd naar de tenant-canonieke waarde.

Daarna loopt de pipeline:

1. Coreference rewrite.
2. Dense en sparse embeddings.
3. Gate check. In Strict (`kb_narrow=true`) wordt de gate overgeslagen en gaat
   retrieval altijd door.
4. Optionele source router als `kb_slugs is None`, router aan staat, scope
   `org` of `both` is, en er geen gate bypass is.
5. Qdrant hybrid search met vector_chunk, vector_questions en optioneel
   vector_sparse via RRF.
6. Optioneel Graphiti search en RRF merge.
7. Link expansion via `links_to`.
8. Authority boost, page context boost.
9. Rerank.
10. Link-expand score boost.
11. Quality floor filter.
12. Source-aware selection.
13. Feedback quality boost.
14. Evidence-tier shadow of active ordering.
15. Parent-text lookup.
16. `ChunkResult` output.
17. Confidence band.
18. `EvidencePack`.

Bronnen:

- `klai-retrieval-api/retrieval_api/api/retrieve.py:239-290`
- `klai-retrieval-api/retrieval_api/api/retrieve.py:316-342`
- `klai-retrieval-api/retrieval_api/api/retrieve.py:374-405`
- `klai-retrieval-api/retrieval_api/api/retrieve.py:405-572`
- `klai-retrieval-api/retrieval_api/api/retrieve.py:584-681`
- `klai-retrieval-api/retrieval_api/api/retrieve.py:689-712`
- `klai-retrieval-api/retrieval_api/api/retrieve.py:851-866`

### Qdrant filters

Retrieval-api bouwt server-side filters, niet alleen client-side intent:

- Altijd `org_id`.
- `scope="personal"` met `user_id` voegt `kb_slug=personal_kb_slug(user_id)`
  toe. Dit voorkomt dat andere private user-KBs via alleen `user_id` meeliften.
- `scope="org"` en `scope="both"` sluiten private chunks uit, behalve private
  chunks van de caller.
- `scope="both"` beperkt eigen private chunks tot de canonieke persoonlijke KB
  plus expliciet geselecteerde slugs, behalve bij
  `include_owned_private_kbs=true`.
- `kb_slugs` is een org-filter; in `scope="both"` mag de persoonlijke branch
  niet door die org-slug filter worden weggefilterd.
- Taxonomy en tags worden als extra filters toegevoegd, nooit als vervanging
  van org/scope filters.

Bronnen:

- `klai-retrieval-api/retrieval_api/services/search.py:70-163`
- `klai-retrieval-api/retrieval_api/services/search.py:179-206`
- `klai-retrieval-api/tests/test_scope_filter.py:45-293`

## EvidencePack en citation contract

Retrieval-api bouwt een `EvidencePack` uit de served chunks. Dit is de
deterministische citation contractlaag:

- `items` zijn citable evidence items met `evidence_id`, chunk metadata, tekst,
  scores, source metadata en image URLs.
- `sources` zijn maximaal drie gededupeerde document-level bronnen.
- Publieke bronnen dedupen op genormaliseerde URL.
- Uploads zonder `source_url` blijven citable via `artifact_id`.
- Chunks zonder URL en zonder artifact worden niet citeerbaar.
- Als niets citeerbaar is, zet het pack `no_citable_reason`.

Bronnen:

- `klai-retrieval-api/retrieval_api/models.py:113-145`
- `klai-retrieval-api/retrieval_api/services/evidence_pack.py:124-235`
- `klai-retrieval-api/retrieval_api/services/evidence_pack.py:238-266`
- `klai-retrieval-api/tests/test_evidence_pack.py:10-369`

LiteLLM gebruikt daarna alleen dit contract:

- `trusted_sources_from_evidence_pack` leest alleen `evidence_pack.sources`.
- `evidence_pack_items_as_chunks` maakt promptbare chunks van
  `evidence_pack.items`.
- `compose_answer_with_trusted_sources` kiest nooit bronnen uit door het model
  geschreven URLs of raw chunks; kandidaten moeten uit EvidencePack komen.

Bronnen:

- `klai-libs/citations/klai_citations/__init__.py:920-957`
- `klai-libs/citations/klai_citations/__init__.py:960-990`
- `klai-libs/citations/klai_citations/__init__.py:1115-1145`

## KB-context en post-call citation rendering

Als retrieval terugkomt:

- `retrieval_bypassed=true`: de hook injecteert alleen de mode prompt en zet
  `_klai_kb_meta.gate_bypassed=true`; post-call citation rendering slaat over.
- Missing `evidence_pack`: de hook faalt dicht voor bronfallback. Er worden
  geen raw chunk citations opgebouwd.
- Zero chunks: Strict krijgt een expliciete "niet in de kennisbank" instructie;
  Open mag een algemene fallback geven met disclaimer.
- Chunks present: Strict krijgt "answer strictly using only the sources below";
  Open krijgt "use this as supplementary context" en mag stabiele algemene
  kennis aanvullen.
- De prompt verbiedt modelgeschreven source lists, URLs, footnotes en citation
  numbers. De app voegt bronnen na generation toe.

Bronnen:

- `deploy/litellm/klai_knowledge.py:3778-3839`
- `deploy/litellm/klai_knowledge.py:3840-3903`
- `deploy/litellm/klai_knowledge.py:3923-4023`
- `deploy/litellm/klai_knowledge.py:4031-4090`
- `deploy/litellm/klai_knowledge.py:4119-4232`

De post-call renderer is mode-aware:

- Geen trusted sources in Strict: vervang de modeltekst door een canned refusal.
- Geen trusted sources in Open: laat de modeltekst staan en voeg geen KB-bronnen
  toe.
- Selector reject in Strict: eventueel fallback naar document-level trusted
  sources; anders refusal.
- Selector reject in Open: laat de modeltekst staan zonder KB-bronnen.
- Non-streaming responses krijgen `message.sources` plus een zichtbare
  `Bronnen` en `Agent activiteit` sectie.
- Streaming behoudt tokens waar mogelijk en appendt bronnen of refusal aan het
  einde. Bij Strict/no sources buffert de guard zodat een ongeciteerde modeltekst
  niet eerst uitlekt.

Bronnen:

- `deploy/litellm/klai_knowledge.py:2385-2501`
- `deploy/litellm/klai_knowledge.py:2710-2833`
- `deploy/litellm/klai_knowledge.py:2980-3018`
- `deploy/litellm/klai_knowledge.py:3021-3156`

LibreChat patcht streaming chunks zodat `sources` door de UI kunnen:

- Direct uit `chunk.sources` of `additional_kwargs/response_metadata`.
- Of uit de verborgen `<!-- klai_sources=... -->` marker die LiteLLM in tekst
  kan meesturen.

Bronnen:

- `deploy/librechat/getklai/patches/stream.cjs:598-633`
- `deploy/librechat/getklai/patches/stream.cjs:807-852`

## Web Search

LibreChat zet Web Search in de interface aan en definieert de providerconfig.
LiteLLM heeft daarnaast een `klai-web-search` search tool met SearXNG.

Bronnen:

- `deploy/librechat/librechat.yaml:17-20`
- `deploy/librechat/librechat.yaml:67-84`
- `deploy/librechat/librechat.yaml:108-114`
- `deploy/litellm/config.yaml:62-66`

De KB-hook detecteert Web Search op request metadata, `web_search_options`, of
tool namen/descriptions. Deze detectie wordt alleen gebruikt om in de general
branch een runtime capabilities block toe te voegen:

```text
Knowledge Base: none selected.
Web Search: available for this turn.
```

Met KB geselecteerd wordt de retrieval body niet aangepast op basis van web aan
of uit. De Open KB prompt zegt wel dat live lookup niet uit training data mag
worden beantwoord tenzij KB chunks of provided web results het ondersteunen.
Strict blijft KB-only; web aan maakt Strict niet open.

Bronnen:

- `deploy/litellm/klai_knowledge.py:614-660`
- `deploy/litellm/klai_chat_prompts.py:244-253`
- `deploy/litellm/klai_chat_prompts.py:263-310`

## Matrix

| Mode | KB geselecteerd | Web | Retrieval | Promptcontract | Citationgedrag |
| --- | --- | --- | --- | --- | --- |
| Open | Nee | Uit | Geen `/retrieve`. | General AI, algemene kennis, geen KB in scope. | Geen KB citations; geen `_klai_kb_meta` post-call rendering. |
| Open | Nee | Aan | Geen `/retrieve`. | General AI plus runtime block: Web Search beschikbaar voor live lookup. | Geen KB citations. Eventuele web sources lopen via tool/LibreChat, niet via EvidencePack. |
| Strict | Nee | Uit | Geen `/retrieve`. | Strict no-KB notice: niet uit algemene kennis antwoorden. | Geen KB citations; model moet weigeren op promptcontract. |
| Strict | Nee | Aan | Geen `/retrieve`. | Zelfde Strict no-KB notice. Web aan versoepelt Strict niet. | Geen KB citations. |
| Open | Ja | Uit | `/retrieve` met `kb_narrow=false`. Gate mag bypassen. | KB als aanvullende context; stabiele algemene fallback mag. | Bronnen alleen uit EvidencePack. Geen trusted sources betekent modelantwoord laten staan zonder KB-bronnen. |
| Open | Ja | Aan | Zelfde retrieval als Web uit. | Zelfde Open KB prompt; live lookup mag alleen met KB chunks of provided web results. | KB-bronnen alleen uit EvidencePack. Web sources zijn geen KB citations. |
| Strict | Ja | Uit | `/retrieve` met `kb_narrow=true`; gate wordt niet gebypasst. | Alleen geselecteerde KB-bronnen; niets uit algemene kennis. | Trusted EvidencePack sources worden gekoppeld. Geen citable sources of zero chunks leidt tot deterministische refusal/no-source gedrag. |
| Strict | Ja | Aan | Zelfde strict retrieval; gate blijft uit. | Strict blijft KB-only. Web aan maakt geen algemene fallback mogelijk. | Zelfde Strict citation guard. Web results tellen niet als KB EvidencePack sources. |

## Belangrijke edge cases

- Title generation en meta-vragen doen geen KB retrieval.
- Feature disabled, retrieval disabled, missing user mapping, of alle scopes uit
  zijn geen "zwakke retrieval" paden. Ze worden expliciet als General/Open of
  Strict refusal behandeld.
- Open met KB en zero chunks is bewust geen refusalpad. De prompt instrueert om
  te melden dat de KB niets vond en daarna algemeen te antwoorden als het topic
  dat toelaat.
- Strict met KB en zero chunks of geen citable sources wordt deterministic
  bewaakt door de post-call renderer, niet alleen door prompttekst.
- `kb_slugs_filter=null` betekent alle org-KBs. Alleen `[]` betekent geen
  org-KBs.
- `scope=personal` vertrouwt niet op client-slugs; retrieval-api forceert de
  canonieke persoonlijke KB-slug uit `user_id`.

## Bronbestanden

Belangrijkste codepaden:

- Portal UI: `klai-portal/frontend/src/routes/app/chat.tsx`
- Chat preferences UI: `klai-portal/frontend/src/routes/app/_components/ChatConfigBar.tsx`
- Portal preferences API: `klai-portal/backend/app/api/app_account.py`
- Portal internal feature API: `klai-portal/backend/app/api/internal.py`
- LiteLLM KB hook (entrypoint/orchestratie): `deploy/litellm/klai_knowledge.py`
- ↳ scopevertaling: `deploy/litellm/klai_kb_scope_policy.py` (`build_retrieve_body`, `resolve_kb_retrieval_scope`)
- ↳ query-rewrite + taxonomy classify: `deploy/litellm/klai_kb_query_rewrite.py`
- ↳ antwoordbeleid + `_klai_kb_meta`: `deploy/litellm/klai_kb_answer_policy.py` (`KbAnswerPolicy.to_kb_meta`)
- ↳ context-block + language reminder: `deploy/litellm/klai_kb_context_prompt.py`
- ↳ citation-rendering (Bronnen/Agent activiteit): `deploy/litellm/klai_kb_citation_render.py`
- ↳ chat-modes (general/open_kb/strict_kb/…): `deploy/litellm/klai_kb_chat_mode.py`
- Prompt constants: `deploy/litellm/klai_chat_prompts.py`
- LiteLLM config: `deploy/litellm/config.yaml`
- LibreChat config: `deploy/librechat/librechat.yaml`
- LibreChat stream source patch: `deploy/librechat/getklai/patches/stream.cjs`
- Retrieval endpoint (handler op `:82`): `klai-retrieval-api/retrieval_api/api/retrieve.py`
- ↳ rerank / link-expand / quality-floor: `klai-retrieval-api/retrieval_api/api/ranking.py`
- ↳ authority / page-context boost: `klai-retrieval-api/retrieval_api/api/page_context.py`
- Retrieval request/response models: `klai-retrieval-api/retrieval_api/models.py`
- Qdrant filters/search: `klai-retrieval-api/retrieval_api/services/search.py`
- EvidencePack builder: `klai-retrieval-api/retrieval_api/services/evidence_pack.py`
- Shared citation engine: `klai-libs/citations/klai_citations/__init__.py`

