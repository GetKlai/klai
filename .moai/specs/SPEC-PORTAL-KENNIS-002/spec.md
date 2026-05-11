---
id: SPEC-PORTAL-KENNIS-002
version: 0.3.0
status: ready
created: 2026-05-11
updated: 2026-05-11
author: Jantine Doornbos
priority: high
parent: SPEC-PORTAL-KENNIS-001 (consolidates the post-merge direction)
---

# SPEC-PORTAL-KENNIS-002: Cleane KB — alles is een bron, focus op CRUD

## Goal

Een KB-detailscherm dat een niet-technische gebruiker meteen begrijpt:

- **Alles is een bron.** Connectors en directe uploads renderen identiek.
- **De drie acties die er toe doen** — bron toevoegen, bron verwijderen, bron syncen — zijn altijd zichtbaar en één klik weg.
- **Drie tabs**, niets meer. Bronnen / Instellingen / Inzichten.
- **Alle KB-configuratie** (naam, leden, taxonomie, danger zone) hoort thuis op Instellingen. **Inzichten** is een pure info-tab (sync-historie en toekomstige diagnostics) — geen mutaties. De Bronnen-tab is rustig.
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
| Naam/omschrijving wijzigen | Instellingen-tab → Algemeen |
| Leden / toegang | Instellingen-tab → Toegang |
| Taxonomie | Instellingen-tab → collapsible "Bronnen-structuur" (geavanceerd, default ingeklapt, kb_manager+) |
| KB verwijderen (danger zone) | Instellingen-tab → Verwijderen (onderaan) |
| Sync-historie, error-details | Inzichten-tab |

## Scope

### In scope (deze SPEC)

**Frontend:**
- `routes/app/knowledge/$kbSlug/bronnen.tsx` — unified list, sync-knoppen, inline drill-down, 3-state status
- `routes/app/knowledge/$kbSlug/route.tsx` — 3-tab shell (Bronnen / Instellingen / Inzichten), KB-titel in h-[66px] strip
- `routes/app/knowledge/$kbSlug/settings.tsx` — Instellingen-tab: Algemeen + Toegang + Bronnen-structuur (taxonomie, collapsible) + Verwijderen (danger zone)
- `routes/app/knowledge/$kbSlug/insights.tsx` *(nieuw)* — Inzichten-tab: sync-historie per connector
- `routes/app/knowledge/$kbSlug/taxonomy.tsx` — wordt sub-component, geïmporteerd in settings.tsx als collapsible sectie (route blijft bereikbaar voor backward-compat; redirect naar `/settings#bronnen-structuur` overweegt)
- `routes/app/knowledge/index.tsx` — KB-lijst met dezelfde status-taal, "Gesynct"-label
- i18n: `kb_status_*` labels (nl + en), nieuwe keys voor tab-namen en sectie-headers

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

- **REQ-4** THE system SHALL exact drie tabs tonen: **Bronnen** (default), **Instellingen**, **Inzichten**. Geen extra tabs.
  - **Instellingen** = *wat is deze KB?* — alle configuratie: naam, beschrijving, leden, bronnen-structuur (taxonomie, collapsible), verwijderen. Alle mutaties die de KB definiëren.
  - **Inzichten** = *wat is er gebeurd?* — pure info-tab: sync-historie en (later) gebruiksstatistieken / citaten-data. Geen mutaties, geen settings.
- **REQ-5** Tab-bar SHALL alleen labels tonen (geen iconen) met onderstreep-indicator voor de actieve tab. Sentence-case, geen uppercase.
- **REQ-6** THE Inzichten-tab SHALL zichtbaar zijn voor owner + admin. Contributor en viewer zien de tab niet (consistent met "geen mutaties = ja zien" zou nog kunnen, maar sync-error-details bevatten interne info die we niet aan iedereen tonen). De **Bronnen-structuur** sectie in Instellingen SHALL alleen zichtbaar zijn voor kb_manager+ en admin.
- **REQ-7** Legacy URL-querystrings (`?tab=overview|items|connectors|members|taxonomy|advanced|settings`) SHALL redirecten:
  - `overview|items|connectors` → `/bronnen`
  - `members|settings` → `/settings`
  - `taxonomy|advanced` → `/settings` (met scroll-anchor `#bronnen-structuur` indien aanwezig)
  - Nieuw: `/insights` is de Inzichten-tab.

### Status-model

- **REQ-8** THE system SHALL exact drie status-toestanden tonen per bron:

| Status | Wanneer | Label (nl) | Variant |
|---|---|---|---|
| `synced` | Connector `last_sync_status` in `{success, completed, ok}` OF upload met afgeronde indexatie | Gesynct | `success` |
| `pending` | Connector `last_sync_status` in `{running, pending, syncing}` OF upload tijdens (re-)indexatie | Bezig | `secondary` |
| `not_synced` | Connector `last_sync_status` in `{error, failed, auth_error, orphan}` OF upload met gefaalde indexatie | Niet gesynct | `secondary` |

Uploads kunnen dankzij re-index (Q5) ook `pending` of `not_synced` zijn. De status komt voor uploads uit een `index_status` veld op het artifact (toe te voegen).

- **REQ-9** WHEN ten minste één bron de status `pending` heeft, THE system SHALL de `/sources` query automatisch elke 4 seconden hervragen zodat de UI live updatet.
- **REQ-10** WHEN alle bronnen `synced` of `not_synced` zijn, THE system SHALL polling stoppen.

### Acties

- **REQ-11** THE system SHALL een **"Bron toevoegen"** primary button tonen in de KB-header, op elke tab zichtbaar.
- **REQ-12** WHEN er ten minste één bron bestaat, THE system SHALL een **"Synchroniseer alles"** ghost-knop tonen boven de bronnenlijst. Klikken triggert per connector een POST `/api/app/knowledge-bases/{slug}/connectors/{id}/sync` EN per upload een POST `/api/app/knowledge-bases/{slug}/uploads/{artifact_id}/reindex` — parallel via `Promise.allSettled`.
- **REQ-13** Per bron SHALL een refresh-knop in de rij zichtbaar zijn (niet hover-only). Voor connectors triggert dit de sync-endpoint, voor uploads de reindex-endpoint. Tijdens `isPending` toont de knop een spinner en is disabled.
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
- **REQ-25** THE Instellingen-tab SHALL vier secties tonen, in deze volgorde, gescheiden door `border-t border-gray-200 pt-6`:
  1. **Algemeen** — naam (editable voor owner/admin), beschrijving (editable), slug (read-only)
  2. **Toegang** — ledenlijst (e-mail + rol per lid). Voor owners + admins: acties om leden toe te voegen / rol te wijzigen / te verwijderen (verhuist vanuit `members.tsx`).
  3. **Bronnen-structuur** — collapsible (default ingeklapt), alleen zichtbaar voor kb_manager+ en admin. Importeert de bestaande taxonomy-UI als sub-component. Header: "Bronnen-structuur (geavanceerd)".
  4. **Verwijderen** — danger zone met **"Verwijder KB"**-knop, alleen zichtbaar voor owner of admin. Hergebruikt `DeleteKbModal` (typed-name confirmation — enige toegestane modal-uitzondering per NFR-4).
- **REQ-26** Niet-owners zien sectie 1 en 2 read-only; sectie 4 is volledig verborgen. Sectie 3 verschijnt enkel bij kb_manager+ of admin.

### Inzichten-tab

- **REQ-27** THE Inzichten-tab SHALL routeren naar `/insights` als URL en uitsluitend info-surfaces bevatten:
  1. **Sync-historie** — per connector een collapsible met laatste 10 runs (status / start-time / duur / fout-reden). Data uit `connector.sync_runs`. Default ingeklapt.
  2. *(toekomst)* Gebruiksstatistieken — top-gecirteerde chunks, query-volume, unieke gebruikers.
- **REQ-28** THE Inzichten-tab SHALL geen mutaties of destructive actions bevatten — pure read-only view. Een sync vanaf hier triggeren is niet mogelijk (gebruik Bronnen-tab).
- **REQ-29** Inzichten-tab SHALL zichtbaar zijn voor owner + admin (zie REQ-6). Toekomstige diagnostic / power-features (RLS-debug, Qdrant-state, embedding-model-info) horen hier ingehangen te worden.

### Copy / i18n

- **REQ-29** Statuslabels gebruiken paraglide-keys `kb_status_klaar`, `kb_status_bezig`, `kb_status_leeg` met waarden:
  - NL: "Gesynct" / "Bezig" / "Niet gesynct"
  - EN: "Synced" / "Working" / "Not synced"
- **REQ-30** Geen technisch jargon zichtbaar voor de gebruiker: geen "indexed", "auth_error", "running" — die mappen op de drie statuslabels.

## Niet-functionele eisen

- **NFR-1** De Bronnen-lijst SHALL renderen binnen 200ms na het binnenkomen van het `/sources` resultaat (geen JS-side filtering die N+1 doet).
- **NFR-2** `Synchroniseer alles` SHALL non-blocking zijn: de POSTs fan out parallel; de UI wacht niet op completion. Verifieer met `Promise.allSettled`.
- **NFR-3** Geen kleurige icoon-tints per provider (eerder afgewezen door brand). Alle bron-icons in `bg-gray-50 text-gray-500`.
- **NFR-4** **Geen modals.** Bevestigingen, hernoemen, delete-flows gebeuren inline (`InlineDeleteConfirm`, `InlineEdit`). De enige toegestane modal-uitzondering is de bestaande `DeleteKbModal` voor de hele KB onder Geavanceerd (typed-name confirmation voor onomkeerbare org-impact). Geen `AlertDialog` of nieuwe modals voor bron-niveau acties.

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

Deze SPEC documenteert grotendeels werk dat al op `main` staat (commits 2026-05-08). De Q9-rename (Geavanceerd → Inzichten + taxonomie naar Instellingen) is nieuw en nog niet gebouwd.

- ✅ 3-tab shell (Bronnen / Instellingen / nu nog "Geavanceerd") — `route.tsx`
- ✅ KB-titel in `h-[66px]` strip met `page-title` utility
- ✅ 3-state status-model + i18n labels
- ✅ Per-row sync-knop + "Synchroniseer alles"
- ✅ Auto-poll tijdens pending
- ✅ Inline drill-down connector → items, upload → chunks
- ✅ Backend `count_sources_per_kb` + `bronnen_by_kb` veld
- ✅ Qdrant count gebruikt voor `chunks` display
- ✅ Admin-bypass op `_get_kb_with_owner_check`
- ✅ Instellingen-tab zichtbaar voor admin + read-only voor niet-owners
- ✅ Derde tab routeert naar `/taxonomy` met danger zone onderaan (wordt nu verbouwd per Q9)

**Openstaand (na Q&A 2026-05-11):**

Bronnen-tab:
- ⏳ Per-upload prullenbak altijd zichtbaar + `InlineDeleteConfirm` (Q2, REQ-15)
- ⏳ "Verbind opnieuw"-knop in connector-rij bij `not_synced + auth_error` (Q4)
- ⏳ Upload reindex-knop per rij + uitbreiding van "Synchroniseer alles" naar uploads (Q5)
- ⏳ Permissie-gating per actie volgens Q6-matrix (viewer = read-only, contributor = beperkt)

Backend:
- ⏳ Endpoint `POST /api/app/knowledge-bases/{slug}/uploads/{artifact_id}/reindex` (Q5)
- ⏳ `index_status` veld op artifact-model + status-update wanneer reindex draait (Q5)
- ⏳ Knowledge-ingest: re-enqueue route voor één artifact via bestaande enrichment-pipeline (Q5)
- ⏳ Contributor delete-check: `artifact.created_by == caller.user_id` voor uploads (Q6b)
- ⏳ Viewer-gate: bronnen tonen wel, alle muteer-endpoints 403 voor role=viewer (Q6)

Instellingen-tab (Q9 reorg):
- ⏳ Vier secties: Algemeen, Toegang, Bronnen-structuur (collapsible), Verwijderen — in deze volgorde (REQ-25)
- ⏳ Taxonomie-UI inbouwen als sub-component van `settings.tsx` (huidige `taxonomy.tsx` opbreken of importeren), kb_manager-gated
- ⏳ Toegang-sectie: ledenmanagement (toevoegen / rol wijzigen / verwijderen) verhuist vanuit `members.tsx`
- ⏳ Danger zone naar Instellingen verplaatsen (uit `taxonomy.tsx`); `DeleteKbModal` blijft als enige modal-uitzondering
- ⏳ Route `/advanced` deprecaten; redirect naar `/settings` met scroll-anchor

Inzichten-tab (nieuw):
- ⏳ Nieuwe route `routes/app/knowledge/$kbSlug/insights.tsx`
- ⏳ Sync-historie sub-sectie: collapsible per connector, laatste 10 runs uit `connector.sync_runs` (Q8)
- ⏳ Tab-label "Inzichten" in `route.tsx` TAB_DEFS; legacy redirects `?tab=taxonomy|advanced` → `/settings`
- ⏳ Permissie-gate: owner + admin (verbergen voor contributor + viewer)

Tests:
- ⏳ E2E Playwright happy-path: load KB → sync alles → wacht → bekijk chunks → delete bron
- ⏳ Permissie-test per rol: viewer ziet geen knoppen, contributor kan alleen eigen uploads weggooien, owner kan alles, en zien-tab-of-niet matcht Q6-matrix
- ⏳ Integration-test: stats-summary `bronnen` count == `/sources` count voor dezelfde KB
- ⏳ Legacy URL redirect tests: `?tab=advanced` en `?tab=taxonomy` landen op `/settings`

## Risico's

- **Status-mapping drift.** Connector `last_sync_status` is een open string. Nieuwe waarden (b.v. `paused`) mapt onbedoeld naar `not_synced`. Mitigatie: log onverwachte waarden in `mapStatus` zodat we het zien.
- **Polling-cost.** 4s poll terwijl pending is, vermenigvuldigd over open sessies, kan portal-api hits opdrijven. Mitigatie: stop polling wanneer geen pending; tab niet zichtbaar (`document.hidden`) zou een later optimization zijn.
- **Bronnen-count diverentie tussen list en detail.** Stats-summary telt via `count_sources_per_kb`; detail rendert via `list_kb_sources`. Beide queries op dezelfde tabel + filters, maar kunnen door RLS-context-verschil afwijken. Mitigatie: één integration-test die op één test-KB beide endpoints raakt en de count vergelijkt.
- **Admin-bypass blast-radius.** REQ-16 geeft platform-admins toegang tot connector-CRUD op elke KB in hun tenant. Acceptabel per portal-security.md (admin = tenant-level superuser), maar logging op admin-acties is essentieel.

## Beantwoorde vragen

**Q1 — Tab-volgorde + namen** (2026-05-11)
Bevestigd: **Bronnen** (default) → **Instellingen** → **Inzichten**. Geen extra tabs.

*Iteratie 2026-05-11 (laat in de Q&A):* "Geavanceerd" hernoemd naar **Inzichten** omdat taxonomie als collapsible sectie verhuist naar Instellingen. Inzichten wordt daardoor een echte info-tab (sync-historie + toekomstige diagnostics).

**Q2 — Upload verwijderen UX** (2026-05-11)
Prullenbak-icoon **altijd zichtbaar** in de rij, met `InlineDeleteConfirm` ("Verwijder '{naam}'? — Annuleren / Verwijder"). Geen hover-only, geen aparte modal. Updates REQ-15.

**Q3 — Upload hernoemen** (2026-05-11)
**Nee.** Bestandsnaam blijft staan zoals geüpload. Geen `display_name` veld, geen PATCH-endpoint voor artifact rename.

**Q4 — Connector auth-fout recovery** (2026-05-11)
**Aparte "Verbind opnieuw"-knop in de rij**, alleen zichtbaar als de connector status `not_synced` heeft met reden `auth_error`. Eén klik start de OAuth re-authorize flow direct (hergebruik `authorize_url` pattern uit `connectors.tsx::handleReconnect`). De knop staat tussen StatusBadge en sync-icoon. Sync-icoon blijft zichtbaar maar disabled zolang `auth_error` actief is — eerst opnieuw verbinden, dan syncen.

**Q6 — Permissie-matrix** (2026-05-11)

| Rol | Bron toevoegen | Per-bron sync / reindex | Bron verwijderen | Sync alles | Instellingen bewerken | Bronnen-structuur (taxonomie) zien | Inzichten zien |
|---|---|---|---|---|---|---|---|
| **Viewer** | nee | nee | nee | nee | nee | nee | nee |
| **Contributor** | ja | ja | alleen eigen uploads | ja | nee | nee | nee |
| **Owner** | ja | ja | ja | ja | ja | ja | ja |
| **Admin** (platform) | ja | ja | ja | ja | ja | ja | ja |

Viewer is volledig read-only: ziet de bronnenlijst, ziet de inhoud van bronnen, kan niets muteren. Contributor mag bronnen toevoegen en syncen, mag alleen z'n eigen uploads verwijderen — andermans uploads en alle connectors blijven beschermd voor owner+admin. Contributor ziet wel de Instellingen-tab (read-only: naam, beschrijving, ledenlijst) maar niet de Bronnen-structuur-sectie en niet de Inzichten-tab.

Owner-check op het backend: per-row delete moet `artifact.created_by == caller.user_id` valideren voor contributors. Voor connectors blijft `_get_kb_with_owner_check` de gate.

**Q7 — Empty state op Bronnen-tab** (2026-05-11)
Huidige patroon **houden**: gestippeld vlak met icoon + tekst + "Eerste bron toevoegen"-knop. Geen mini-onboarding-kaartjes, geen extreem minimale variant. Voldoet aan portal-patterns Empty States.

**Q8 — Sync-historie** (2026-05-11, geüpdatet na Q9)
**Belangrijkste content van de Inzichten-tab**: per connector de laatste N sync-runs (status / start-time / duur / fout-reden indien failed). Data komt uit `connector.sync_runs` (al gepopuleerd door klai-connector). Uploads hebben geen sync-historie — alleen connectors. Updates REQ-27.

**Q9 — Geavanceerd hernoemen + taxonomie verhuizen** (2026-05-11)
"Geavanceerd" hernoemd naar **Inzichten**. Taxonomie verhuist als collapsible sectie "Bronnen-structuur" naar Instellingen (sectie 3, default ingeklapt, kb_manager-gated). Inzichten wordt daardoor een pure info-tab zonder mutaties. Splitst de eerdere overlap tussen "instellen" en "geavanceerd" netjes op: alle config in Instellingen, alle info in Inzichten.

Concreet:
- Nieuw endpoint of hergebruik van bestaande `/connectors/{id}/syncs?limit=N` (zie `connectors.tsx::sync_runs_query`)
- UI: collapsible per connector met laatste 10 runs, default ingeklapt
- Fout-reden: korte stringuit `sync_run.error_message` afgekapt; volledige stack via "Toon detail"
- Onderaan elke run-rij: relatieve tijd ("2 uur geleden", "3 dagen geleden")

**Q5 — "Synchroniseer alles" scope** (2026-05-11)
**Alles betekent alles.** De knop synct connectors EN re-indexeert uploads. Voor uploads: chunks opnieuw embedden via de bestaande embedding-pipeline (TEI + bge-m3) en in Qdrant zetten. Per upload-bron krijgt de rij ook een individuele sync-knop met dezelfde semantiek (re-index).

Extra werk:
- Nieuw endpoint: `POST /api/app/knowledge-bases/{slug}/uploads/{artifact_id}/reindex`
- Knowledge-ingest: re-enqueue-route die de bestaande enrichment-pipeline triggert voor één artifact, hergebruik `extra_payload` semantiek (zie `knowledge.md` § "Procrastinate enrichment passthrough")
- Status-mapping: upload tijdens re-index → `pending`; daarna terug naar `synced`
- Polling-loop (REQ-9/10) dekt beide bron-typen.

## Open vragen
