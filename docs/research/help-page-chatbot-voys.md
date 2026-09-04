# Klai als chatbot op de Nederlandse helppagina's — research Voys-pilot

> Last updated: 2026-09-03
> Status: Active research — basis voor development-verbeteringen (chat-/widget-infrastructuur)
> Herkomst: samengevoegd uit twee onafhankelijke onderzoeksrondes op dezelfde
> opdracht — Qwen 3.8 Flash-Next (structuur, code-inventaris, extern onderzoek)
> en Claude Opus 5 (gap-loop, capaciteitsanalyse, marktcijfers). Waar de twee
> elkaar tegenspraken is het verschil nagerekend tegen de bron; zie § 5.4.
> Uitvoering: dit document is de onderzoeksbasis, niet de werkvoorraad. Wat we
> daadwerkelijk bouwen — requirements, beslissingen, omkeringen, verificatie —
> staat in `docs/specs/SPEC-VOYS-HELPBOT-001/spec.md`. Werk dat plan daar bij,
> niet hier, anders lopen de twee uiteen. De gatentabel in § 8 draagt sinds
> 2026-09-04 een statuskolom die naar de bijbehorende requirement verwijst.
> Onderzoeksmethode: alles hieronder volgt broncode op deze checkout (`main` @ `e3fb3de4a`), repo-docs en externe bronnen die op 2026-09-03 zijn opgehaald. Interne claims dragen `bestand:regel`; externe claims een URL. Wat niet geverifieerd is, staat expliciet bij § 10.

---

## 0. Vraag en TL;DR

**Vraag:** kunnen we Klai als chatbot op de Nederlandse helppagina's van Voys (tenant `voys`) zetten, en zo ja: vraagt dat om andere instructies en een andere setup dan nu, en hoe staat onze chat-infrastructuur in vergelijking met onze data-infrastructuur?

**Kernconclusies:**

1. **De datalaag is inderdaad de sterkste kant — en die geldt ook voor de widget.** De widget loopt tegen dezelfde retrieval-api `/retrieve` aan met dezelfde pipeline (hybrid search, rerank, quality floor, EvidencePack-citatiecontract) als de app-chat. Zie § 5.1.
2. **De chatlaag eromheen is voor de widget dunner dan voor de app.** Er bestaan twee parallelle chat-paden; het widget-pad (path B) mist vier answer-policy-lagen die op het app-pad (path A) juist díe kwaliteitswinsten opleverden die uit Voys-incidents zijn geboren (query-rewrite met brand-bridging, low-confidence injectie, multi-question guard, distillatie van geplakte correspondentie). Zie § 5.2. Dat bevestigt de onderbuik: de chat-infrastructuur kan beter.
3. **De prompts zijn bewust voor een ander oppervlak geschreven.** De shared prompt-library documenteert letterlijk dat Klai "an internal-team tool, not a customer-support surface" is (`klai-libs/chat-prompts/klai_chat_prompts/__init__.py:63-70`). Een helpdesk-bot vraagt om een ander persona, andere "niet gevonden"-zinnen ("kennisbank" zegt een websitebezoeker niets), escalatie-instructies en — sinds 2 augustus 2026 — een AI-openbaarmakingsplicht uit de EU AI Act. Zie § 7 en § 6.4.
4. **De widget-stack is verder dan verwacht:** anonieme auth (HKDF per tenant), origin-allowlisting, rate limits, audit + retention, NL/EN labels, streaming met bronnen + agent-activiteit, HubSpot-handoff, admin-UI met 7 tabs, stats-endpoint en een 200 kB bundle-budget bestaan al en zijn getest. De pilot is dus vooral **configureren + instructie-ontwerp**, niet bouwen. Zie § 2.
5. **Eén harde blocker voor de Voys-pilot:** de HubSpot-handoff is vastgezet op tenant `getklai` en origin `getklai.getklai.com` (`klai-portal/backend/app/api/partner.py:106-107, 2485-2501`). Een Voys-eigen handoff naar Voys-support bestaat niet; de pilot start zonder handoff, of die gating wordt multi-tenant gemaakt. Zie § 8, G-1.
6. **De feedback-loop ontbreekt op de widget zelf.** Er is géén thumbs-up/down in de widget-UI, géén feedback-kolom op `widget_messages`, en `/partner/v1/feedback` is een partner-API-key-endpoint — niet gekoppeld aan widget-sessies. Voor een publieke helpdesk is dit dé bron van waarheid over wat de bot niet kan. Zie § 8, G-3.
7. **De rol van Voys als testtenant is historisch sterk:** de 41-NL-query evalsuite staat op de Voys-support-KB (`deploy/litellm/eval_suites/chat.yaml:3-13`), en alle incidenten die de retrieval- en answer-policy-laag gescherpt hebben, dragen "Voys" in de commentaren (§ 4). De helpdesk-pilot sluit aan op bestaand, beproefd werk.
8. **Aanbevolen route** (uitvoering en actuele status: `docs/specs/SPEC-VOYS-HELPBOT-001/spec.md`)**:** Fase 0 = no-code pilot op help.voys.nl (widget + NL helpdesk-instructies, § 9.1) met wekelijkse transcript-review; Fase 1 = vijf gerichte dev-steps (refusal-herstyling naar customer-tone, confidence-band-portability, thumbs + opslag, AI Act-disclosure, evalsuite uitbreiden naar path B); Fase 2 = één orchestratiepad (parallelle implementaties samenvoegen).

---

## 1. Hoe het chatverkeer nu loopt: twee paden

### Path A — de app-chat (LibreChat → LiteLLM-hook)

`/app/chat` in de portal laadt een tenant-specifieke LibreChat in een iframe; LibreChat praat tegen de LiteLLM-proxy, en daar draait de `klai_knowledge`-pre-call-hook die retrieval, prompt-injectie en citatie-guards verzorgt.

- Flow, state-contracten en de Open/Strict-matrix: [`docs/architecture/regular-chat-knowledge-retrieval-citations.md`](../architecture/regular-chat-knowledge-retrieval-citations.md) (let op de header-notitie: regelverwijzingen in dat document zijn verouderd sinds de opsplitsing van `klai_knowledge.py` op 2026-06-07; de logica woont nu in `klai_kb_scope_policy.py`, `klai_kb_query_rewrite.py`, `klai_kb_answer_policy.py`, `klai_kb_context_prompt.py`, `klai_kb_citation_render.py`, `klai_kb_chat_mode.py`).
- Hook-volgorde: eerst `klai_knowledge.klai_knowledge_hook`, dan `custom_router.token_router` (arch-doc § "Reguliere chat entrypoint").

### Path B — de widget / partner-API (portal → retrieval-api → LiteLLM pass-through)

De widget (`klai-widget`) roept `POST /partner/v1/chat/completions` (`klai-portal/backend/app/api/partner.py:1533, 1570`). Die handler:

1. valideert model + berichten en draait een safety-scan (`partner.py:1585-1587`),
2. vertaalt widget-KB-rechten naar `kb_slugs` (`partner.py:1611-1640`),
3. roept **zelf** `/retrieve` aan op de retrieval-api via `retrieve_context()` (`klai-portal/backend/app/services/partner_chat.py:1821-1990`, request-body `:1885-1896`),
4. bouwt het system prompt in `_build_system_prompt()` (`partner_chat.py:1751-1806`) — GROUNDED-prompt + widget-instructies + instruction-hierarchy/safety-block + URL-guard,
5. stuurt dat naar LiteLLM `/v1/chat/completions` met de **general-chat key** en een expliciete `_klai_openai_passthrough`-metadatavlag (`partner_chat.py:1306, 1400`), zodat de KB-hook van path A dit request overslaat,
6. laat de citatie-renderer na generatie trusted sources uit de EvidencePack toevoegen, inclusief deterministische refusal als er geen citeerbare bronnen zijn (`partner_chat.py:1521-1563`, `_no_citable_sources_message`).

Modelkeuze widget: `klai-primary` (default in schema `partner.py:181`; toegestaan `klai-primary`/`klai-fast`, `partner.py:84`); `klai-primary` rout op Mistral Small (`deploy/litellm/config.yaml:12`), met fallback naar `klai-medium` (`config.yaml:114`).

> **Bijzonderheid:** dit pad staat in geen enkel architectuurdocument. De arch-doc hierboven beschrijft uitsluitend path A. Path B is een parallelle implementatie — de docstring van `retrieve_context` zegt het zelf: *"Follows the pattern from deploy/litellm/klai_knowledge.py"* (`partner_chat.py:1838`).

---

## 2. De widget-stack vandaag (inventaris)

### 2.1 Frontend (`klai-widget/`, SolidJS, single-file IIFE-bundle)

| Feature | Bewijs |
|---|---|
| Embed via `<script src="…/widget/n" data-widget-id>` + bubble (Shadow DOM) óf inline-mode | `klai-widget/src/main.ts:34-122`; `klai-portal/frontend/src/routes/widget-test.tsx` |
| Bundle-budget 200 kB gzip, CI-check | `klai-widget/scripts/check-bundle-size.mjs:27` |
| SSE-streaming met content-delta's + `sources` + `agent-activity` events | `klai-widget/src/api/chat-stream.ts:224-329` |
| Pagina-context-verzamelaar (title, path, ≤2000 chars excerpt van `<main>`) — opt-in | `chat-stream.ts:72-141` |
| NL/EN labels, default NL; locale uit `data-locale`, widget-copy of `document.lang` | `src/i18n/labels.ts:1-133` |
| Disclaimer onderaan: "AI-antwoorden kunnen fouten bevatten…" | `labels.ts` (`disclaimer`) |
| Handoff: "Praat met een medewerker" → HubSpot, inclusief transcript-summary, naam/e-mail, "onthoud mijn gegevens (30 dagen)" | `src/api/handoff.ts`; `labels.ts` |
| Gespreksgeschiedenis lokaal (localStorage), sessietoken alleen in-memory | `src/store/chat.ts:19-36, 150-172` |
| Token-refresh bij 401, exact één keer | `chat-stream.ts:331-361` |
| WCAG-conforme shipped defaults (recent) | git `50f8d8007` "fix(widget): the shipped defaults now meet WCAG…" |
| **Geen** thumbs-up/down of feedback-UI | grep `feedback|thumb` in `klai-widget/src` → alleen CSS-scrollbar |

### 2.2 Backend (`/partner/v1` + admin)

| Feature | Bewijs |
|---|---|
| Publiek `/widget-config`: sessie-JWT (HS256, 1 u) met `wgt_id/org_id/kb_ids`, HKDF per-tenant sleutel uit master-secret + tenant-slug (`"voys"` als letterlijk voorbeeld in de docstring) | `app/api/partner.py:2510+`; `app/services/widget_auth.py:35, 82-105, 143` |
| Origin-vertrouwen: default-deny lijst, wildcard-subdomeinen, `allow_any_origin` als expliciete opt-in; UX-gate, security-boundary is het JWT | `widget_auth.py:196-266`; `partner.py` REQ-23 docstring |
| Rate-limits: 10 mints/min per widget bij config-mint, `rate_limit_rpm` default 60 per widget | `partner.py:2712-2724`; `models/widgets.py:75`; tests `test_widget_mint_rate_limit.py` |
| Audit: `widget_conversations` (first_user_query, language_detected, loaded_origin, is_preview) + `widget_messages` met `sources` JSONB; dagelijkse retention-worker | `models/widgets.py:101-174`; `services/widget_messages_retention.py` |
| Admin-UI: 7 tabs (Details, Appearance, Embed, KnowledgeBases, Integrations, Activity, Danger), preview-sessie, conversatie-browser, `/stats` (gesprekken, berichten, top-queries, hourly) | `routes/admin/widgets/_components/tabs/`; `admin_widgets.py:804-811` |
| Widget-instructies: vrij tekstveld `system_prompt` (max 4000) + herbruikbare Template via `template_slug`, samengevoegd ter runtime | `admin_widgets.py:76, 89`; `partner.py:335-374` |
| HubSpot-handoff-integratie (connect/disconnect/rebuild/test-message) — **pilot, vastgezet op één tenant/origin** | `admin_widgets.py:527-636`; `partner.py:106-107, 2117-2149, 2485-2501` |
| Publieke share-URL `/bot/{widget_id}` (hosted bot-pagina, zelfde JWT) | `partner.py:2688-2700`; `routes/bot/$widgetId.tsx` |
| Platform-gate: widgets zijn een per-tenant "platform unlock"; soft-deleted → 404; existence-non-disclosure | `partner.py` REQ-1/REQ-16 comments; `post_deploy_h1i2j3k4l5m6_…sql:22` ("Voys … heeft noch widgets noch custom MCPs geconfigureerd" — stand van die datum) |
| Veiligheid: instructie-hierarchy/safety-block onderaan elke prompt; page-context en opgehaalde chunks gaan door een safety-filter; web-search expliciet uitgesloten voor publieke widget-keys | `partner_chat.py:1771-1779, 1863, 1966-1978`; `partner.py:187-192` |
| Citatie-rescue op path B met dezelfde drempels als path A, **handmatig gesynchroniseerd** | `deploy/docker-compose.yml:966-971` ("Mirrors the litellm block (path A); keep the two in sync") |

Deze stack is breed getest: ~20 widget-testbestanden in `klai-portal/backend/tests/` (JWT-per-tenant, origin-default-deny, mint-rate-limit, retention, soft-delete, handoff-gating, platform-unlock).

### 2.3 Hosts

- Widget-script en API in prod: `widget.getklai.com` / `api.getklai.com` (`deploy/docker-compose.yml:934-935`; default `WIDGET_CONFIG_BASE_URL` in `handoff.ts:7-9`).

---

## 3. De helpcentrum-oppervlakken

Er zijn **twee** plekken waar "hulppagina's" in het geding zijn:

1. **help.voys.nl (extern, vandaag live)** — een Super.so/Next.js-site met Notion-content, `lang="nl"`, geïndexeerd door zoekmachines. Bron: live-fetch van de ruwe HTML, vandaag. (Let op: een samenvattende page-fetch meldde hier "geen chatwidget aanwezig"; dat is onjuist — de embed staat alleen in de ruwe HTML, niet in de zichtbare tekst. Verifieer dit soort claims op de HTML zelf.) Daarin is al een **Nerds helpdesk/booking-widget** ingebed (`https://helpdesk.nerds.nl/embed.js` + `cdn.nerds.nl/embed/voys/booking.css`) — dat is het "bel-icoontje rechtsonder" waar de pagina `/administratie/verhuizen` zelf naar verwijst. Gevolgen voor de Klai-widget: script-embed kan via Super's custom code, maar er botst een tweede bubble rechtsonder (placering/visibility afstemmen), en de Klai-contentbron voor deze pagina's is de **crawl van help.voys.nl** (die in de Voys-KB zit; zie `chat.yaml:365` dat `help.voys.nl` als bron noemt).
2. **klai-docs (Klai-eigen helpcenter-product, per tenant)** — Next.js-reader op `{org}.getklai.com` (subdomain→org via `klai-docs/middleware.ts:1-36`), content in Gitea (`org-voys/help-center`, `docs.voys.nl` als custom-domain-plaatshouder: `klai-docs/migrations/001_docs_schema.sql:10-33`), ingest in de KB via `klai-docs/lib/knowledge_ingest.py`. **Er is nog géén chat-widget ingebed op de reader-pagina's** (grep op `widget` in `klai-docs/`: alleen middleware + arch-doc). De editor leeft in de portal (`/app/docs/`), zie `klai-docs/docs/architecture.md`.

Beide kandidaten: de pilot op **help.voys.nl** test tegen de echte, live publieke content maar met een externe scraping-source-of-truth; de pilot op **klai-docs** test het eigen product maar met content die nog niet gelijkgetrokken is met de live hulppagina's. Zie § 9.

---

## 4. Voys als tenant — de historie is de testcase

Voys is de reference tenant van het hele kennisplatform:

- **Chat-app voor CS:** `chat-voys.getklai.com` is de productiescene van het Voys-Salesforce-hallucinatie-incident (2026-05-07) dat SPEC-RAG-LOW-CONFIDENCE-ABSTAIN-001 opleverde (`docs/knowledge-retrieval-low-confidence-abstain-2026-05-08.md:26-35` — rerank top-1 0.18 → 0.96 na de fix).
- **Evalsuite op hun support-KB:** 41 NL-queries, "stijl mirror een Voys CS-medewerker", org `368884765035593759` (`deploy/litellm/eval_suites/chat.yaml:3-13`), inclusief negatieve canaries die hallucinaties moeten vangen (`chat.yaml:355-366`).
- **Incident-gedreven lagen,** allemaal met Voys in de commentaren: "Meldingen"-confabulatie → anti-confabulatie-paragraaf in de prompts (`klai-libs/chat-prompts/__init__.py:48-50`); webhook-FAQ (11 vragen, 1 bron, 11 zelfverzekerde antwoorden) → multi-question guard (`deploy/litellm/klai_kb_confidence_policy.py:50-53`); geplakte correspondentie → distillatie-block in query-rewrite (`klai_kb_query_rewrite.py:88-92`); streaming-replay ("Voys feedback #21") → citatie-render-fix (`klai_kb_citation_render.py:1029, 1318`); Voys/Ascend KB-manager-incident (`klai-portal/backend/app/services/access.py:301`); de HKDF-slug in `widget_auth.py:87` heet in de docstring "voys".
- **Handoff-pilot:** de HubSpot-custom-channel-ontvanger heet in de code "Voys support" (`klai-portal/backend/app/services/hubspot_custom_channel.py:195, 240`) — de pilot is dus historisch wél voor deze flow bedoeld, maar technisch vastgezet op tenant `getklai` (zie § 8, G-1).

Conclusie voor de pilot: de data-infrastructuur voor Voys-content is niet theoretisch goed, ze is **operationeel beproefd** — met een eigen NL-evalset.

---

## 5. Oordeel: data-infrastructuur vs chat-infrastructuur

### 5.1 Wat de widget vandaag erfde van de RAG-investeringen (de "superieur"-hypothese klopt)

De `/retrieve`-pijplijn is gedeeld (arch-doc § Retrieval-api): coreference-rewrite, dense+sparse+questions via RRF, optioneel Graphiti, link-expansion, authority/page-context-boost, rerank, quality floor, source-aware selection, feedback-boost, parent-text, **confidence-band** en `EvidencePack` als deterministisch citatiecontract. Ook de widget krijgt server-side Qdrant-filters op `org_id`+`kb_slugs` en backend-managed citations ("model mag zelf geen bronnen/URLs/numerics schrijven": `partner_chat.py:1787-1794`). De citatie-rescue-drempels zijn handmatig gelijkgetrokken tussen beide paden (`docker-compose.yml:966-971`).

### 5.2 Wat pad B mist ten opzichte van pad A

| Laag (geboren uit Voys-incidents) | Path A (hook) | Path B (widget) | Bewijs |
|---|---|---|---|
| Taxonomy/query-rewrite met **brand-bridging** (Voys↔Bubble/RedCactus, "SIP 404"-categorietalen) | ✅ apart LLM-rewrite + taxonomy-classify | ❌ alleen de coreference-rewrite binnen retrieval-api | `klai_kb_query_rewrite.py:120-140` vs afwezig in `partner_chat.py` |
| **Distillatie geplakte correspondentie** (email/ticket in query) | ✅ | ❌ | `klai_kb_query_rewrite.py:83-110` |
| **Low-confidence injectie** ("presenteer als algemene kennis / niet verzin") | ✅ beleid via `klai_kb_confidence_policy.py` | ❌ `confidence_band` wordt genegeerd in `retrieve_context` (`partner_chat.py` leest alleen `evidence_pack`; band gedefinieerd in `retrieval_api/models.py:197`) | — |
| **Multi-question guard / fan-out** (per vraag wegen) | ✅ | ❌ (base-prompt heeft wél een multi-part paragraaf: `klai_chat_prompts/__init__.py:240-252`, maar zonder retrieval-fan-out) | `klai_kb_confidence_policy.py:55-75` |
| **Strict/KB-only-modus** (`kb_narrow`) | ✅ | ❌ — veld default `False`, niet in body | `retrieval_api/models.py:55`; body `partner_chat.py:1885-1896` |
| Streaming-buffer bij refusal (tekst lekt niet vóór de refusal) | ✅ gebufferd | deels — path B heeft marker-mode buffering (`partner_chat.py:2181-2186`) maar niet de strict-no-buffer-logica van A | arch-doc § post-call |
| Meta-query vroege-return, title-gen-skip | ✅ | n.v.t. (widget heeft die modes niet) | arch-doc § pre-call flow |
| Taaldetectie voor refusal-tekst | ✅ | ✅ — gedeelde lib, NL-marker (`klai_chat_prompts/__init__.py:100-136`) | — |
| Deterministische refusal zonder bronnen | ✅ | ✅ (`partner_chat.py:1537, 1563`) | — |
| Safety-filter input/page-context/chunks | ✅ (`klai_kb_llm_safety`) | ✅ (`context_safety_violation`, `partner_chat.py:1863, 1966-1978`) | — |

**Dit is de "chat-infrastructuur kan beter"-scoop, mechanisch uitgelegd:** de investeringen in retrieval (gedeeld) zijn er wél; de investeringen in *antwoord-beleid* (pad-afhankelijk) zijn er grotendeels niet. En de evalueerbare kwaliteitswinsten (de Voys-evalsuite + live-evalscripts) draaien op de LiteLLM-proxy/pad A (`deploy/litellm/scripts/eval_pii_restore_live.py:117` raakt `:4000/v1/chat/completions`); er is in de repo géén equivalent eval-harness voor path B aangetroffen.

### 5.4 Waarom pad B die lagen mist — nagerekend

Twee onderzoeksrondes gaven hier twee verschillende verklaringen; beide bleken
onjuist. De werkelijke reden is de derde:

- *Niet* de `_klai_openai_passthrough`-vlag. Die wordt uitsluitend gezet in
  `_with_openai_passthrough_metadata` (`partner_chat.py:1284-1304`), dat alleen
  door het general-passthrough-pad (`openai_chat_completion_*`) wordt gebruikt.
  De kennis-functies `chat_completion_streaming` / `chat_completion_non_streaming`
  zetten hem niet — geverifieerd door de functie-bodies af te zoeken.
- *Niet* de LibreChat-gebruikerscheck (`klai_knowledge.py:508`). De hook komt
  daar niet eens aan toe.
- **Wel: het kennis-pad praat met LiteLLM via de master key.** De request-body is
  `{model, messages, temperature, stream}` met
  `Authorization: Bearer {settings.litellm_master_key}` en zonder `metadata` en
  zonder `user` (`partner_chat.py:2041-2050`, idem `:1634`, `:2285`). De hook
  leest `org_id` uit de metadata van de LiteLLM *key*; bij de master key is die
  leeg en dan geldt: `if not org_id: # Master key usage — no org scope
  available, skip silently; return data` (`klai_knowledge.py:495-499`).

Dat is geen vlag die je omzet. Zolang het widget-pad met de master key praat,
kan de hook er per definitie geen org-gescopeerd beleid op toepassen — en die
key is nodig omdat portal-api hier server-to-server namens een anonieme
bezoeker handelt, zonder tenant-key-context. De remediatie is dus structureel:
til de answer-policy uit de hook naar een shared lib die zowel de hook als
`partner_chat` aanroept (§ 9.3, G-7), of geef `partner_chat` een org-gescopeerde
LiteLLM-key. Een quick fix bestaat hier niet.

### 5.5 Gap-registratie: het widget-pad voedt de zelflerende lus niet

Klai heeft een werkende kennisgat-registratie: `app/api/app_gaps.py` biedt
`/api/app/gaps`, `/gaps/summary` en `/gaps/by-taxonomy` (open gaten per
taxonomie-knoop met een frequentie-gebaseerde prioriteit), met een
resolved-lifecycle op `PortalRetrievalGap`. Gap-events worden gevuurd via
`klai-libs/retrieval-telemetry` (`fire_n` → portal `/internal/v1/gap-events`),
en de enige aanroeper is de LiteLLM-hook (`klai_knowledge.py:1412`,
`gap_type = _classify_gap(chunks)`). `partner_chat.py` bevat het woord "gap"
niet.

Gevolg voor de pilot: het dashboard dat moet vertellen welke helpartikelen
ontbreken, ziet straks alleen interne chat en niet de vragen van echte
klanten — precies de doelgroep waarvoor je het wilt. Dit is de goedkoopste
hoge-waarde-ingreep in dit document: de infrastructuur (classificatie,
taxonomie-prioritering, dashboard, lifecycle) staat er al; er moet één aanroep
bij op pad B, met een `caller_client_id` die widget-verkeer onderscheidt van
app-verkeer.

### 5.6 Capaciteit: de rate limits zijn per widget, niet per bezoeker

De limieten uit § 2.2 zijn geen detail maar een plafond voor publiek verkeer:

- Chat: `_SESSION_RATE_LIMIT_RPM = 60`, **hardcoded** in
  `partner_dependencies.py:210` en gesleuteld op `wgt_id` — dus 60 requests per
  minuut gedeeld door alle bezoekers samen. De admin-instelbare
  `rate_limit_rpm` (10-600, `admin_widgets.py:108`) geldt voor
  `pk_live_`-partnerkeys, niet voor widget-JWT's.
- Sessie-mint: 10 per minuut per widget (`partner.py:2577`), en de widget mint
  bij elke pagina-load opnieuw (`klai-widget/src/main.ts`, `bootstrap`); het
  token staat bewust alleen in het geheugen en `/widget-config` stuurt geen
  `Cache-Control`.

Voor een helpcentrum met echt verkeer is dit de eerste muur, nog voor
antwoordkwaliteit een rol speelt. Er is geen per-bezoeker-dimensie, dus de
limiet is ook een gedeeld-lot-mechanisme. Behandel dit als Fase 0-werk.

### 5.3 Drift-risico

De parallelle implementaties worden bewust synchroon gehouden via comments ("Mirrors …", "keep the two in sync"), en drift-tests (`test_klai_retrieval_telemetry_drift.py`, `test_chat_yaml_eval_suite_drift.py`) dekken alleen telemetry/eval-content, niet de beleidslogica. Hoe langer A en B apart leven, hoe waarschijnlijker het scenario: *"in de app antwoordt ie goed, in de widget verzint ie iets"*.

---

## 6. State of the art: hoe bouw je zoiets goed (extern onderzoek)

### 6.1 De standaard-loop en het instructiemodel (Intercom Fin, marktleider)

Fin's kernloop: content-ingest → per vraag relevante passage ophalen → grounded antwoord → **escaleren bij lage confidentie** (derde-partij-samenvattingen: [getmacha.com](https://www.getmacha.com/blog/intercom-fin-ai-agent-complete-guide), [octopods.io](https://blog.octopods.io/intercom-fin-guide/)). Onderscheidend detail zit in het instructiebeheer ([Intercom-docs "Provide Fin AI Agent with specific guidance"](https://www.intercom.com/help/en/articles/10210126-provide-fin-ai-agent-with-specific-guidance), opgehaald 2026-09-03):

- **Gestructureerde guidance in categorieën:** Communication style · Context and clarification (doorvragen bij onduidelijke vraag) · Content and sources (per vraagtype naar specifieke artikelen verwijzen, via `@bron`) · Spam · Other. Max 100 live × 2500 chars — gericht, geen muur van tekst.
- **Kanaal- en publieksscheiding:** per guidance een kanaalkeuze (Chat/Email/Voice) en audience-targeting; instructies zijn context-sensief, niet globaal.
- **Escalatie als apart regime:** dedicated Escalation-tab met *deterministische rules* (data: sentiment, plan, "agent"-keyword) én *NL-escalation-guidance* ("als een klant 'refund' noemt…"); "escaleer direct in plaats van bied aan" is configureerbaar.
- **Testen-vóór-live:** preview-paneel en "Preview user" om guidance vóór enablen te testen; een AI-schrijfassistent checkt guidance op dubbelzinnigheid, redundantie, tegenstrijdigheid en systeem-limitaties.
- **Gebruiksstatistiek per guidance + versiegeschiedenis met rollback** — instructies worden gemeten zoals code.

Vergelijk daar de Klai-widget-instructies mee: één vrij veld van 4000 chars + optioneel Template, geen per-vraagtype-contentrouting, geen kanaal/doelgroep-scheiding, geen versiebeheer, geen usage-meting (`admin_widgets.py:76`; `partner.py:335-374`).

### 6.2 KPI's: wat je moet meten en waar het faalt

- **Containment** = AI-gesprekken zonder follow-up op hetzelfde issue (formule: [Rasa](https://rasa.com/blog/measure-ai-agent-performance-in-the-contact-center)); let op de doom-loop: containment die "niet-escaleren" meet maar niet "oplossen" ([digitalapplied](https://www.digitalapplied.com/blog/ai-customer-support-metrics-deflection-csat-framework-2026)).
- **Deflection vs containment vs resolution**: resolution is de strengste, eerlijkste noemer; containment de losste — kies expliciet welke je rapporteert en met welke noemer ([Owlish](https://owlish.bot/blog/resolution-rate-vs-deflection-rate/), [Decagon](https://decagon.ai/glossary/what-is-chatbot-containment-rate)).
- **CSAT via thumbs op gesprekseinde**, en meet deflection ook met A/B op pagina's met/zonder widget ([heeya](https://heeya.fr/en/blog/ai-chatbot-kpis-metrics-guide-2026)).
- **Meetmethode van de serieuze spelers: LLM-as-judge over 100% van de gesprekken.** CSAT-enquetes halen 2-8% respons, dus een steekproef vertelt je weinig; Zendesk laat een LLM achteraf verifieren of een als-opgelost gemarkeerd gesprek dat werkelijk was, juist om opgeblazen cijfers te voorkomen ([Zendesk](https://www.zendesk.com/blog/ai/productivity/ai-resolution-rate/), [eesel](https://www.eesel.ai/blog/zendesk-ai-agent-metrics-resolution-rate)).
- **Richtcijfers 2026** (leveranciersclaims, niet onafhankelijk gemeten): ~tweederde resolutie als mediaan, 70-75% een sterke deployment, 80%+ best-in-class op een goed gestructureerde intent-mix. Fin claimt 76% gemiddeld; Sierra ~70% bij WeightWatchers met 4,6/5 CSAT; Decagon bracht Rippling van 38% naar 50%+ ([Fin](https://fin.ai/learn/ai-resolution-rate), [Lorikeet](https://www.lorikeetcx.ai/articles/resolution-rate-ai-customer-support-benchmarks-2026)).
- **De kloof tussen deflectie en resolutie is 20-30 procentpunt** — wie afhaakt telt mee als deflectie maar niet als resolutie. Sturen op deflectie optimaliseert dus richting "bezoeker geeft het op" ([Fin](https://fin.ai/learn/resolution-rate-vs-deflection-rate)).
- Voor documentatie-assistenten geldt daarnaast: onzekerheidspercentage onder 10% bij goed onderhouden content, en tijd tot eerste token onder 3 seconden ([kapa.ai](https://www.kapa.ai/blog/top-5-ai-documentation-chatbots-2026)).
- Klai heeft vandaag alleen volume-metrics (conversaties, berichten, top-queries, hourly — `admin_widgets.py:804-811`). Er is geen outcome-label (resolved/escalated/abandoned) op `widget_conversations`.

### 6.3 Guardrails: indirecte prompt-injectie en RAG-poisoning

OWASP LLM Top 10 (2025) maakt van prompt-injectie risicoklasse **LLM01**, met expliciete indirecte-vector: instructies verstopt in opgehaalde content (documenten, gecrawde webpagina's, tickets) die het model als commando behandelt ([Oligo](https://www.oligo.security/academy/owasp-top-10-llm-updated-2025-examples-and-mitigation-strategies), [BSG](https://bsg.tech/blog/owasp-llm-top-10/)); RAG-poisoning van de vector-store is een erkende aanvalsweg ([promptfoo](https://www.promptfoo.dev/docs/red-team/owasp-llm-top-10/)). Relevant voor onze setup:

- De widget stuurt een **page-context excerpt van de helppagina zelf mee in de prompt** (`chat-stream.ts:72-102`, `partner_chat.py:1861-1869`) — elke content-manipulatie op de host-pagina kan als injectiedoel dienen. Mitigations die we al hebben: instructie-hierarchy-block onderaan (`partner_chat.py:1771-1779`), safety-filter op page-context én chunks (`:1863, 1966-1978`). Dit past bij onze fail-loud-cultuur; de KB-crawl van help.voys.nl is zelf een aanvalsoppervlak (ingest-gate/quality-floor aanwezig, extra review van pagina-updates niet gevonden).

### 6.4 EU AI Act — openbaarmakingsplicht (actueel sinds 2026-08-02)

Artikel 50(1): AI-systemen die rechtstreeks met mensen interageren moeten zodanig ontworpen zijn dat die persoon **op de hoogte is dat hij met een AI-systeem praat**, "tenzij dit voor een redelijk goed geïnformeerde, attente persoon duidelijk is"; 50(5): die informatie moet **duidelijk en onderscheidbaar bij de eerste interactie** gegeven worden en aan toegankelijkheidseisen voldoen. Inwerkingtreding: 2 augustus 2026 ([artificialintelligenceact.eu/article/50](https://www.artificialintelligenceact.eu/article/50/), opgehaald vandaag). De Klai-widget toont nu een disclaimer over nauwkeurigheid en heet "Klai AI" in de bubble — dat raakt de "tenzij duidelijk"-toets, maar expliciete disclosure bij de eerste interactie (welkomstregel als "Vraag het onze AI-assistent") is de goedkope, veilige zet. Juridische kwalificatie voorbehouden aan legal; dit is een signaal, geen advies.

### 6.5 Architectuurreferentie (Microsoft Foundry baseline, jun 2026)

De Azure-referentie-architectuur voor enterprise chat onderschrijft wat wij op pad A al deden én geeft drie bevestigingen voor de inhaalbeweging op path B: (a) gesprekstoestand + tooloproepen **server-side persisteren in een eigen store** (onze widget: client beheert history, server schrijft alleen audit — werkbaar maar zie § 9.3); (b) **agentdefinities "as code"** in source control met rollback, in plaats van alleen vrije DB-tekstvelden; (c) isolatie van de chat-workload (dedicated knowledge store, eigen state) ([learn.microsoft.com — Baseline Microsoft Foundry Chat](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/architecture/baseline-microsoft-foundry-chat), opgehaald vandaag).

### 6.6 Embed-mechanica en prestaties

- web.dev-richtlijnen voor derde-partij-embeds: lazily laden, layout-shift beperken ([web.dev/articles/embed-best-practices](https://web.dev/articles/embed-best-practices)); onze widget bootstrapt async en blokkeert de hostpagina niet (`main.ts:125-127`) — ok.
- Shadow DOM vs iframe: practitioner-bronnen zijn unaniem dat Shadow DOM styling-isolatie geeft maar **geen** security-isolatie; iframe is sterker maar zwaarder/moeilijker te thema'en ([stackoverflow](https://stackoverflow.com/questions/49220533/how-are-javascript-widgets-made-without-iframes), [r/webdev](https://www.reddit.com/r/webdev/comments/1hb0kw6/preferred_method_for_creating_3rd_party/)). De Klai-keuze is bewust Shadow DOM + JWT-scoping van de API — verdedigbaar zolang bundle-budget en XSS-sanitering (dompurify aanwezig, `package.json`) houdbaar blijven op een vreemde host als help.voys.nl.

### 6.7 Nederlands als taal van het oppervlak

Nederlandstalige LLM-kwaliteit is dun gemeten: de meeste benchmarks zijn vertalingen met slechte lokale kalibratie (onderbouwing in de Fietje-paper, [arXiv:2412.15450](https://arxiv.org/html/2412.15450v1)); EuroEval meet systematisch per taal ([overzichtsdiscussie, r/thenetherlands](https://www.reddit.com/r/thenetherlands/comments/1moyqac/prestaties_van_grote_taalmodellen_llms_in_het/)) en Artificial Analysis heeft een multilingual-index ([artificialanalysis.ai/models/multilingual](https://artificialanalysis.ai/models/multilingual)). Praktijkconsequentie: de keuze voor Mistral Small/Medium als `klai-primary` is EU-hosted en taaltechnisch plausibel, maar **toets dat op de eigen 41-query NL-evalsuite**: meet Dutch-output-quality op Voys-content (bedrijfstijl "je"/"jullie", productnamen Bubble/Freedom/RedCactus), niet op een generieke benchmark. Dat sluit aan op het meetprotocol dat dit research-programma zelf aanbeveelt (RAGAS + Wilcoxon, zie [README.md](README.md)).

---

## 7. De instructievraag: internal persona vs helpdesk persona

De shared prompts zijn bewust niet-helpdesk-geschreven. Verschillen die er voor een hulppagina-toepassing écht toe doen:

| Dimensie | Nu (shared lib / path A-dna) | Helpdesk-widget nodig |
|---|---|---|
| Toon | "senior colleague… No filler, emoji, exclamation marks, or closing pleasantries" (`klai_chat_prompts/__init__.py:222-229`) | klantvriendelijk maar niet zoetsappig; emoji-stijl is merkkeuze — Fin laat dit per "Communication style" guidance configureren toe ([bron](https://www.intercom.com/help/en/articles/10210126-provide-fin-ai-agent-with-specific-guidance)) |
| Afwezig antwoord | "Dat staat niet in de kennisbank" (`:234-239`, refusal `:134-136`) | bezoeker kent geen "kennisbank": "Ik vind dat niet terug in onze helpartikelen" + concreet alternatief (artikel-link, contact). Citeertaal blijft uit de EvidencePack |
| Doel | informatief, correct, anti-confabulerend | hetzelfde, **plus** self-service-deflection en route naar vervolgstappen (setup, contact, upgrade) |
| Vraagbehandeling | multi-part per-vraag afhandelen (`:240-252`) | hetzelfde + actief doorvragen bij vage vragen ("Context and clarification" bij Fin); een bezoeker typt "bubble werkt niet" |
| Escalatie | geen instructie-laag; handoff alleen via knop | expliciete regels: bij "ik wil een mens", bij frustratie/herhaling, bij storing of contractvragen → direct aanbieden of forceren (Fin § Escalation, § 6.1) |
| Eerste contact | geen welkomst-disclosure in prompt | AI-openbaarmaking + verwachtingen (AI Act 50(5); zie § 6.4) |
| Persoonlijke gegevens | n.v.t. intern | widget vraagt pas naam/mail bij handoff (`collect_user_info`); publieke pagina = extra AVG-aandacht, bewaartermijn al gedekt door de retention-worker |

De widget kent daarvoor nu maar één ingang: het 4000-char `system_prompt`-veld, dat expliciet "does not override the source URL rules" (`partner_chat.py:1762-1768`) — toon-instructies botsen dus met de vaste Klai-voice in de base-prompt (beide staan in dezelfde system prompt; het model beslecht het conflict). Fase 1-advies: ofwel een helpdesk-promptvariant in de shared lib (`HELPDESK_GROUNDED_CHAT_SYSTEM_PROMPT`), ofwel een expliciete "persona vervangt Klai voice"-contractregel in de lib waardoor widget-instructies boven de voice-blokken primeren.

---

## 8. Gap-analyse voor de Voys-pilot

| ID | Gap | Ernst | Status | Bewijs |
|---|---|---|---|---|
| G-1 | **Handoff geblokkeerd voor Voys**: `_HUBSPOT_HANDOFF_DEV_TENANT_SLUG="getklai"`, origin-whitelist `getklai.getklai.com`, HubSpot-config wijst naar Klai-eigen inbox | **Blocker** (pilot zonder escalatie kan, maar "Praat met een medewerker" faalt dan) | deels — REQ-6 (boekings-redirect) | `partner.py:106-107, 2117-2149, 2485-2501`; `core/config.py:189-198`; compose `:1055` |
| G-2 | Widget-path mist answer-beleid (rewrite/brand-bridging, low-confidence, multi-question, strict) | hoog | open | § 5.2 |
| G-3 | Geen feedback-loop op de widget (geen thumbs in UI, geen kolom op `widget_messages`, `/feedback` is partner-key-only) | hoog | **gebouwd** — REQ-3 | § 2.1; `partner.py:2289-2301` + `require_permission("feedback")` |
| G-4 | Geen outcome-meting (resolved/escalated/abandoned) op conversaties; alleen volume/top-queries | hoog | in aanbouw — REQ-5 | `models/widgets.py:101-145`; `admin_widgets.py:804-811` |
| G-5 | Interne toon/jargon in basis-prompts en refusal; persona-conflict in de instructielaag | hoog | **gebouwd** — REQ-4 | § 7 |
| G-6 | Geen eval-harness voor path B (widget) — alle kwaliteitsmetingen gaan via pad A | hoog | open | § 5.2 |
| G-7 | Parallelle paden = driftgevaar; synchronisatie via handmatige comments | midden | open | `docker-compose.yml:966-971`; docstring-referenties |
| G-8 | Instructies: één vrij veld, geen structuur/versioning/testpad/usage-meting | midden | open | § 6.1 vs `admin_widgets.py:76` |
| G-9 | Page-context excerpt = aanvalsvector (injectie via hostpagina) — deels gemitigeerd | midden | open | § 6.3 |
| G-10 | AI Act-artikel-50-disclosure niet expliciet ingericht (alleen nauwkeurigheid-disclaimer) | midden | in aanbouw — REQ-6 | § 6.4; `labels.ts` (`disclaimer`) |
| G-11 | Content-bron-kloof: help.voys.nl (Super/Notion, externe crawl) vs klai-docs (Gitea, ingest) vs interne support-KB; oud/tegenstrijdig materiaal levert conflicterende antwoorden op | midden | open | § 3; corroboration-laag is uitgesteld (zie [README](README.md) §3) |
| G-12 | Twee chat-bubbels rechtsonder op help.voys.nl (Nerds booking-widget) | laag (UX) | open | HTML-fetch § 3 |
| G-14 | **Rate limits zijn per widget, niet per bezoeker**: chat 60 rpm hardcoded en gedeeld door alle bezoekers, sessie-mint 10/min, geen caching op `/widget-config` | **Blocker voor publiek verkeer** | **vervallen** — opgelost door REQ-2 | § 5.6; `partner_dependencies.py:210`; `partner.py:2577` |
| G-15 | **Widget-pad voedt de gap-registratie niet**: gap-events komen alleen uit de LiteLLM-hook, dus het gaten-dashboard ziet geen klantvragen | hoog (en goedkoop te dichten) | **gebouwd** — REQ-1 | § 5.5; `klai_knowledge.py:1412` vs geen "gap" in `partner_chat.py` |
| G-16 | Widget mint bij elke paginaweergave een sessietoken, ook voor de 90-97% die nooit chat; geen facade/lazy-load | hoog (lost G-14 op én versnelt de helppagina's) | **gebouwd** — REQ-2 | § 9.1 stap 8 |
| G-13 | Widget heeft geen server-side conversation state (client post hele history; server schrijft alleen een audit-spur) → beperkte context-recovery en geen "resume" over apparaten | laag (pilot-fase) | open | § 6.5 vs `chat-stream.ts:237-241` |

**Wat al wél klopt** (en dus niet gebouwd hoeft te worden): anonieme maar tenant-gebinde auth, default-deny origins, rate-limits, retention, audit-met-sources, NL/EN-widget, streaming-citaten, agent-activiteit, HubSpot-flow end-to-end getest (binnen één tenant), share-link `/bot/`, preview-vlagging die testdata uit productie-stats houdt, WCAG-conforme defaults, 200 kB-budget, safety-filters, deterministische refusal, taalcontract (3 guards) — dat is een solide chat-infrastructuur; hij is alleen **voor een ander oppervlak afgesteld**.

---

## 9. Aanbevolen setup en route

### 9.1 Fase 0 — no-code pilot op help.voys.nl (0 dev-werk, 1-2 dagen config)

1. **Widget aanmaken in de Voys-admin** (`/admin/widgets`), KB-scope = **alleen de helpdesk-content**: de gecrawde `help.voys.nl`-collectie (+ eventueel een curated Voys-help-KB). Geen interne/personal KB's. Dit is de KnowledgeBases-tab; het JWT draagt `kb_ids` mee — scope kan niet lekken (`widget_auth.py` claims).
2. **Origins:** `allowed_origins=["https://help.voys.nl"]` (niet `allow_any_origin`); de auto-default (`{org}.getklai.com`) overschrijven (`admin_widgets.py:356-357`).
3. **Platform-unlock controleren:** de grandfather-SQL-notes dat Voys op dat moment géén widgets geconfigureerd had; de actuele productiestatus is vanuit de repo **niet te verifiëren** — check `portal_orgs.platform_unlocked_features` of de admin-UI. Zie § 10.
4. **Config-UI:** `page_context_enabled=on` (context op de pagina's is de grote winst bij helpartikelen), `collect_user_info=on`, `hide_disclaimer=off`, `welcome_message` met AI-disclosure (§ 6.4), `conversation_starters` = de 4-6 meest gestelde Voys-vragen in de stijl van de evalset ("Hoe koppel ik …?", "Hoe werkt …").
5. **Handoff:** uit (G-1). Wordt de knop dan zichtbaar? Nee — de widget toont de handoff-knop alleen als `integrations.hubspot.status=connected` in de widget-config staat (`partner.py:2671`); bij Voys dus vanzelf uit.
6. **Instructies (plak-klaar, NL)** — voor in het `system_prompt`-veld, bewust kort en Fin-achtig gecategoriseerd:

```
[Persona]
Je bent de helpbot van Voys. Je helpt klanten (belsystemen, CRM, apps) zelf hun
probleem op te lossen. Toon: behulpzaam, zakelijk-vriendelijk, je/jullie.
Geen emoji. Antwoord in het Nederlands tenzij de klant een andere taal
schrijft.

[Afbakening]
Alleen Voys-producten en Voys-onderwerpen. Bedrijfsspecifieke accountgegevens
(koppelnummers, factuurbedragen, contractdata) kun je niet opvragen: verwijs
naar Beheer of een medewerker. Je adviseert niet over producten van derden,
behalve koppelingen die in de artikelen staan.

[Niet gevonden]
Zeg in klantentaal: "Dat vind ik niet in onze helpartikelen." Bied daarna aan
(1) het gerelateerde onderdeel op help.voys.nl, voor zover dat uit de bronnen
komt, en (2) contact met de supportdesk. Zeg nooit "kennisbank".

[Doorvragen]
Vage vraag (geen product/context) → stel maximaal één korte verduidelijkingsvraag.
Meerdere vragen in één bericht → beantwoord ze genummerd, één voor één.

[Escalatie]
Bied een medewerker aan bij: frustratie, herhaling van dezelfde klacht, klacht,
annulering/opzegging, storing van telefonie, of vragen over prijzen/contracten.

[Citaten]
Verwijs naar de helpartikelen die onder het antwoord getoond worden; noem zelf
geen URLs.
```

7. **Embed:** Super-site → custom head/code: `<script async src="https://widget.getklai.com/widget/n" data-widget-id="wgt_…" data-locale="nl">` (URL-vorm conform `widget-test.tsx`; exact prod-pad opvragen bij het uitrollen). Positionering: test of de bubble van Nerds en die van Klai botsen (G-12) — eventueel Klai inline op artikelpagina's (`data-mode="inline" data-container=…`, `main.ts:73-103`).
8. **Widget lui laden met een facade-bubbel (G-16), vóór livegang.** Vervangt het
   ophogen van de limiet uit G-14: in plaats van bij elke paginaweergave een
   sessietoken te minten, toont de pagina een lichtgewicht nepbubbel en wordt de
   echte widget (plus het token) pas geladen bij de eerste klik. Onderbouwing:
   slechts 3-10% van de bezoekers opent een chatwidget
   ([corewebvitals.io](https://www.corewebvitals.io/pagespeed/chat-widget-perfect-core-web-vitals)),
   dus 90-97% van de mints is nu verspilling. Dit is het standaardpatroon —
   Zendesk biedt er `connectOnPageLoad` voor, Gorgias bracht er de
   Lighthouse-impact van hun widget mee terug tot één punt
   ([Gorgias](https://www.gorgias.com/blog/reduce-chat-widget-lighthouse-score)).
   Drie effecten tegelijk: de mint-limiet wordt met een factor 10-30 ontlast
   waardoor G-14 als capaciteitsvraag vervalt, de helppagina's worden meetbaar
   sneller, en "widget geladen" gaat eindelijk hetzelfde betekenen als "chat
   geopend" in de statistiek. Daarna kan de limiet per bezoeker in plaats van
   per site, wat meteen het gedeeld-lot-scenario afdekt (één bot legt de chat
   voor iedereen plat) — Chatwoot doet precies dat, per IP
   ([Chatwoot](https://developers.chatwoot.com/self-hosted/monitoring/rate-limiting)).
9. **Capaciteit vóór livegang regelen (G-14):** een per-bezoeker-dimensie naast de per-widget-limiet, en `/widget-config` cachebaar maken of het sessietoken hergebruiken over pagina-navigaties. Zonder dit loopt een helpcentrum met echt verkeer vast op 60 rpm gedeeld en 10 mints/min. Dit is het enige Fase 0-item dat wél dev-werk is.
10. **Meetplan vanaf dag 1:** wekelijkse handmatige review van de eerste 50 transcripts (admin Activity-tab); definitie vóór start afgesproken: *contained* = gesprek zonder handoff én geen vervolg-ticket binnen 48 uur op hetzelfde onderwerp (afgeleid van de Rasa-formule, § 6.2); noteer top-queries uit stats en label ze "goed / fout / content ontbreekt".

### 9.2 Fase 1 — verplaatst naar de SPEC

De vijf dev-stappen die hier stonden zijn overgenomen als requirements in
`docs/specs/SPEC-VOYS-HELPBOT-001/spec.md` (REQ-1 t/m REQ-8), inclusief de
briefs waarmee ze gebouwd zijn en de beslissingen die onderweg zijn omgedraaid.
Ze staan hier bewust niet meer: één plan op twee plekken bijhouden loopt uit
elkaar, en de SPEC is de plek waar de status per onderdeel klopt.

Wat hier wél blijft staan is § 9.1 hierboven — de no-code pilotconfiguratie is
beheerwerk in de portal, valt expliciet buiten de scope van de SPEC, en vraagt
iemand die de Voys-inhoud kent.

### 9.3 Fase 2 — structureel (na de pilot-beslissing)

- **Eén orchestratiepad (G-7):** de answer-policy-logica uit de LiteLLM-hook naar een shared service/lib tillen (vergelijkbaar met hoe `klai-libs/citations` en `klai-libs/chat-prompts` dat al doen voor citaten/prompts) en door zowel hook als partner_chat laten aanroepen. Doel: "app-gedrag = widget-gedrag, behalve de expliciete persona."
- **Widget-eval-harness (G-6):** chat.yaml parametriseren op path B (`/partner/v1/chat/completions` met widget-token) zodat dezelfde 41 queries wekelijks op de widget-route draaien; drempel = huidige pad-A-resultaten.
- **Guidance as data (G-8):** het 4000-char-veld splitsen in Fin-categorieën (toon / afbakening / escalatie / content-routing), versiebeheer in de DB (template-patroon bestaat al via `template_slug`), plus per-segment "gebruikt bij X gesprekken"-statistiek uit de audit-tabel.
- **Content-governance (G-11):** één source-of-truth per vraaggebied; voor Voys betekent dat: crawl van help.voys.nl met verversings-SLA, of content-overdracht naar klai-docs (met widget-embed daar, waar we zelf de CSP/placement in de hand hebben).
- **Server-side sessies (G-13)** als resume/cross-device waardevol blijkt; de Azure-baseline bevestigt het patroon (§ 6.5).

### 9.4 Expliciet niet gedaan (en waarom)

- Geen multi-agent-orkestratie: voor een afgebakende helpdesk volstaat één agent met tools; multi-agent verhoogt latency en complexiteit zonder winst op dit oppervlak (zelfde conclusie in de Azure-baseline § single-vs-multiagent).
- Geen eigen NL-fine-tune of kleiner model: het taalrisico (§ 6.7) vangen we met de eigen evalsuite, niet met een modelwissel.
- Geen lead-capture/CRM-routing boven wat `collect_user_info` + de handoff-summary al doen: de HubSpot-handoff draagt naam/mail + transcript al mee (`handoff.ts:20-47`).

---

## 10. Niet geverifieerd / open vragen

1. **Productiestatus Voys-unlock en bestaande Voys-widget(s):** `post_deploy_h1i2j3k4l5m6_…sql:22` beschrijft "Voys heeft noch widgets noch custom MCPs" op de datum van die migration; de actuele rij in `portal_orgs.platform_unlocked_features` is vanaf deze checkout niet te lezen (GEEN productietoegang in dit onderzoek).
2. **Of `/widget/n` het werkelijke prod-pad is** en of de bundle op `widget.getklai.com` de huidige broncode weerspiegelt (alleen code-zijde bekeken; geen live-check van het asset).
3. **Reikwijdte van de evalscripts op pad A:** de suites meten pad A; in hoeverre die uitslagen opgaan voor pad B is per definitie onbekend (dat is juist G-6).
4. **Of help.voys.nl Super-custom-code op alle pagina's toestaat** (de Nerds-embed bewijst dat het kan, maar niet of het per losse artikelpagina mag) en of er CSP/CORS-conflict ontstaat op die host.
5. **Juridische kwalificatie** van de AI Act-plicht en de AVG-positie bij chat-overnames door supportmedewerkers — voorbehouden aan juridisch advies.
6. **Keuze help.voys.nl vs klai-docs als pilot-oppervlak** — dit document legt beide kanten uit; beslistpunt voor Mark.

---

## Bronnen

**Intern (deze checkout, `main` @ `e3fb3de4a`):** `docs/architecture/regular-chat-knowledge-retrieval-citations.md`; `docs/knowledge-retrieval-low-confidence-abstain-2026-05-08.md`; `docs/research/README.md`; `docs/research/kb-chat-system-prompts.md`; `deploy/litellm/config.yaml`; `deploy/litellm/klai_kb_{query_rewrite,confidence_policy,citation_render,context_prompt}.py`; `deploy/litellm/eval_suites/chat.yaml`; `deploy/litellm/scripts/eval_pii_restore_live.py`; `deploy/docker-compose.yml:934-935, 966-971, 1055`; `klai-libs/chat-prompts/klai_chat_prompts/__init__.py`; `klai-libs/citations/klai_citations/__init__.py`; `klai-retrieval-api/retrieval_api/models.py`; `klai-retrieval-api/retrieval_api/services/search.py`; `klai-widget/{package.json,vite.config.ts,scripts/check-bundle-size.mjs,src/*}`; `klai-docs/{middleware.ts,migrations/001_docs_schema.sql,docs/architecture.md}`; `klai-portal/backend/app/api/{partner.py,admin_widgets.py,app_assistant.py}`; `klai-portal/backend/app/services/{partner_chat.py,widget_auth.py,widget_handoff.py,widget_messages_retention.py,hubspot_custom_channel.py,access.py}`; `klai-portal/backend/app/models/widgets.py`; `klai-portal/backend/app/core/config.py`; `klai-portal/backend/alembic/versions/post_deploy_h1i2j3k4l5m6_grandfather_platform_unlocks.sql`; `klai-portal/frontend/src/routes/admin/widgets/*`, `routes/{bot/$widgetId.tsx,widget-test.tsx}`; `klai-portal/backend/tests/test_widget_*.py`.

**Extern:**
- EU AI Act Art. 50 — <https://www.artificialintelligenceact.eu/article/50/> (opgehaald 2026-09-03)
- Intercom, "Provide Fin AI Agent with specific guidance" — <https://www.intercom.com/help/en/articles/10210126-provide-fin-ai-agent-with-specific-guidance> (vandaag)
- Intercom, "Fin Procedures explained" — <https://www.intercom.com/help/en/articles/12495167-fin-procedures-explained>
- Derde-partij-analyses van Fin's loop — <https://www.getmacha.com/blog/intercom-fin-ai-agent-complete-guide>, <https://blog.octopods.io/intercom-fin-guide/>
- Rasa, "AI Agent Performance Metrics" — <https://rasa.com/blog/measure-ai-agent-performance-in-the-contact-center>
- Decagon, "What is Containment Rate" — <https://decagon.ai/glossary/what-is-chatbot-containment-rate>
- Owlish, "Resolution vs Deflection vs Containment" — <https://owlish.bot/blog/resolution-rate-vs-deflection-rate/>
- digitalapplied, "AI Customer Support Metrics" — <https://www.digitalapplied.com/blog/ai-customer-support-metrics-deflection-csat-framework-2026>
- Heeya, "AI Chatbot KPIs 2026" — <https://heeya.fr/en/blog/ai-chatbot-kpis-metrics-guide-2026>
- OWASP LLM Top 10 (2025) over indirecte injectie/RAG-poisoning — <https://www.oligo.security/academy/owasp-top-10-llm-updated-2025-examples-and-mitigation-strategies>, <https://bsg.tech/blog/owasp-llm-top-10/>, <https://www.promptfoo.dev/docs/red-team/owasp-llm-top-10/>
- Microsoft Foundry Chat baseline architecture — <https://learn.microsoft.com/en-us/azure/architecture/ai-ml/architecture/baseline-microsoft-foundry-chat>
- web.dev, "Best practices for using third-party embeds" — <https://web.dev/articles/embed-best-practices>
- Embed-encapsulatie-discussies — <https://stackoverflow.com/questions/49220533/how-are-javascript-widgets-made-without-iframes>, <https://www.reddit.com/r/webdev/comments/1hb0kw6/preferred_method_for_creating_3rd_party/>
- Nederlandse LLM-evaluatie — <https://arxiv.org/html/2412.15450v1> (Fietje), <https://www.reddit.com/r/thenetherlands/comments/1moyqac/prestaties_van_grote_taalmodellen_llms_in_het/> (EuroEval), <https://artificialanalysis.ai/models/multilingual>
- help.voys.nl — live-fetch van de homepage (HTML, vandaag): Super.so/Next.js-site, `lang="nl"`, Nerds helpdesk/booking-embed.
