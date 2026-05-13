---
id: SPEC-PORTAL-KB-OWNERSHIP-001
version: "0.1.0"
status: draft
created: "2026-05-12"
updated: "2026-05-12"
author: Mark Vletter (sparring met Claude)
priority: high
issue_number: 0
related:
  - SPEC-INFRA-TENANT-DELETE-001 (org-level delete pattern + state machine)
  - SPEC-PORTAL-RBAC-REFACTOR-001 (5-layer permissions + ProfileRole.ADMIN)
  - SPEC-SEC-TENANT-001 (tenant-scoped membership delete in offboard)
  - SPEC-KB-SOURCES-001 (KB-source wire-contract)
---

## HISTORY

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 0.1.0 | 2026-05-12 | Mark + Claude | Initiële draft. Drie onderdelen: (a) admin-delete van org-KBs van anderen met expliciete bevestiging, (b) offboarding-flow met KB-transfer wizard, (c) hard-firewall rond persoonlijke KBs. |

# SPEC-PORTAL-KB-OWNERSHIP-001 — Admin KB-delete, offboarding-transfer en personal-KB firewall

## Doel

Drie aan elkaar geknoopte verbeteringen rond eigendom van knowledge bases:

1. **Admin-delete van org-KBs** — Een org-admin kan elke org-eigendom KB binnen
   zijn tenant verwijderen, ook als hij hem niet zelf heeft aangemaakt. De UI
   waarschuwt expliciet "deze heb jij niet opgericht" en vraagt om bevestiging.
2. **Offboarding-transfer** — Bij `POST /admin/users/{id}/offboard` worden de
   org-KBs die de offboarded user als enige owner heeft expliciet overgedragen
   naar een andere user (default = de offboardende admin) of gewist. Geen
   silent-orphans meer.
3. **Personal-KB firewall** — Een admin krijgt persoonlijke KBs van anderen
   onder geen enkele rol of route te zien, te lezen, te delen, te
   herstellen of te verwijderen. De huidige model-discriminator
   (`owner_type='user'` + `owner_user_id`) wordt op meerdere lagen
   afgedwongen (DB-policy + API-filter + audit-test), zodat een toekomstige
   refactor de firewall niet per ongeluk omver kan trekken.

## Motivatie

### Wat er nu fout gaat

Op 2026-05-12 meldde de eigenaar van Klai dat hij als org-admin geen KBs van
collega's kon verwijderen. Vooral relevant nu meerdere medewerkers org-KBs
aanmaken en niet meer zelf opruimen — de admin is de enige die een rommelige
lijst kan rechtbreien, maar krijgt nu standaard `403 Owner access required`.

Tegelijk is de offboarding-flow (`offboard_user` in
`klai-portal/backend/app/api/admin/users.py`) KB-blind: een offboarded user
laat al zijn KBs achter met `created_by=<dode-zitadel-id>`. De
`get_user_role_for_kb`-resolver geeft `created_by` automatisch owner-rol —
dus de KB heeft nu een owner die niet meer bestaat. Niemand kan er nog mee.
Voor org-KBs is dit ook een hygiene-probleem (slug bezet, naam zichtbaar in
admin-UI). Voor persoonlijke KBs is dit een privacy-probleem (data blijft
hangen, zelfs als de medewerker is vertrokken).

### Wat er goed is en wat we NIET willen breken

`list_app_knowledge_bases` heeft al de cruciale filter:

```python
# klai-portal/backend/app/api/app_knowledge_bases.py:340-342
(PortalKnowledgeBase.owner_type == "org") | (PortalKnowledgeBase.owner_user_id == perms.user_id)
```

Dit verbergt persoonlijke KBs van anderen voor élke caller, ongeacht rol —
inclusief admins. Die invariant is precies wat we willen versterken, niet
versoepelen.

### Wat industry-standaard is (Google Workspace, Notion, Slack)

| Platform | Bij user-removal | Wie kan privé-content (My Drive / Private pages) bereiken? |
|---|---|---|
| Google Workspace | Admin krijgt prompt om Drive-files over te dragen vóór delete. Niet-overgedragen files zitten 5 dagen in Trash. | Admin kan "Transfer Drive files" via Admin Console — direct ownership-overdracht. Alleen "Service Settings administrator" privilege. Geen direct view. |
| Notion (Enterprise) | Bij removal prompt om "owned" pages over te dragen. Aparte "Content Transfer API" voor private pages. | Workspace owner kan binnen 30 dagen private pages reassignen — gated op Enterprise + 30d-window. Geen browse-access. Re-add binnen 30d herstelt private content. |
| Slack | Deactivation laat berichten/files staan. Owners kunnen canvases en lists overdragen. Primary Owner kan niet worden gedeactiveerd zonder eerst ownership over te dragen. | Niet — DM's en private channels blijven bij de account. Workspace exports (Plus/Enterprise) zijn een aparte gate. |

Synthese: **niemand laat persoonlijke content zonder expliciete intentie
verdwijnen, en niemand geeft admins ongelimiteerde toegang tot privé-content
"omdat ze admin zijn"**. Het patroon is altijd hetzelfde: owner-overdracht
vóór delete, korte restore-window, audit-trail, en een aparte gate voor
privé-data (Notion's Enterprise + 30d-window is de strengste, Google's
"Service Settings administrator" privilege de meest pragmatische).

We kopiëren niet één-op-één: Klai is voor een klein team (Voys, Klai-zelf,
early customers) en heeft geen tier waar we deze functionaliteit achter
kunnen verstoppen. We nemen daarom Google's pragmatische lijn voor
org-KBs (admin mag verwijderen na bevestiging) en Notion's strenge lijn
voor personal KBs (admin krijgt nooit toegang; max keuze "delete now of
restore-window dan delete"). Geen halve maatregelen, geen "admin kan
indien nodig stiekem alles" — dat zou onze eigen waardepropositie
("persoonlijk staat persoonlijk") ondermijnen.

## Environment

- **Affected services:** klai-portal/backend (KB-delete-endpoint, offboard-endpoint, nieuwe transfer-endpoint, audit-event), klai-portal/frontend (KB-card delete-modal, offboarding-wizard).
- **Externe systemen aangeraakt bij delete:**
  - docs-app (`docs_client.deprovision_kb`) — Qdrant vectors + Gitea repo + docs DB row, alleen voor KBs met `gitea_repo_slug`/`docs_enabled`.
  - knowledge-ingest (`knowledge_ingest_client.delete_kb`) — FalkorDB graph + Qdrant chunks + PG artifacts; altijd aangeroepen.
  - Geen wijziging in deze externe-call-volgorde — alleen wie de delete mag triggeren verandert.
- **Affected klai-portal backend files:**
  - `app/api/app_knowledge_bases.py` — splits `_require_owner` en de delete-handler in een owner-pad en een admin-override-pad; nieuwe `transfer_app_knowledge_base` endpoint.
  - `app/api/admin/users.py` — `offboard_user` krijgt verplichte body-param met KB-disposition per KB die de user owned.
  - `app/services/access.py` — `get_user_role_for_kb` blijft ongewijzigd; nieuwe helper `is_personal_kb(kb)` als single-source-of-truth voor de firewall.
  - `app/services/kb_offboarding.py` (NIEUW) — orchestrator die per KB transfer-of-delete uitvoert binnen één DB-transactie + emit audit-events.
  - `app/models/audit.py` of bestaande `log_event` — drie nieuwe action-codes: `kb.admin_deleted`, `kb.transferred`, `kb.personal_purged_on_offboard`.
  - `tests/test_app_knowledge_bases.py` — uitbreiding voor admin-override pad + personal-KB firewall tests (admin probeert toegang → 404).
  - `tests/test_user_lifecycle.py` — uitbreiding voor offboarding-met-KB-disposition.
  - `tests/test_kb_personal_firewall.py` (NIEUW) — gefocused op invariant: geen enkele admin-route kan een personal KB van een ander zien/wijzigen/verwijderen.
- **Affected klai-portal frontend files:**
  - `frontend/src/components/knowledge/delete-kb-modal.tsx` (UITGEBREID) — varianten "you-own" en "you-don't-own"; tweede variant toont aanmaker, datum, en "Type DELETE om te bevestigen".
  - `frontend/src/routes/admin/users/$user/offboard.tsx` (NIEUW of uitbreiding van bestaande offboard-actie) — wizard met KB-overdracht-stappen.
  - `frontend/messages/{nl,en}.json` — Paraglide strings.
- **Affected docs:**
  - `docs/runbooks/user-offboarding.md` (NIEUW) — admin-runbook met KB-transfer-stappen + restore-window.
  - `klai-portal/CLAUDE.md` (UITGEBREID) — verwijzing naar runbook.

## Assumptions

- Org-admin = `ProfileRole.ADMIN` (bestaande RBAC-laag uit
  SPEC-PORTAL-RBAC-REFACTOR-001). Geen nieuwe rol nodig.
- Een KB hoort bij precies één org (`portal_knowledge_bases.org_id`). Cross-org
  transfer is uit scope.
- Owner-discriminator is `owner_type` + `owner_user_id` (zoals nu). Migratie
  van data-shape is uit scope.
- Restoration-window voor personal KBs is opt-in via een nieuwe org-instelling
  (default uit). MVP levert "delete now" en "delete after N dagen"; de
  re-activate-pad uit Notion is opgenomen als open vraag (Q3) en kan in een
  Phase 2 SPEC.
- `ProfileRole.ADMIN` bestaat per tenant — er is geen super-admin die
  cross-tenant kan overschrijden. Dat blijft zo.

## Probleem-mapping

| # | Probleem | Bewijs | Impact |
|---|---|---|---|
| P1 | Admin krijgt 403 bij delete van org-KB die collega heeft aangemaakt | `_require_owner` in `app_knowledge_bases.py:305-313` checkt alleen `role == "owner"` zonder admin-override | Rommelige KB-lijst blijft hangen, admin moet gebruiker verzoeken om delete of via DB ingrijpen |
| P2 | `remove_user`/`offboard_user` raken KBs niet aan | `app/api/admin/users.py:410-565` — geen enkele query op `portal_knowledge_bases` of `portal_user_kb_access` | Orphan-KBs (`created_by=<dode-id>`) blijven; geen owner kan ze nog wijzigen, niemand merkt het tot iemand wil delete'n |
| P3 | Personal-KB-filter is alleen op één plek geïmplementeerd | `list_app_knowledge_bases` heeft de OR-filter; andere endpoints (get_kb, members, sources) checken alleen `org_id == perms.org_id` en vertrouwen impliciet op de slug-pad | Een toekomstige refactor die `_get_kb_or_404` aanpast kan stilletjes admin-toegang tot persoonlijke KBs introduceren — geen test bewaakt dat |
| P4 | Geen audit-trail bij admin-overrides | `log_event` wordt niet aangeroepen in `delete_app_knowledge_base` | Bij security-incident is niet reproduceerbaar wie wat verwijderde |
| P5 | Geen restore-window voor offboarded user die per ongeluk weg is | `offboard_user` flipt status maar er is geen "restore"-pad voor zijn data | Voys-praktijk: medewerker stopt na 6 weken alsnog niet, alle context is weg |

## Gewenste eindtoestand

### 1. Admin-delete van org-KB

```
[user clicks Delete on org-KB they didn't create]
  ↓
DELETE /api/app/knowledge-bases/{slug}
  - actor: admin
  - kb.owner_type: 'org'
  - kb.created_by: someone-else
  - actor's role for this KB: not 'owner', but actor is ProfileRole.ADMIN
  ↓
Backend: branch into admin-override pad
  - Require explicit X-Admin-Override-Confirm: 'I-WAS-NOT-CREATOR' header
    (UI sends this only after the second confirmation modal)
  - Emit log_event(action='kb.admin_deleted', actor, kb_id, previous_owner=created_by)
  - Continue with same docs-app + knowledge-ingest + portal-DB delete chain
```

UI:
- KB-card "verwijder" button is altijd zichtbaar voor admins op org-KBs.
- Eerste klik opent normale "weet je zeker"-modal.
- Als `kb.created_by != actor.user_id` toont de modal een gele banner:
  "Deze KB is aangemaakt door {name}. Je verwijdert content van een
  collega. Type **DELETE** om te bevestigen."
- Pas na DELETE-typ + klik wordt de extra header meegestuurd.

### 2. Offboarding-transfer

```
[admin clicks Offboard on user X]
  ↓
GET /api/admin/users/{X}/offboard-preview
  → returns { org_kbs_owned_solely: [...], personal_kbs: [...] }
  ↓
UI: wizard
  - Stap 1: per org-KB die X solely owned → kies (transfer_to: <user-id>) of (delete: true)
            Default-selectie: transfer naar caller (de offboardende admin)
  - Stap 2: voor personal KBs → kies (delete_now) of (purge_after_days: N) — geen transfer-optie
  - Stap 3: bevestiging
  ↓
POST /api/admin/users/{X}/offboard
  body: { kb_dispositions: [{kb_id, action: 'transfer'|'delete', transfer_to?: user_id}, ...] }
  ↓
Backend:
  - Run kb_offboarding.apply_dispositions() in één transactie
    - transfer: update kb.created_by + kb.owner_user_id (if user-owned, but user-owned won't get here)
    - delete (org-KB): same chain as DELETE endpoint, with log_event(action='kb.admin_deleted', reason='offboarding')
    - delete (personal): docs/ingest delete + log_event(action='kb.personal_purged_on_offboard')
  - Continue with existing offboard logic (status='offboarded', remove memberships, deactivate Zitadel, etc.)
```

Org-KBs waar X als één-van-meerdere owners stond verliezen alleen X's
`portal_user_kb_access`-rij; geen disposition nodig (KB heeft nog
owners). Org-KBs waar X de enige owner was verschijnen in de wizard.

### 3. Personal-KB firewall

Drie lagen die elkaar onafhankelijk afdwingen:

**Laag A — single source of truth helper:**
```python
def is_personal_kb(kb: PortalKnowledgeBase) -> bool:
    return kb.owner_type == "user"
```

**Laag B — gate in elke KB-route:**
Iedere route die een specifieke KB ophaalt
(`get_app_knowledge_base`, `update_knowledge_base`, `delete_app_knowledge_base`,
`list_kb_bronnen`, `list_members`, `invite_user`, `update_user_role`,
`remove_user` op KB-niveau, `invite_group`, `crawl_preview`, `auth_probe`,
en alle endpoints in `app_knowledge_sources.py` met `kb_slug`-param) draait
direct na `_get_kb_or_404`:

```python
if is_personal_kb(kb) and kb.owner_user_id != perms.user_id:
    raise HTTPException(404, "Knowledge base not found")  # 404, niet 403 — bestaan niet onthullen
```

Wordt geïmplementeerd als FastAPI-dependency `get_kb_with_access` die alle
bovenstaande routes vervangen, in plaats van per-route copy-paste.

**Laag C — invariant-test:**
`tests/test_kb_personal_firewall.py` itereert over élke KB-route
(geïntrospecteerd via `app.routes`) en bevestigt: als een route met
`{kb_slug}` of `{kb_id}` een personal-KB van een andere user als param
krijgt, krijgt de admin-caller 404 (niet 200, niet 403). Een nieuwe route
die de gate vergeet, faalt deze test.

Optioneel als Phase 2 (uit scope MVP): laag D — DB row-level-security
policy die `owner_user_id = current_setting('app.current_user_id')`
afdwingt op `portal_knowledge_bases` met `owner_type='user'`. Heeft
dezelfde architectuur-vorm als de bestaande `tenant_isolation`-policy
op `portal_users`. Pas zinvol als laag B een keer faalt.

## Requirements (EARS)

### Admin-delete (REQ-1.x)

- **REQ-1.1** WHEN een caller met `ProfileRole.ADMIN` `DELETE
  /api/app/knowledge-bases/{slug}` aanroept op een org-KB (`owner_type='org'`)
  binnen zijn eigen tenant en `kb.created_by != caller.user_id`, EN de
  request bevat header `X-Admin-Override-Confirm: I-WAS-NOT-CREATOR`,
  THEN voert de backend de delete uit volgens dezelfde 3-stap-keten als
  bij een owner-delete (docs → ingest → portal-DB).
- **REQ-1.2** WHEN dezelfde caller dezelfde DELETE aanroept ZONDER de
  header, THEN antwoordt de backend met `403` en body
  `{"detail": "Owner access required, or set X-Admin-Override-Confirm header"}`.
- **REQ-1.3** WHEN een caller met `ProfileRole.ADMIN` `DELETE
  /api/app/knowledge-bases/{slug}` aanroept op een **personal** KB van
  een andere user (`owner_type='user'` AND `owner_user_id != caller`),
  THEN antwoordt de backend met `404` ongeacht of er een
  override-header staat. De personal-KB firewall heeft voorrang.
- **REQ-1.4** WHEN een delete via admin-override succesvol is, THEN
  emit de backend `log_event(action='kb.admin_deleted',
  actor=caller.user_id, resource_type='kb', resource_id=kb.id,
  meta={previous_owner: kb.created_by, kb_name: kb.name, kb_slug: kb.slug})`.
- **REQ-1.5** WHEN de admin-override delete loopt, THEN gedraagt de
  externe-call-keten (docs-app deprovision, ingest delete, portal-DB
  delete) zich identiek aan de owner-delete: failures in stap 1 of 2
  aborten vóór de portal-DB-rij verdwijnt. Geen verschil in
  failure-semantiek tussen owner en admin.

### Offboarding-transfer (REQ-2.x)

- **REQ-2.1** WHEN een caller met `ProfileRole.ADMIN` `GET
  /api/admin/users/{zitadel_user_id}/offboard-preview` aanroept,
  THEN antwoordt de backend met JSON `{org_kbs_solely_owned:
  [{kb_id, slug, name, role_count}, ...], personal_kbs: [{kb_id,
  slug, name}, ...]}`. Alleen org-KBs waar de target-user de enige
  resterende owner is, en alle personal KBs van die user, staan in
  de lijst.
- **REQ-2.2** WHEN de admin `POST /api/admin/users/{zitadel_user_id}/offboard`
  aanroept met body `{kb_dispositions: [...]}` waarin elke `kb_id` uit
  REQ-2.1 voorkomt met `action='transfer'` (org-KB only) of
  `action='delete'`, THEN voert de backend de dispositions uit binnen
  één DB-transactie vóór de bestaande offboard-stappen (status-flip,
  membership-delete, Zitadel-deactivate). Failure in een disposition
  rolt alles terug — de user wordt niet offboarded.
- **REQ-2.3** WHEN een disposition `action='transfer'` heeft op een
  org-KB met `transfer_to=<new-user-id>`, THEN update de backend
  `kb.created_by = new_user_id`. Bestaande
  `portal_user_kb_access`-rijen voor de oude user worden verwijderd.
  Een nieuwe `portal_user_kb_access`-rij `(kb_id, new_user_id,
  role='owner', granted_by=actor)` wordt geüpsert.
- **REQ-2.4** WHEN een disposition `action='transfer'` voorkomt voor
  een personal KB, THEN antwoordt de backend met `400` en body
  `{"detail": "Personal knowledge bases cannot be transferred to
  another person"}`. De firewall-invariant geldt ook hier.
- **REQ-2.5** WHEN de offboard-call zonder body of zonder dispositions
  voor élke KB uit REQ-2.1 binnenkomt, THEN antwoordt de backend met
  `400` en body `{"detail": "Missing dispositions for: [<kb_slug>,
  ...]"}`. Geen impliciete defaults — admin moet expliciet kiezen om
  silent-orphans te voorkomen.
- **REQ-2.6** WHEN een org-KB transfer succesvol is, THEN emit
  `log_event(action='kb.transferred', actor, resource_type='kb',
  resource_id=kb.id, meta={from_user: old, to_user: new,
  reason: 'offboarding'})`. WHEN een delete uit offboarding succesvol
  is, THEN emit `kb.admin_deleted` (org) of
  `kb.personal_purged_on_offboard` (personal) met
  `meta.reason='offboarding'`.

### Personal-KB firewall (REQ-3.x)

- **REQ-3.1** Een centrale FastAPI-dependency `get_kb_with_access(kb_slug)`
  vervangt het patroon `kb = await _get_kb_or_404(...)` in alle huidige
  KB-routes onder `/api/app/knowledge-bases/{kb_slug}/...`. De dependency
  resolvet de KB en raised `HTTPException(404)` (NIET 403) WHEN
  `is_personal_kb(kb) AND kb.owner_user_id != caller.user_id`, ongeacht
  caller-rol.
- **REQ-3.2** WHEN een caller (inclusief `ProfileRole.ADMIN`) een
  personal-KB-slug van een andere user gebruikt op ÉLKE bestaande of
  toekomstige `/api/app/knowledge-bases/{kb_slug}/*` route, THEN
  antwoordt de route `404` zonder enige info over de KB te onthullen
  (geen naam, geen aantal sources, geen creator).
- **REQ-3.3** Een test
  `tests/test_kb_personal_firewall.py::test_no_admin_route_leaks_personal_kb`
  itereert via `app.routes` over élke route waarvan het pad-pattern
  `{kb_slug}` of `{kb_id}` bevat. Voor élke route maakt de test (a)
  een personal KB van user A en (b) een admin-caller van een andere
  user B (zelfde tenant). De test verwacht response `404` op élke
  route — falen hierop is een blocker.
- **REQ-3.4** `list_app_knowledge_bases` blijft de bestaande
  OR-filter `(owner_type='org') OR (owner_user_id=caller)` behouden.
  Een test bevestigt: een admin ziet géén personal-KB's van anderen
  in de response, ongeacht een nieuwe optionele query-param of
  filter-flag.
- **REQ-3.5** Geen enkele bestaande of nieuwe route biedt een
  "view-as-admin" / "switch-context" / "impersonate" pad richting
  personal-KB content. WHEN een toekomstige SPEC zo'n pad voorstelt,
  THEN moet die SPEC dit SPEC-document expliciet noemen als
  conflict + de firewall met een additionele laag (laag D, RLS-
  policy) backen.

### Audit + observability (REQ-4.x)

- **REQ-4.1** Alle nieuwe `log_event`-calls hierboven landen in de
  bestaande audit-tabel, queryable per `actor` en `resource_id` (zelfde
  patroon als `user.offboarded`).
- **REQ-4.2** Backend logt `_slog.info("kb_admin_deleted", ...)`,
  `_slog.info("kb_transferred", ...)` en
  `_slog.info("kb_personal_purged_on_offboard", ...)` met velden
  `org_id`, `actor_user_id`, `kb_id`, `kb_slug`, `previous_owner`. Deze
  events zijn in VictoriaLogs queryable als
  `service:portal-api AND event:kb_admin_deleted` (zie
  `klai/projects/portal-logging-py.md`).
- **REQ-4.3** Een Grafana-alert (Phase 2 — niet in MVP-scope) op
  `kb_personal_purged_on_offboard`-rate > 0 voor 5 min: standaard uit,
  alleen activeren als personal-KB-purge een gevoeligheid wordt.

## Acceptance criteria

### AC-1: admin-delete owner-pad blijft werken
- **Given** user A heeft org-KB X aangemaakt en is owner
- **When** A `DELETE /api/app/knowledge-bases/X` zonder override-header
- **Then** response 204; KB weg uit docs/ingest/portal-DB

### AC-2: admin-delete van org-KB van anderen mét override
- **Given** user A heeft org-KB X aangemaakt; user B is admin in zelfde tenant; B is geen owner van X
- **When** B `DELETE /api/app/knowledge-bases/X` mét header `X-Admin-Override-Confirm: I-WAS-NOT-CREATOR`
- **Then** response 204; audit-event `kb.admin_deleted` met `previous_owner=A`; KB weg uit alle backends

### AC-3: admin-delete zonder override blijft 403
- **Given** zoals AC-2
- **When** B `DELETE /api/app/knowledge-bases/X` zonder header
- **Then** response 403; KB onaangeraakt; geen audit-event

### AC-4: admin-delete van personal KB van anderen → 404
- **Given** user A heeft personal KB; user B is admin in zelfde tenant
- **When** B `DELETE /api/app/knowledge-bases/personal-{A}` mét override-header
- **Then** response 404; KB onaangeraakt; geen audit-event

### AC-5: offboard-preview toont enkel solely-owned org-KBs en alle personal KBs
- **Given** user X owned solely org-KB Y (geen andere owner-rij), is co-owner van org-KB Z (samen met admin), heeft personal KB P
- **When** admin `GET /api/admin/users/{X}/offboard-preview`
- **Then** response bevat Y en P; bevat NIET Z

### AC-6: offboard zonder dispositions → 400 met expliciete missing-list
- **Given** preview uit AC-5
- **When** admin `POST /api/admin/users/{X}/offboard` met lege body
- **Then** response 400, body bevat `Y` en `P` in `Missing dispositions`

### AC-7: offboard-transfer voor org-KB werkt + audit
- **Given** zoals AC-5
- **When** admin POST met `{kb_dispositions: [{kb_id: Y.id, action: 'transfer', transfer_to: admin.id}, {kb_id: P.id, action: 'delete'}]}`
- **Then** response 200; Y.created_by = admin; admin heeft owner-rij; P weg uit docs/ingest/portal-DB; user X status='offboarded'; audit-events `kb.transferred`, `kb.personal_purged_on_offboard`, `user.offboarded`

### AC-8: transfer van personal KB → 400
- **Given** zoals AC-5
- **When** admin POST met `{kb_dispositions: [{kb_id: P.id, action: 'transfer', transfer_to: admin.id}, ...]}`
- **Then** response 400 met `Personal knowledge bases cannot be transferred`

### AC-9: invariant-firewall test groen
- `pytest tests/test_kb_personal_firewall.py -v` slaagt voor élke geïntrospecteerde KB-route

### AC-10: failure tijdens offboarding-transfer rolt alles terug
- **Given** AC-5; mock `knowledge_ingest_client.delete_kb` om te raisen op P
- **When** admin POST disposition zoals AC-7
- **Then** response 5xx; user X status NOG `active`; Y.created_by NIET veranderd; geen audit-events

### AC-11: front-end "Type DELETE" gate
- **Given** admin opent UI voor org-KB van anderen
- **When** admin klikt op delete in KB-card
- **Then** modal toont gele banner met aanmaker-naam en datum, knop is disabled tot user "DELETE" typt

## Out of scope (MVP)

- **Reactivate-pad voor offboarded user.** Eigenaarsbeslissing: nee
  (D3). `suspend_user` blijft de niet-destructieve weg. Niet uit te
  bouwen in een latere SPEC tenzij produktbeleid verandert.
- **Soft-delete grace-period voor personal KBs.** Eigenaarsbeslissing:
  delete = onmiddellijk (D2). Geen `status` of `purge_after` kolommen
  nodig in `portal_knowledge_bases`.
- **DB-laag RLS-policy** voor personal KB firewall (laag D in §3). Pas zinvol als laag B een keer faalt.
- **Cross-org KB-transfer.** Klai heeft per definitie tenant-isolatie; cross-org transfer zou dat ondergraven.
- **Bulk-admin-delete** (selecteer 5 KBs en delete). Eerst single-KB pad solide, dan bulk.
- **Soft-delete-tombstones bij admin-override.** `PortalKBTombstone` bestaat al maar wordt nu niet gebruikt door `delete_app_knowledge_base`. Kan in Phase 2.
- **Self-service voor de offboarded user** ("ik wil mijn personal KB exporteren"). Aparte SPEC.
- **Auto-revoke OAuth grants en provider-tokens** (Notion / Google Drive / Microsoft connectors die een offboarded user heeft aangemaakt op org-KBs). Andersoortige hygiene; eigen SPEC. Maar zie REQ-2.x — bij KB-transfer naar nieuwe owner moeten connector-credentials wel onder de nieuwe owner draaien (mogelijk een impliciete dependency).

## Bronnen voor de beslissingen

- Google Workspace ownership-transfer best practices ([gpanel.io](https://gpanel.io/blog/google-workspace-offboarding), [Google Workspace Help](https://support.google.com/a/answer/1247799?hl=en))
- GDPR retention proportionality ([heydata.eu](https://heydata.eu/en/magazine/gdpr-data-retention-periods-overview-requirements-best-practices/), [TechTarget](https://www.techtarget.com/searchdatabackup/tip/Compare-SaaS-data-retention-policies-from-4-major-providers))
- Reactivation patterns ([Slack reactivate](https://slack.com/help/articles/360002061747-Reactivate-a-members-account), [Notion 30d](https://www.notion.com/help/duplicate-delete-and-restore-content), [Torii reactivate Google](https://www.toriihq.com/articles/how-to-reactivate-user-google-workspace))
- DELETE confirmation pattern ([piranna.github.io](https://piranna.github.io/2020/03/01/Confirm-deletion-in-RESTful-APIs/), [http.dev DELETE](https://http.dev/delete))
- 91% offboarded-token-survival statistic en checklist ([Reco SaaS offboarding](https://www.reco.ai/use-cases/saas-offboarding), [Nudge Security 2026](https://www.nudgesecurity.com/post/nudge-securitys-it-offboarding-checklist-for-a-saas-first-world))

## Beslissingen (industry-research backed)

Op 2026-05-12 doorlopen tegen industry standards (Google Workspace, Notion,
Slack, GDPR-best-practice, IT-offboarding-checklists). Bronnen onderaan dit
document. Onderstaande zijn ingebakken in de requirements hierboven; staan
hier gedocumenteerd voor de audit-trail.

### D1 — Default-ontvanger bij offboard-transfer = de offboardende admin
Google Workspace recommendeert "direct manager OR a dedicated archive
account" als default. Klai heeft geen archief-account; de offboardende
admin is ook bijna altijd de manager / verantwoordelijke. **Default:
`transfer_to = caller.user_id`** met UI-banner "Klik op de KB om een
andere ontvanger te kiezen". Files krijgen een naam-prefix
"ex-{user-naam} / {original-name}" (mirror van Google's gedrag waarbij
overgedragen files in een folder met de oude eigenaar's email landen —
provenance behouden).

### D2 — Personal-KB delete = onmiddellijk (geen grace-period)
Onderzoek wees op 30d als industry-default (Microsoft, Google, Notion),
maar productbeslissing van de eigenaar (2026-05-12): **delete now, geen
grace-period**. Past bij Klai's positionering "persoonlijk staat
persoonlijk" — een offboarded user zou bij een grace-period nog 30 dagen
in een schemertoestand zitten waarin de admin theoretisch het account
kan reactivaten en alsnog bij privé-data kan. Onmiddellijke purge
elimineert dat venster. GDPR-compliant (sterker: GDPR-strenger dan
nodig) en simpeler te implementeren.

### D3 — Geen restore-on-rehire pad
Productbeslissing van de eigenaar (2026-05-12): **nee**. Een
gereactivateerde medewerker krijgt een schoon personal-KB; oude
inhoud is bij offboard onmiddellijk verwijderd (D2). Wie zeker wil
zijn dat data niet weg is, gebruikt `suspend_user` (bestaande
endpoint, behoudt alles tot reactivate). `offboard_user` is en
blijft destructief en irreversibel. Klant-communicatie hierover hoort
in de offboard-confirmation modal: "Deze actie verwijdert
onomkeerbaar de persoonlijke kennisbank van {user}. Suspend de
gebruiker in plaats daarvan als je twijfelt."

### D4 — Override-mechaniek = HTTP-header
Industry-best-practice voor destructieve confirmation tokens is
"headers or query params, NOT body" (DELETE-body semantiek is
ongedefinieerd, sommige proxies droppen 'm). Headers worden in de
geciteerde RESTful-bronnen "the most canonical option" genoemd.
Klai heeft al een precedent: `I-CONFIRM-REMOVAL` in
`klai-infra/sync-env.yml` (zie process-rules.md
`sync-env-removal-needs-explicit-confirmation`). **Beslissing:**
`X-Admin-Override-Confirm: I-WAS-NOT-CREATOR` zoals in REQ-1.1.

### D5 — API-tokens en MCP-tokens worden auto-gerevoked bij offboard
Onderzoek wees uit: **91% van offboarded-user-tokens blijft actief
na delete** (Reco / Nudge Security 2026 onderzoek). Dit is een
gedocumenteerd anti-pattern, geen edge-case. Klai heeft twee
relevante endpoints:

- `klai-portal/backend/app/api/admin_api_keys.py` — admin-kanaal
  API-keys (per-user grants); helpers `_get_key_or_404`, `delete_api_key`.
- `klai-portal/backend/app/api/me_mcp_tokens.py` — per-user MCP-tokens;
  helper `revoke_my_token`.

**Beslissing:** uitbreiden van REQ-2.x met REQ-2.7 (auto-revoke alle
api-keys en MCP-tokens van de offboarded user binnen dezelfde DB-tx
als de KB-dispositions). De preview-endpoint (REQ-2.1) telt en toont
het aantal tokens dat geraakt zal worden. Geen "manual cleanup banner"
— die past niet bij het industry-pattern.

## Toegevoegde requirements (per beslissingen)

- **REQ-2.1b** WHEN preview wordt opgehaald, THEN bevat de response
  ook `api_keys_count: int` en `mcp_tokens_count: int` voor de target-user.
- **REQ-2.7** WHEN offboard wordt uitgevoerd, THEN revoket de backend
  binnen dezelfde DB-transactie (via expliciete `delete()` queries)
  alle rijen in `portal_api_keys` met `created_by = target_user_id`
  én alle rijen in `portal_mcp_tokens` (of equivalent) met
  `user_id = target_user_id`. Failure rolt alles terug, inclusief
  KB-dispositions.
- **REQ-2.8** WHEN een personal-KB-disposition `action='delete'` is,
  THEN voert de backend de volledige docs/ingest/portal-DB delete
  onmiddellijk uit (zelfde 3-stap-keten als REQ-1.5). Geen soft-delete,
  geen grace-period, geen reactivate-pad. Klanten die data willen
  behouden gebruiken `suspend_user`.

## Restant-vragen

Geen. Alle 5 oorspronkelijke open vragen zijn gesloten via industry-
research + productbeslissing van de eigenaar (2026-05-12). De drie
verbeteringen — admin-delete, offboarding-transfer, personal-firewall
— zijn nu eenduidig gedefinieerd en MVP-scope is helder afgebakend.

## Migration / data-impact

- Geen DDL-wijzigingen — bestaand model is voldoende.
- Geen backfill — bestaande KBs zijn al correct gemodelleerd.
- **Geen orphan-KBs in productie vandaag** (eigenaar-bevestigd
  2026-05-12). De offboard-flow heeft historisch nog niemand
  doorgevoerd; deze SPEC voorkomt dat toekomstige offboards
  orphans achterlaten.

## Implementation hints (niet-bindend)

- Splits `_require_owner` niet — voeg een `_require_owner_or_admin_override`
  helper toe die expliciet de override-header vraagt en bij gebruik de
  audit-emit doet. Bestaande callers blijven `_require_owner` gebruiken.
- `kb_offboarding.apply_dispositions` als async functie die een
  `tenant_scoped_session` opent (zie
  `klai/projects/portal-backend.md` Pool-GUC pollution) en in één commit
  alle dispositions uitvoert. Externe calls (docs/ingest delete) buiten
  de DB-transactie maar binnen een `try/finally` met compensating
  transactions.
- Frontend: bestaande `delete-kb-modal.tsx` uitbreiden met een
  `mode: 'self' | 'admin-override'` prop in plaats van een nieuwe modal
  — voorkomt drift in tekst en design.
- Hard rule (per `.claude/rules/klai/portal-ui`): geen raw `<button>` of
  inline Tailwind in de gele banner of de "Type DELETE" input — gebruik
  `components/ui/`.

## Risico's

- **R1 — Admin verwijdert per ongeluk een KB van het hele team.** Mitigatie:
  twee-staps modal + verplicht typen van DELETE + audit-event. Verlies
  blijft mogelijk maar minder waarschijnlijk dan via DB-ingrijpen vandaag.
- **R2 — Frontend stuurt header per ongeluk altijd.** Mitigatie: header
  alleen toegevoegd in de `admin-override`-modal-variant, met een
  `data-test-id` zodat een unit test bevestigt dat de owner-pad-modal
  hem nooit set.
- **R3 — Personal-firewall regression bij refactor.** Mitigatie:
  REQ-3.3 invariant-test draait op élke PR, faalt hard als een nieuwe
  KB-route de gate vergeet. Phase 2 voegt RLS-laag toe.
- **R4 — Offboarding stuck op extern systeem (docs-app of ingest down).**
  Mitigatie: dezelfde semantiek als de bestaande owner-delete — failure
  abort vóór portal-DB-mutatie. Admin krijgt foutmelding en kan
  retry'en. User wordt niet half-offboarded.
- **R5 — Disposition-transfer op org-KB met X als enige owner én X als
  laatste user in de org.** Edge case: admin offboardt zichzelf en is
  enige user. Bestaande `leave_workspace` en
  `_lock_org_for_role_change` handelen tenant-leegloop al — dit SPEC
  verandert daar niets aan; transfer faalt expliciet ("can't transfer
  to non-existent receiver") wat de admin dwingt eerst de tenant te
  deprovisionen via SPEC-INFRA-TENANT-DELETE-001.

## Niet-MVP follow-ups (preview)

- Bulk-admin-delete (selecteer 5 KBs).
- Tombstone gebruik bij admin-override (slug-reuse audit-trail).
- Restore-on-rehire window voor personal KBs.
- DB-laag RLS-policy als laag D van de personal-firewall.
- Self-service personal-KB-export voor de offboarded user (download).
- API-token / MCP-key cleanup als onderdeel van offboarding.
- Grafana-alert op `kb_personal_purged_on_offboard`-rate.
