---
id: SPEC-KNOWLEDGE-ESCALATION-001
version: "0.4.0"
status: in aanbouw
created: 2026-09-29
updated: 2026-09-29
author: Claude (Opus 5.5), commissioned by Mark Vletter
priority: high
tenant_scope: platform-wide — elke tenant met de `knowledge_activity`-unlock en een geconfigureerde widget
related:
  - SPEC-KNOWLEDGE-ACTIVITY-001 (de beoordelingsroute waar deze knop in komt)
  - docs/architecture/support-gap-detection.md (servicekey-onderzoek, lees-connector `hubspot_support`)
  - SPEC-VOYS-HELPBOT-001 §4 (live-overdracht naar de support-partner; staat hier los van, zie §6)
---

# HISTORY

| Versie | Datum | Wijziging |
|---|---|---|
| 0.4.0 | 2026-09-30 | Volledige versie terug, gebouwd vóór de scopes er zijn: contact aanmaken, bedrijfskoppeling en het accountnummer zelf ophalen. Elk onderdeel dat een ontbrekende scope vraagt degradeert zichtbaar per ticket (status plus de scope die ontbreekt); het ticket zelf komt er altijd. Volledige scopelijst in §2.5. |
| 0.3.0 | 2026-09-29 | Scope per endpoint nagekeken in de HubSpot-docs (29 sep): Tickets-API accepteert `crm.objects.tickets.write`, Contacts-API `crm.objects.contacts.read`, Pipelines-API één van 94 scopes waaronder `crm.objects.contacts.read`. Account-info vraagt `oauth`, dat niet op de key staat, dus dat endpoint vervalt: de admin vult het HubSpot-account-ID zelf in en de key wordt gecontroleerd door de pipelines op te halen. |
| 0.2.0 | 2026-09-29 | Teruggebracht tot de scopes op de bestaande servicekey. 0.1.0 vroeg er twee bij (contacten aanmaken, bedrijven lezen) zonder dat tegen die key te leggen. Nu: geen contact aanmaken (onbekende bezoeker = ticket zonder koppeling, gegevens bovenaan de inhoud) en geen bedrijf. |
| 0.1.0 | 2026-09-29 | Eerste versie na drie feedbackrondes met Mark: één knop "Maak ticket" in de beoordeling, geen knop zonder e-mailadres, de notitie uit de beoordeling gaat mee in plaats van een eigen tekstveld. Gebouwd in twee lanes (backend, frontend) tegen het contract in §4. |

# 1. Aanleiding

Een beoordelaar op `/app/knowledge/activity/$conversationId` ziet regelmatig
een gesprek waarin de bezoeker iets van Sales of Finance nodig heeft: een
prijsvraag, een uitbreiding, een factuur. De chat kan dat niet afhandelen en
het gesprek verdwijnt na de retentietermijn. De beoordelaar moet het met één
knop als ticket bij het juiste team in HubSpot kunnen neerleggen, met genoeg
informatie om zonder Klai verder te kunnen.

# 2. Besluiten

1. **Eén knop "Maak ticket" in het beoordelingsformulier.** De knop slaat eerst
   de beoordeling op en opent dan een inline paneel (geen modal, geen drawer):
   team kiezen, zien of de bezoeker een bestaand HubSpot-contact is,
   bevestigen.
2. **Geen e-mailadres, geen knop.** Zonder `widget_conversations.visitor_email`
   weten we niets van de klant en verschijnt het ticketblok niet. Ook niet bij
   een testgesprek of als de widget geen ticketkoppeling heeft.
3. **Geen eigen tekstveld.** De notities uit de beoordelingen van dit gesprek
   gaan mee als eerste blok van het ticket.
4. **Doelen zijn configureerbaar per widget** (label, pipeline, stage), niet
   hard "Sales" en "Finance": dat zijn Voys-teams, dit is een platformfunctie.
   De configuratie staat op het Integraties-tabblad van de widget, naast de
   afspraak-URL, omdat de admin daar het kanaal beheert.
5. **Scopes.** Nodig voor de volledige functie: `crm.objects.tickets.write`
   (ticket aanmaken), `crm.objects.contacts.read` (contact zoeken, pipelines),
   `crm.objects.contacts.write` (onbekende bezoeker als contact aanmaken),
   `crm.objects.companies.read` (bedrijf van het contact vinden en koppelen)
   en, als een servicekey hem kan krijgen, `oauth` (accountnummer en
   HubSpot-omgeving zelf ophalen via account-info). De functie werkt ook
   zonder de laatste drie: elk onderdeel dat een ontbrekende scope raakt
   (HubSpot 403) vervalt voor dat ticket, en het ticket laat zien welk
   onderdeel en welke scope. Dat is geen stille fallback: de status staat
   per ticket in Klai. Zelfde key als de gap-detectie, die daarnaast
   `crm.objects.tickets.read`, `conversations.read`, `sales-email-read` en
   `crm.objects.owners.read` gebruikt of zal gebruiken.
6. **Contact vinden op e-mailadres, anders aanmaken.** Geen treffer → contact
   aanmaken met e-mailadres en naam (gesplitst op de eerste spatie); HubSpot
   409 `CONTACT_EXISTS` → het bestaande id uit de fout. Mag de key dat niet
   (403), dan komt het ticket zonder contact en begint de inhoud met "Niet
   gevonden in HubSpot: <naam> · <e-mailadres>". Voor een bestaand contact
   wordt het eerste gekoppelde bedrijf opgezocht en aan het ticket gekoppeld;
   bedrijven maken we nooit aan.
7. **Levenscyclusfase tonen zoals HubSpot hem geeft.** Of Voys "klant" in
   HubSpot bijhoudt is niet bevestigd; het paneel toont de fase als die er is
   en anders alleen "bestaand contact".
8. **Privacy volgt SPEC-KNOWLEDGE-ACTIVITY-001 §3.** Een kb_manager mag het
   ticket aanmaken en ziet "bestaand contact" of "nieuw contact" plus de
   levenscyclusfase; naam van het contact en bedrijfsnaam alleen voor admins.
   Het e-mailadres gaat server-side naar HubSpot en komt nooit in een
   kenniskant-respons.
9. **Buiten scope:** de judge laat geen escalatie voorstellen, geen automatische
   tickets, geen eigenaar of prioriteit (dat regelen de pipelines in HubSpot).

# 3. Ticketinhoud

Platte tekst in de ticketproperty `content`, onderwerp in `subject`:

```
Onderwerp:  Webchat: <eerste bezoekersvraag, max 100 tekens>

Notities van de beoordelaar
  <per beoordeelde beurt met notitie: naam beoordelaar, datum, notitie>

Bezoeker
  <naam als bekend> · <e-mailadres>

Gesprek
  <startdatum en -tijd> · <widgetnaam> · <taal>
  Beoordeling: <slechtste oordeel> · <oorzaak>

  <tijd>  Bezoeker  <tekst>
  <tijd>  Klai      <tekst>
                    Bronnen: <titel> (<url>), …

Bekijk in Klai (beschikbaar tot <datum>): <portal-url>/app/knowledge/activity/<id>
```

Niet in het ticket: judge-oordeel, zekerheidsband, `answer_signals`. Het ticket
wordt gekoppeld aan het (gevonden of aangemaakte) contact en aan het bedrijf
van een bestaand contact. Kon er geen contact worden aangemaakt, dan begint de
inhoud met de regel "Niet gevonden in HubSpot: <naam> · <e-mailadres>".

# 4. Contract

## 4.1 Tabellen (klai-owned, Cat-D RLS, marker-revisie + post-deploy SQL zoals `answer_reviews`)

`widget_ticket_settings` — één rij per widget met een ticketkoppeling.

| Kolom | Type |
|---|---|
| `widget_id` | uuid pk, FK widgets ON DELETE CASCADE |
| `org_id` | int, FK portal_orgs ON DELETE CASCADE |
| `service_key_encrypted` | bytea (`portal_secrets.encrypt`) |
| `hubspot_portal_id` | bigint (uit account-info als de key dat mag, anders door de admin ingevuld) |
| `hubspot_ui_domain` | text, nullable (uit account-info, bv. `app-eu1.hubspot.com`; null = `app.hubspot.com`) |
| `targets` | jsonb: `[{"key","label","pipeline_id","stage_id"}]`, 1–5 items, `key` uniek slug `^[a-z0-9_-]{1,32}$` |
| `updated_at` | timestamptz |
| `updated_by_user_id` | int, FK portal_users SET NULL |

`conversation_tickets` — één rij per aangemaakt (of mislukt) ticket.

| Kolom | Type |
|---|---|
| `id` | bigint pk |
| `org_id` | int, FK portal_orgs CASCADE |
| `conversation_id` | bigint, FK widget_conversations **SET NULL** (overleeft de purge) |
| `target_key`, `target_label` | text (label is een snapshot) |
| `status` | text CHECK `pending`/`created`/`failed` |
| `hubspot_ticket_id`, `hubspot_contact_id` | text, nullable |
| `contact_status` | text CHECK `existing`/`created`/`create_forbidden`, nullable |
| `company_status` | text CHECK `linked`/`none`/`forbidden`, nullable |
| `ticket_url` | text, nullable |
| `error` | text, nullable (korte, voor mensen leesbare reden) |
| `created_by_user_id` | int, FK portal_users SET NULL |
| `created_at`, `updated_at` | timestamptz |

UNIQUE (`conversation_id`, `target_key`) WHERE `conversation_id IS NOT NULL`.

## 4.2 Admin-API (router `admin_widgets.py`, zelfde gates als de bestaande `/integrations/hubspot`-routes)

- `GET /api/admin/widgets/{widget_id}/integrations/tickets` →
  `{configured: bool, hubspot_portal_id: int|null, portal_id_source: "hubspot"|"manual"|null, targets: Target[]}`.
  Geeft de key nooit terug.
- `PUT /api/admin/widgets/{widget_id}/integrations/tickets` body
  `{service_key?: string, hubspot_portal_id?: int, targets: Target[]}` → zelfde vorm als GET.
  De server roept eerst account-info aan: lukt dat, dan komen portal-id en
  ui-domein van HubSpot (`portal_id_source = "hubspot"`), en wijkt een
  meegestuurd id af → 422 `detail: "account_mismatch"`. Geeft account-info 403,
  dan is het meegestuurde id verplicht (`portal_id_source = "manual"`,
  ui-domein null), anders 422 `detail: "portal_id_required"`.
  `service_key` weglaten = bestaande key houden (422 als er nog geen is).
  De server verifieert de key door `GET /crm/v3/pipelines/tickets` op te
  halen en controleert dat elke pipeline en stage bestaat. HubSpot 401 → 422 `detail: "invalid_service_key"`, 403 → 422
  `detail: "missing_scope"`, onbekende pipeline/stage → 422
  `detail: "unknown_pipeline_or_stage"`.
- `DELETE /api/admin/widgets/{widget_id}/integrations/tickets` → 204.
- `POST /api/admin/widgets/{widget_id}/integrations/tickets/pipelines` body
  `{service_key?: string}` → `[{id, label, stages: [{id, label}]}]`. POST omdat
  de key in de body zit; zonder key gebruikt hij de opgeslagen key.

`Target = {key: string, label: string, pipeline_id: string, stage_id: string}`

## 4.3 Kenniskant-API (router `app_activity.py`, zelfde gates als de rest)

- `GET /api/app/activity/conversations/{id}` krijgt een veld
  `ticket: {available: bool, targets: [{key, label}], tickets: TicketOut[]}`.
  `available` = widget heeft ticketinstellingen én `visitor_email` is niet leeg
  én het gesprek is geen test. `tickets` staat er altijd (historie).
- `GET /api/app/activity/conversations/{id}/ticket-preview` →
  `{contact: "existing"|"new", lifecycle_stage: string|null, contact_name: string|null, company_name: string|null}`.
  `contact_name` en `company_name` alleen voor admins, anders null.
  `company_name` is null als er geen bedrijf is of de key geen bedrijven mag lezen. 409
  `detail: "ticket_unavailable"` als `available` false is. HubSpot-fout → 502.
- `POST /api/app/activity/conversations/{id}/tickets` body `{target_key}` →
  201 `TicketOut`. Bestaat er al een `created`-rij voor dit doel → 409 met
  `detail: "ticket_exists"`. Een `failed`- of blijven hangende `pending`-rij
  wordt hergebruikt (opnieuw proberen). HubSpot-fout → rij `failed` met reden,
  respons 502 met `detail` = die reden.
- Lijst `GET /api/app/activity/conversations`: item krijgt
  `ticket_labels: string[]` (labels van `created`-tickets), query-param
  `has_ticket: bool | null`.

`TicketOut = {target_key, target_label, status, ticket_url: string|null, contact_status: string|null, company_status: string|null, error: string|null, created_by_name: string|null, created_at}`

## 4.4 Volgorde bij aanmaken

1. Gesprek hoort bij de org, is geen test, heeft een e-mailadres, widget heeft
   instellingen, doel bestaat.
2. Rij `pending` vastleggen (unique index voorkomt dubbele tickets) en
   committen. Geen HubSpot-call binnen een databasetransactie.
3. Contact zoeken: e-mail in kleine letters, filtergroepen `email EQ` óf
   `hs_additional_emails CONTAINS_TOKEN`; alleen een contact dat het exacte
   adres draagt telt, hoofdadres wint.
4. Geen contact → aanmaken (`created`); 403 → `create_forbidden`, verder
   zonder contact.
5. Bestaand contact → eerste gekoppelde bedrijf ophalen (`linked`, of `none`
   als er geen is). Een nieuw aangemaakt contact heeft nog geen bedrijf
   (`none`).
6. Ticket aanmaken met `subject`, `content`, `hs_pipeline`,
   `hs_pipeline_stage` en associaties naar contact en bedrijf. Weigert HubSpot
   met 403 terwijl er een bedrijfsassociatie in zat, dan één keer opnieuw
   zonder bedrijf (`company_status = forbidden`).
7. Rij bijwerken naar `created` met ids en `ticket_url`
   (`https://<ui_domain of app.hubspot.com>/contacts/<portal_id>/record/0-5/<ticket_id>`),
   of naar `failed` met de reden.

# 5. Interface

Beoordelingsformulier, alleen als `ticket.available`:

- Knop "Maak ticket" naast "Opslaan". Klik = beoordeling opslaan, dan paneel
  open. Ongeldig formulier = knop uit, net als Opslaan.
- Paneel: doelknoppen (doelen met een `created`-ticket vallen weg), regel met
  het contact uit `ticket-preview` (laadt bij openen), "Ticket aanmaken" en
  "Annuleren".
- Onder het formulier per ticket: "✓ Bij Sales · datum · door naam · Openen in
  HubSpot ↗", of bij `failed` de reden en "Opnieuw proberen". Bij
  `create_forbidden` of `company_status = forbidden` een regel die zegt welk
  onderdeel niet lukte en welke scope daarvoor ontbreekt.
- Gesprekkenlijst: badge per ticketlabel, filter "met ticket / zonder ticket".
- Admin, Integraties-tab van de widget: kaart "Tickets in HubSpot" met
  servicekey (wachtwoordveld, leeg = behouden), HubSpot-account-ID (optioneel; alleen nodig als de key account-info niet mag), "Pipelines ophalen", per doel
  label + pipeline + stage, doel toevoegen/verwijderen, opslaan, koppeling
  verwijderen.

# 6. Verhouding tot SPEC-VOYS-HELPBOT-001

Die SPEC zegt dat Voys niet naar HubSpot escaleert. Dat gaat over de
live-overdracht van een bezoeker naar Klai's eigen HubSpot-inbox en blijft
waar. Deze SPEC maakt tickets in het HubSpot-account van de tenant zelf, met
de servicekey van de tenant, nadat een collega het gesprek heeft beoordeeld.
