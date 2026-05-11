---
id: SPEC-PORTAL-KENNIS-002
version: 0.1.0
status: draft
created: 2026-05-11
author: Jantine Doornbos
priority: high
parent: SPEC-PORTAL-KENNIS-001 (consolidates the post-merge direction)
---

# SPEC-PORTAL-KENNIS-002: Cleane KB — alles is een bron, focus op CRUD

## Goal

Een KB-detailscherm dat een niet-technische gebruiker meteen begrijpt:

- **Alles is een bron.** Connectors en directe uploads renderen identiek.
- **De drie acties die er toe doen** — bron toevoegen, bron verwijderen, bron syncen — zijn altijd zichtbaar en één klik weg.
- **Drie tabs**, niets meer. Bronnen / Instellingen / Geavanceerd.
- **Alle complexiteit** (taxonomie, gevorderde connector-config, danger zone) staat onder **Geavanceerd**. De Bronnen-tab is rustig.
- **Past in de grid.** KB-titel ligt op dezelfde lijn als het sidebar-logo. Lijsten gebruiken de portal-list-pattern (top+bottom border, divide-y, geen rounded boxes per rij).

Deze SPEC consolideert de richting die in het 2026-05-08 iteratie-traject naar voren kwam. SPEC-PORTAL-KENNIS-001 carvde de feat-branch over naar main; deze SPEC legt de definitieve UI vast en sluit de losse eindjes.

## Core principle: zichtbaar wat ertoe doet, weggestopt wat verstoort

| Wat | Waar |
|---|---|
| Bron toevoegen | Primary button, KB-header (altijd zichtbaar) |
| Bron syncen | Refresh-icoon per rij (alleen connectors) + "Synchroniseer alles"-knop bovenaan |
| Bron verwijderen | Inline op de rij (hover of via inline-confirm) — destructive maar bereikbaar |
| Status zien | Eén badge per rij: **Gesynct / Bezig / Niet gesynct** |
| Inhoud bekijken | Klik op rij → expandt inline. Connector → items → chunks. Upload → chunks. |
| Naam/omschrijving wijzigen | Instellingen-tab |
| Leden / toegang | Instellingen-tab (onder Bewerken) |
| Taxonomie, danger zone | Geavanceerd-tab |
| Sync-historie, error-details | Geavanceerd-tab (later) |

## Scope

### In scope (deze SPEC)

**Frontend:**
- `routes/app/knowledge/$kbSlug/bronnen.tsx` — unified list, sync-knoppen, inline drill-down, 3-state status
- `routes/app/knowledge/$kbSlug/route.tsx` — 3-tab shell, KB-titel in h-[66px] strip
- `routes/app/knowledge/$kbSlug/settings.tsx` — Instellingen-tab: KB-velden + ledenlijst
- `routes/app/knowledge/$kbSlug/taxonomy.tsx` — Geavanceerd-tab: taxonomie + danger zone
- `routes/app/knowledge/index.tsx` — KB-lijst met dezelfde status-taal, "Gesynct"-label
- i18n: `kb_status_*` labels (nl + en)

**Backend:**
- `klai-knowledge-ingest`: `count_sources_per_kb` + `chunks-summary` extended met `bronnen_by_kb`
- `klai-portal/backend`: stats-summary gebruikt bronnen-by-kb voor `bronnen`, Qdrant-count voor `chunks`
- `klai-portal/backend`: `_get_kb_with_owner_check` accepteert `is_platform_admin` bypass

### Out of scope

- Sync-historie tab / sync-run details (komt in latere SPEC)
- Bulk bron-acties (selecteren + bulk delete / sync)
- Connector reauth flow restyling (raakt aan SPEC-CONNECTOR-INPUT-VALIDATION-001)
- Per-bron error-pagina met aangepaste recovery (vervolgwerk)
- Nieuwe brontypen (PDF/URL/connector blijven wat ze nu zijn)

## Requirements (EARS)

### Layout en grid

- **REQ-1** WHEN een gebruiker een KB-detailpagina opent, THE system SHALL de KB-naam tonen in een `h-[66px]` flex-strip bovenaan zodat de titel op dezelfde verticale lijn ligt als het sidebar-logo. Apply `page-title` utility (2px Parabole ascender-trim).
- **REQ-2** WHEN de Bronnen-lijst wordt gerenderd, THE system SHALL `border-t border-b border-gray-200 divide-y divide-gray-200` gebruiken op de container; rijen zijn flat zonder rounded box.
- **REQ-3** WHEN de KB-titel langer is dan de beschikbare breedte, THE system SHALL `truncate` toepassen zonder de actie-knop rechts te verbergen.

### Tab-structuur

- **REQ-4** THE system SHALL exact drie tabs tonen: **Bronnen** (default), **Instellingen**, **Geavanceerd**. Geen extra tabs.
- **REQ-5** Tab-bar SHALL alleen labels tonen (geen iconen) met onderstreep-indicator voor de actieve tab. Sentence-case, geen uppercase.
- **REQ-6** WHEN de gebruiker niet de minimale rol `kb_manager` heeft AND geen platform admin is, THE system SHALL de Geavanceerd-tab verbergen.
- **REQ-7** Legacy URL-querystrings (`?tab=overview|items|connectors|members|taxonomy|advanced|settings`) SHALL redirecten naar de juiste nieuwe tab.

### Status-model

- **REQ-8** THE system SHALL exact drie status-toestanden tonen per bron:

| Status | Wanneer | Label (nl) | Variant |
|---|---|---|---|
| `synced` | Connector `last_sync_status` in `{success, completed, ok}` OF upload aanwezig in `/sources` | Gesynct | `success` |
| `pending` | Connector `last_sync_status` in `{running, pending, syncing}` | Bezig | `secondary` |
| `not_synced` | Connector `last_sync_status` in `{error, failed, auth_error, orphan}` OF connector zonder ingest-data | Niet gesynct | `secondary` |

Uploads kunnen niet `pending` of `not_synced` zijn — een upload die in `/sources` voorkomt is per definitie gesynct.

- **REQ-9** WHEN ten minste één bron de status `pending` heeft, THE system SHALL de `/sources` query automatisch elke 4 seconden hervragen zodat de UI live updatet.
- **REQ-10** WHEN alle bronnen `synced` of `not_synced` zijn, THE system SHALL polling stoppen.

### Acties

- **REQ-11** THE system SHALL een **"Bron toevoegen"** primary button tonen in de KB-header, op elke tab zichtbaar.
- **REQ-12** WHEN er ten minste één connector-bron bestaat, THE system SHALL een **"Synchroniseer alles"** ghost-knop tonen boven de bronnenlijst. Klikken triggert per connector één POST `/api/app/knowledge-bases/{slug}/connectors/{id}/sync` parallel.
- **REQ-13** Per connector-bron SHALL een refresh-knop in de rij zichtbaar zijn (niet hover-only). Klikken triggert dezelfde sync-endpoint. Tijdens `isPending` toont de knop een spinner en is disabled.
- **REQ-14** WHEN een sync-knop wordt geklikt, THE system SHALL `['kb-bronnen', kbSlug]` en `['app-knowledge-bases-stats-summary']` invalideren bij succes.
- **REQ-15** Per upload-bron SHALL een verwijder-actie beschikbaar zijn via `InlineDeleteConfirm`. Connectoren verwijderen blijft achter een explicit-confirm (kan dezelfde component gebruiken).
- **REQ-16** WHEN de caller `is_platform_admin == true` is, THE system SHALL de owner-only gate in `_get_kb_with_owner_check` overslaan zodat admin alle connector-acties kan uitvoeren.

### Inline drill-down

- **REQ-17** WHEN een gebruiker op een bron klikt, THE system SHALL de rij expanderen en de inhoud inline tonen:
  - Connector → lijst van items (artifact-paden + chunk-count per item)
  - Upload → lijst van chunks (positie + token-count + tekstvoorbeeld)
- **REQ-18** Slechts één bron tegelijk SHALL geëxpandeerd zijn. Klikken op een tweede bron sluit de eerste.
- **REQ-19** Expand-state SHALL niet bewaard blijven over page-navigatie (geen URL state).

### Tellingen (backend-correctheid)

- **REQ-20** THE `bronnen`-veld in `KBStatsSummary` SHALL het aantal distinct bronnen tellen (connector_id-groepen + losse upload-artifacts), niet alleen `portal_connectors`.
- **REQ-21** THE `chunks`-veld in `KBStatsSummary` SHALL de Qdrant point-count gebruiken (`items_by_slug`), niet `parent_chunks` (die is alleen gevuld voor KBs die het citation-enrichment-pad hebben gedraaid).
- **REQ-22** WHEN knowledge-ingest niet bereikbaar is voor `chunks-summary`, THE system SHALL terugvallen op `bronnen = connectors_count` en `chunks = items_count` zonder 5xx.

### Instellingen-tab

- **REQ-23** THE Instellingen-tab SHALL voor elke caller zichtbaar zijn (geen `return null`). Niet-owners zien een read-only formulier.
- **REQ-24** Admins SHALL het formulier kunnen bewerken zelfs als ze geen KB-rol owner hebben.
- **REQ-25** THE Instellingen-tab SHALL een ledenlijst onder het formulier tonen (e-mail + rol per lid).

### Geavanceerd-tab

- **REQ-26** THE Geavanceerd-tab SHALL routeren naar `/taxonomy` als URL.
- **REQ-27** Onderaan de taxonomie SHALL een **Verwijderzone** staan met de "Verwijder KB"-knop, alleen zichtbaar voor owner of admin.
- **REQ-28** Toekomstige geavanceerde features (per-connector reauth UI, sync-historie, RLS-debug) SHALL hier worden ingehangen — niet op de Bronnen-tab.

### Copy / i18n

- **REQ-29** Statuslabels gebruiken paraglide-keys `kb_status_klaar`, `kb_status_bezig`, `kb_status_leeg` met waarden:
  - NL: "Gesynct" / "Bezig" / "Niet gesynct"
  - EN: "Synced" / "Working" / "Not synced"
- **REQ-30** Geen technisch jargon zichtbaar voor de gebruiker: geen "indexed", "auth_error", "running" — die mappen op de drie statuslabels.

## Niet-functionele eisen

- **NFR-1** De Bronnen-lijst SHALL renderen binnen 200ms na het binnenkomen van het `/sources` resultaat (geen JS-side filtering die N+1 doet).
- **NFR-2** `Synchroniseer alles` SHALL non-blocking zijn: de POSTs fan out parallel; de UI wacht niet op completion. Verifieer met `Promise.allSettled`.
- **NFR-3** Geen kleurige icoon-tints per provider (eerder afgewezen door brand). Alle bron-icons in `bg-gray-50 text-gray-500`.

## Acceptance criteria (samenvatting)

Een KB-detail-bezoek slaagt als de gebruiker:

1. De KB-titel verticaal uitgelijnd ziet met het logo.
2. Exact drie tabs ziet (en Geavanceerd ook ziet wanneer admin of kb_manager).
3. Per bron precies één van **Gesynct / Bezig / Niet gesynct** als badge ziet.
4. Een sync-knop per connector-rij ziet die werkt op één klik (admin-bypass landde, zie REQ-16).
5. "Synchroniseer alles" en "Bron toevoegen" boven de lijst ziet.
6. Bij klikken op een bron de inhoud inline ziet (items voor connector, chunks voor upload).
7. Op de Instellingen-tab een formulier + ledenlijst ziet (read-only voor niet-owners).
8. Op de Geavanceerd-tab de volledige taxonomie-UI ziet met een verwijderzone onderaan.
9. In de KB-lijst correcte bronnen- en chunks-counts ziet (niet meer "0 bronnen · 0 chunks" voor KBs die wel inhoud hebben).
10. Tijdens een sync de "Bezig"-status ziet updaten zonder handmatige refresh.

## Status van de implementatie (2026-05-11)

Deze SPEC documenteert grotendeels werk dat al op `main` staat (commits 2026-05-08):

- ✅ 3-tab shell (Bronnen / Instellingen / Geavanceerd) — `route.tsx`
- ✅ KB-titel in `h-[66px]` strip met `page-title` utility
- ✅ 3-state status-model + i18n labels
- ✅ Per-row sync-knop + "Synchroniseer alles"
- ✅ Auto-poll tijdens pending
- ✅ Inline drill-down connector → items, upload → chunks
- ✅ Backend `count_sources_per_kb` + `bronnen_by_kb` veld
- ✅ Qdrant count gebruikt voor `chunks` display
- ✅ Admin-bypass op `_get_kb_with_owner_check`
- ✅ Instellingen-tab zichtbaar voor admin + read-only voor niet-owners
- ✅ Geavanceerd-tab routeert naar `/taxonomy` met danger zone onderaan

**Openstaand:**

- ⏳ Per-upload delete via `InlineDeleteConfirm` (REQ-15) — sync-knop is er, delete-actie ontbreekt nog in de huidige bronnen.tsx
- ⏳ Connector reauth flow als duidelijke recovery-knop bij `not_synced` met reden `auth_error`
- ⏳ Sync-historie als sub-sectie onder Geavanceerd (REQ-28)
- ⏳ End-to-end Playwright happy-path: load KB → sync alles → wacht → bekijk chunks

## Risico's

- **Status-mapping drift.** Connector `last_sync_status` is een open string. Nieuwe waarden (b.v. `paused`) mapt onbedoeld naar `not_synced`. Mitigatie: log onverwachte waarden in `mapStatus` zodat we het zien.
- **Polling-cost.** 4s poll terwijl pending is, vermenigvuldigd over open sessies, kan portal-api hits opdrijven. Mitigatie: stop polling wanneer geen pending; tab niet zichtbaar (`document.hidden`) zou een later optimization zijn.
- **Bronnen-count diverentie tussen list en detail.** Stats-summary telt via `count_sources_per_kb`; detail rendert via `list_kb_sources`. Beide queries op dezelfde tabel + filters, maar kunnen door RLS-context-verschil afwijken. Mitigatie: één integration-test die op één test-KB beide endpoints raakt en de count vergelijkt.
- **Admin-bypass blast-radius.** REQ-16 geeft platform-admins toegang tot connector-CRUD op elke KB in hun tenant. Acceptabel per portal-security.md (admin = tenant-level superuser), maar logging op admin-acties is essentieel.

## Open vragen

1. Moet een upload-bron ook een "Hernoemen"-actie krijgen, of accepteren we dat de oorspronkelijke bestandsnaam blijft?
2. Wanneer een connector in `not_synced` staat door `auth_error` — willen we een aparte "Verbind opnieuw"-knop in de rij, of valt dat onder Geavanceerd?
3. Moet `Synchroniseer alles` ook voor uploads iets doen (nu: enkel connectors)? Vermoedelijk nee, een upload kan niet hergesyncd worden zonder her-uploaden.
4. Tab-volgorde: gebruiker noemde "Bronnen / Instellingen / Geavanceerd". Bevestigd?
