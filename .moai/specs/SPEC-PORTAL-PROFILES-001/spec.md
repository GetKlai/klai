---
id: SPEC-PORTAL-PROFILES-001
version: "0.5.0"
status: phase-3-in-progress
created: 2026-05-03
updated: 2026-05-03
author: Mark Vletter
priority: high
related:
  - SPEC-PORTAL-UNIFY-KB-001 (capabilities + plan_limits foundation)
  - SPEC-AUTH-008 (group memberships, system_groups)
---

# SPEC-PORTAL-PROFILES-001: User Profiles & Tenant Add-Ons

## HISTORY

| Date | Version | Change |
|------|---------|--------|
| 2026-05-03 | 0.1.0 | Initial draft after sparring session. Five-rung profile ladder (Personal chat → Company chat → Knowledge manager → Group manager → Admin) replaces the current `admin / group-admin / member` triplet. Plan and role decoupled: Company chat and Knowledge manager share a billing tier but differ in role. Scribe and Docs become per-tenant add-on toggles, not Plan-bound products. Knowledge access for Personal chat is hard-gated: no `default_org_role` fallback, ever. |
| 2026-05-03 | 0.2.0 | Capability-laag versimpeld na review van Phase-1 PR. `PROFILE_CAPABILITIES` bevat alleen capabilities die op endpoints via `require_capability(...)` worden gechecked: `kb.connectors`, `kb.connectors.external`, `kb.create_org`, `kb.members`, `kb.taxonomy`, `kb.gaps`. Verwijderd: `kb.read_org`, `kb.append_via_chat`, `groups.manage`, `groups.invite_users`, `org.billing`, `org.settings` — die worden directe rol-checks via `_require_at_least` of `_require_admin`. Connector-allowlist via twee capability-gates ipv aparte rol-tabel. `effective_capabilities = role_caps ∩ plan_caps` is nu de canonieke werking. |
| 2026-05-03 | 0.3.0 | Phase 1 merged via PR #274 (squash commit `b6397735`). Phase 1.6 cleanup-scope toegevoegd om de code "industry-standard" te maken vóór Phase 2/3: (1) Capability-strings worden typed via `Capability` Literal/StrEnum in `app/core/profiles.py` zodat pyright typos vangt. (2) `PROFILE_LADDER` wordt aangevuld met `PROFILE_RANK: dict[str,int]` voor O(1) ladder-lookups; legacy `.index()` calls worden vervangen. (3) `portal_users.role` wordt gemigreerd van `VARCHAR(20) + CHECK constraint` naar een echte Postgres `ENUM` type voor type-safety op DB-niveau. (4) Dood `require_at_least_dep` wordt verwijderd uit `dependencies.py` — de legacy helpers in `groups.py` blijven want ze hebben async system-group logica die niet in een Depends-decorator past. (5) Admin-bypass in `get_effective_capabilities` krijgt een expliciet `# @MX:NOTE` block met SPEC-referentie en de keuzeratio (admin moet upgrade-effecten kunnen testen zonder billing-wijziging). |
| 2026-05-03 | 0.4.0 | Phase 1.6 merged via PR #277 (squash commit `0d90fa5e`). Phase 2 (backend add-ons) en Phase 3 (frontend gating) worden in twee opeenvolgende PRs uitgerold. Phase 2 levert: nieuwe kolom `portal_orgs.enabled_addons text[] NOT NULL DEFAULT '{}'`, `PATCH /api/admin/settings/addons` endpoint, `docs` als eigen product (afgesplitst van `knowledge`), `require_product` wordt twee-laags voor add-ons (tenant `enabled_addons` ∩ user/group `portal_user_products`), system-groups herinrichten (oude obsolete groups opruimen, vijf rol-bind groups + twee add-on groups registreren). Phase 3 levert: sidebar/admin gating per rol, profile-picker op user-edit, KB-tab gating per `effective_role`, add-on toggles op `/admin/settings`, `/app/docs` route gated op `docs`-product, connectors-list gefilterd voor personal/company. |
| 2026-05-03 | 0.5.0 | Phase 2 merged via PR #282 (squash commit `e5d1a813`). Phase 3 frontend implementatie. Sidebar `/app/route.tsx` krijgt rol-aware filtering: nav-items met `minRole` worden verborgen onder de ondergrens; `/app/docs` mapt nu naar product `docs` (was `knowledge`). Admin-tak `/admin/route.tsx` krijgt per-tab gating: `/admin/groups` zichtbaar voor `group_manager+`, `/admin/templates` voor `kb_manager+`, rest admin-only. KB-detail tabs (connectors, members, taxonomy, advanced) worden grijs voor sub-`kb_manager` rollen. `/admin/users/{id}/edit` krijgt een Profile-radio met de vijf rungen + bijhorende beschrijvingen; submit gaat via bestaande `PATCH /api/admin/users/{id}/role`. `/admin/settings` krijgt een "Add-ons" sectie met checkboxes voor scribe en docs, sync via nieuwe `PATCH /api/admin/settings/addons`. Connectors-list bij KB-source-toevoegen filtert types tegen `effective_capabilities` (alleen url/upload voor personal/company). Hooks: `useEffectiveRole`, `useEffectiveCapabilities`, `useEnabledAddons`. i18n via Paraglide voor alle nieuwe UI-strings (NL + EN). |

---

## Summary

The current portal has three roles (`admin`, `group-admin`, `member`) and three products (`chat`, `scribe`, `knowledge`). That model conflates two orthogonal axes — what a tenant pays for vs. what an individual user is allowed to do — and offers no way to give one user "personal chat with their own KB only" while a colleague in the same org gets "build company knowledge with all connectors". To make Klai production-ready for non-uniform tenants we introduce:

1. A **five-rung profile ladder** that replaces the role enum:
   **Personal chat → Company chat → Knowledge manager → Group manager → Admin**.
   Each rung is a strict superset of the rung below (its description starts with *"Everything in [previous], plus..."*).
2. A **plan ≠ role** decoupling. A single billing plan can host multiple roles. The org pays for "Company-tier"; the admin then assigns each user a profile within that tier.
3. **Scribe and Docs become per-tenant add-on toggles** — `portal_orgs.enabled_addons text[]`. Default empty (off). The admin enables them per tenant; assignment to users still goes through the existing `portal_user_products` / `portal_group_products` mechanism.
4. **Connector allowlist per role**: Personal chat and Company chat may use only `url` + `upload` connector types; Knowledge manager and above unlock the external connectors (Notion, Google Drive, Microsoft 365, Confluence, GitHub, Airtable, …).
5. **Hard gating of org-KB read for Personal chat**. Today `default_org_role=viewer` on KBs implicitly grants every user in the org read access. For Personal chat that fallback is suppressed at query time.

The frontend label set (Engels):

| Rung | Label | Description |
|------|-------|-------------|
| 1 | Personal chat | Full chat features. Manage personal knowledge — upload documents, add web links, and run inquiries on up to 5 of your own knowledge bases. |
| 2 | Company chat | Everything in Personal chat, plus inquire on and contribute to company knowledge — read every team knowledge base and add documents or links through the chat. |
| 3 | Knowledge manager | Everything in Company chat, plus manage company knowledge — connect Notion, Google Drive, M365, Confluence, GitHub and more, with member management, taxonomy, and gap detection. |
| 4 | Group manager | Everything in Knowledge manager, plus manage groups and access — decide who's in which group and which knowledge bases they can use. No user invitations, billing, or settings. |
| 5 | Admin | Everything in Group manager, plus organisation administration — users, billing, settings, domains, integrations, API keys, and templates. |

---

## Motivation

1. **Production blocker for heterogeneous tenants.** A typical mid-size customer wants secretaries on Personal chat (eigen werk, geen org-KB), helpdesk on Company chat (mag alle bedrijfskennis raadplegen + tijdens chat aanvullen), a small redaction team on Knowledge manager (verbindt Notion + Drive, beheert taxonomie), one or two Group managers (verdelen toegang tussen teams), and one Admin per org. Today every member is implicitly equal — there is no way to keep a Personal chat user out of the org-KB.
2. **Plan ↔ feature mismatch.** SPEC-PORTAL-UNIFY-KB-001 already gates connectors/members/taxonomy/gaps via plan-level capabilities, but plan is a billing axis, not a per-user axis. A `complete`-plan tenant should still be able to give some users a connector-less profile. Today they cannot.
3. **Alpha discipline.** Scribe (production stable for some) and Docs (alpha) are bundled together under the `knowledge` product flag. There is no way to enable Docs without enabling Scribe, no way to keep a tenant on the Scribe-only plan, and no way to dark-launch new add-ons without surfacing them to every tenant.
4. **Group management lives in two places.** A `group-admin` enum value on `portal_users.role` AND a per-membership `is_group_admin` boolean both exist, both check group-management rights. Behaviour drifts between them. We collapse them into one Group manager role on the ladder.

---

## Scope

### In scope

**Backend — role ladder**

- `portal_users.role` enum uitbreiden:
  ```
  'personal' | 'company' | 'kb_manager' | 'group_manager' | 'admin'
  ```
  Verlies van `member` en `group-admin` enum-waarden.
- `app/api/dependencies.py`:
  - `_require_admin` blijft (Admin-only endpoints).
  - Nieuwe ladder-helpers: `_require_at_least(role)` met ladder-volgorde definieert ondergrens.
  - `_require_admin_or_group_admin*` opheffen, vervangen door `_require_at_least("group_manager")`.
  - `require_product` blijft (gebruikt voor add-on producten Scribe/Docs).
  - `require_capability` blijft (gebruikt voor fijnmazige KB-capabilities).
- Nieuwe module `app/core/profiles.py`:
  - `PROFILE_LADDER: list[str]` (machine-leesbare volgorde)
  - `PROFILE_CAPABILITIES: dict[str, frozenset[str]]` (per-rol effective capabilities)
  - `effective_role(user) → str` helper

**Backend — capability remap**

Capability-strings worden alléén bewaard voor checks die via `require_capability(...)` op endpoints lopen. Alles wat al via een directe rol-check te doen is (org-KB read filter, append-via-chat, groups beheren, billing, settings) krijgt geen capability-string maar een rechtstreekse `_require_at_least(...)` of `_require_admin(...)` op de route.

**Capability-string set** (gechecked via `require_capability`, gehandhaafd op endpoints):

| Capability | In `PROFILE_CAPABILITIES` | In `PLAN_LIMITS.capabilities` |
|---|---|---|
| `kb.connectors` (basis: url/upload) | personal, company, kb_manager, group_manager, admin | core, professional, complete |
| `kb.connectors.external` (overige types) | kb_manager, group_manager, admin | complete |
| `kb.create_org` | kb_manager, group_manager, admin | complete |
| `kb.members` | kb_manager, group_manager, admin | complete |
| `kb.taxonomy` | kb_manager, group_manager, admin | complete |
| `kb.gaps` | kb_manager, group_manager, admin | complete |

**Effective capabilities** op runtime:

```
effective_capabilities(user) = PROFILE_CAPABILITIES[role] ∩ PLAN_LIMITS[plan].capabilities
```

Plan blijft ceiling, profiel blijft floor. Een `kb_manager` op een `core`-plan krijgt alleen wat `core` toelaat (basis `kb.connectors`); een `personal` op `complete`-plan krijgt alleen wat `personal` toelaat (basis `kb.connectors`). Admin behoudt de bestaande complete-tier bypass uit SPEC-PORTAL-UNIFY-KB-001 — een admin op een goedkoper plan krijgt alle kb-capabilities (rationaal: admin moet kunnen testen wat een upgrade oplevert; bewuste keuze).

**Directe rol-checks** (geen capability-string):

| Wat | Hoe |
|---|---|
| Personal hard gate op org-KB | `get_accessible_kb_slugs` filtert wanneer `user.role == "personal"` |
| Append-to-org-KB via chat | Endpoint check `_require_at_least("company")` op append-flow |
| Groups beheren | `_require_at_least("group_manager")` op `/api/admin/groups/*` |
| Users invite, billing, settings, domains, api-keys, widgets, MCPs, templates | `_require_admin` op de overige `/api/admin/*` |

Plan-tier blijft de ceiling op rol-toekenning: een `core`-plan bedrijf mag geen `kb_manager`-rol toekennen aan zijn users. Nieuw: per-rol-per-plan toelaatbaarheid in `app/core/profiles.py::ALLOWED_PROFILES_PER_PLAN`.

**Backend — connector allowlist**

De connector-type check loopt via twee capability-checks op het `POST /api/connectors` endpoint:

1. `require_capability("kb.connectors")` — mag de user überhaupt connectors creëren? (basis-gate, plan + rol)
2. Indien `connector_type ∉ {"url", "upload"}`: `require_capability("kb.connectors.external")` — mag de user externe types? (extra-gate, plan + rol)

Geen aparte rol-tabel of `tier_minimum` op adapter-registratie nodig — de capabilities dragen de gating.

**Backend — KB quota**

- [`PLAN_LIMITS`](klai-portal/backend/app/core/plan_limits.py) wordt vervangen door of uitgebreid met `PROFILE_LIMITS`:
  ```python
  PROFILE_LIMITS["personal"]   = (max_kbs=5, max_items_per_kb=20)
  PROFILE_LIMITS["company"]    = (max_kbs=5, max_items_per_kb=20)
  PROFILE_LIMITS["kb_manager"] = (max_kbs=None, max_items_per_kb=None)   # unlimited
  PROFILE_LIMITS["group_manager"] = same as kb_manager
  PROFILE_LIMITS["admin"]      = same as kb_manager
  ```
  Plan-tier kan dit nog steeds verlagen (Mark beslist later of `complete`-plan upgrade ook iemand met rol `personal` onbeperkt maakt — voorkeur: nee, profiel wint).

**Backend — org-KB read filter (kritisch voor Personal chat)**

- [`get_accessible_kb_slugs`](klai-portal/backend/app/services/access.py#L92-L146) filtert het `default_org_role`-blok weg wanneer caller's rol = `personal`. Effect: Personal chat-user ziet géén org-KBs in haar slug-lijst, ongeacht de KB-instellingen.
- `personal-{user_id}` blijft. `org` slug verdwijnt voor `personal` (daar zat impliciet het org-niveau in).

**Backend — add-on toggles per tenant**

- Nieuwe kolom `portal_orgs.enabled_addons text[] NOT NULL DEFAULT '{}'`.
- Nieuwe endpoint `PATCH /api/admin/settings/addons` (admin-only) die deze kolom muteert.
- `require_product("scribe")` checkt voortaan twee dingen: (a) tenant heeft `scribe` in `enabled_addons` AND (b) caller heeft `scribe` in zijn effective products via user/group toekenning.
- `docs` wordt **een eigen product** (apart van `knowledge`) en aan dezelfde toggle-/entitlement-pijp gehangen.

**Backend — group manager collapse**

- `portal_users.role = 'group-admin'` migratie → `'group_manager'`.
- `portal_group_memberships.is_group_admin` blijft bestaan voor *per-group* delegated admin (een Knowledge manager die toevallig admin is van zijn eigen team-group). Het is niet langer een ladder-rol, maar een per-membership boolean.

**Frontend — sidebar gating**

- `routes/app/route.tsx`: `PRODUCT_ROUTES` mapping wordt `ROLE_OR_PRODUCT_ROUTES`. Items vereisen of een rol-ondergrens of een product-flag:
  ```ts
  '/app/chat': { minRole: 'personal' },
  '/app/transcribe': { product: 'scribe' },
  '/app/knowledge': { minRole: 'personal' },
  '/app/docs': { product: 'docs' },             // niet meer 'knowledge'
  ```
- `routes/admin/route.tsx`: `requireAdmin` blijft op `/admin`-root, maar individuele tabs krijgen ladder-checks:
  - `/admin/groups` → minRole `group_manager`
  - `/admin/templates` → minRole `kb_manager`
  - `/admin/users`, `/admin/billing`, `/admin/settings`, `/admin/domains`, `/admin/api-keys` → admin only

**Frontend — KB UI gating**

- Op KB-detail pages worden tabs grijs (zelfde patroon als SPEC-PORTAL-UNIFY-KB-001) op basis van `effective_role`:
  - `connectors.tsx` → minRole `kb_manager`
  - `members.tsx` → minRole `kb_manager`
  - `taxonomy.tsx` → minRole `kb_manager`
  - `advanced.tsx` → minRole `kb_manager`
- `routes/app/gaps/index.tsx` → minRole `kb_manager`
- KB source-toevoegen-flow: connectors-list filtert tegen `PROFILE_CAPABILITIES[role]`. Personal en Company users zien alleen URL + Upload.
- Voor `personal` gebruikers: org-KBs verdwijnen volledig uit overzichten (ze komen al niet uit de slugs-lijst van de backend).

**Frontend — admin user-edit (profile picker)**

- Op `/admin/users/{userId}/edit` komt een Profile-radio met de vijf rungen + de huidige beschrijving. Selectie zet `role` direct via bestaande `PATCH /api/admin/users/{id}/role` endpoint, dat krijgt een uitgebreide enum.
- Bestaand "Effective Products"-blok blijft (toont uniem van direct + group-inherited products).
- Nieuw blok "Add-on access" met checkboxes voor scribe en docs (alleen zichtbaar als tenant ze enabled heeft via `portal_orgs.enabled_addons`).

**Frontend — admin tenant settings**

- Nieuwe sectie op `/admin/settings`: "Add-ons". Twee toggles: Scribe, Docs. State sync met `enabled_addons` via nieuwe endpoint. Bij uitzetten worden bestaande user/group entitlements niet verwijderd — ze worden alleen niet effectief tot de toggle weer aan gaat.

**Backend — system groups herijken**

- `app/core/system_groups.py`: opruimen van obsolete groups ("Chat + Focus", "+ Scribe", "+ Knowledge + Docs"). Vervangen door rol-bind groups met `system_key`:
  - `personal` (toekent rol = personal)
  - `company` (toekent rol = company)
  - `kb_manager`
  - `group_manager`
  - `admin`
  - + product add-on groups: `scribe-users`, `docs-users` (toekennen alleen het product, geen rol).
- Add-membership in system-group → bijwerken van `portal_users.role`. Dat is nieuw gedrag (vandaag bewerken groups alleen products); we registreren een trigger of een service-laag.

**Migration — bestaande users**

Het systeem is nog niet in gebruik bij externe klanten. We kiezen het schoonste mapping-pad, niet het minst-disruptieve:

| Oude waarde | Nieuwe waarde |
|---|---|
| `admin` | `admin` |
| `group-admin` | `group_manager` |
| `member` | `personal` (default) |

`personal` als nieuwe default is in lijn met het principe dat toegang opt-in is: een admin verhoogt expliciet naar `company` / `kb_manager` waar nodig. Voor de handvol interne testaccounts is dat een paar klikken. Geen "veilig superset"-gymnastiek.

`portal_knowledge_bases.default_org_role` blijft bestaan voor Knowledge managers; het filter op rol zit in `get_accessible_kb_slugs`.

**Tests**

- Backend pytest-coverage ≥ 85% op `app/core/profiles.py`, `effective_role`, `_require_at_least`, en de role-aware paden in `access.py` en `app_knowledge_bases.py`.
- Karakteriseringstests voor de migration (oude member krijgt company, oude group-admin krijgt group_manager).
- Playwright smoke tests:
  - Personal-user flow: chat werkt, kan eigen KB maken met URL/Upload, ziet GEEN org-KBs in `/app/knowledge`, krijgt 403 op `/api/app/knowledge-bases-with-access` voor org-KBs.
  - Company-user flow: ziet org-KBs read-only, kan via chat document/URL toevoegen, ziet alleen URL/Upload bij source-toevoegen, ziet GEEN connectors-tab op org-KBs.
  - Knowledge-manager flow: ziet alle connector-types, kan org-KB maken, members/taxonomy/gaps werken.
  - Group-manager flow: ziet `/admin/groups`, kan members verschuiven, krijgt 403 op `/admin/billing`.
  - Admin flow: alles werkt zoals nu.
  - Add-on toggle flow: tenant met `enabled_addons=['scribe']` toont scribe-knop alleen voor users met scribe-product.

### Out of scope

- Billing flows en upgrade-funnel (volgt in `SPEC-BILLING-UPGRADE-001`).
- Self-service profile-switching door eindgebruikers (alleen admins kunnen profielen toekennen).
- Migratie van bestaande `is_group_admin` membership-booleans naar de nieuwe `group_manager` rol — beide blijven naast elkaar bestaan.
- Plan-tier herziening (`free`/`core`/`professional`/`complete`) — dat hoort bij de billing-SPEC.
- Alpha-tier als marketing-tag of intercom-segment — alleen de feature-mechaniek (`enabled_addons`) is in scope.
- Klai-docs (`klai-docs/` Next.js public reader) — alleen het portal-side editor-oppervlak (`/app/docs/*`) wordt onder een eigen `docs`-product gebracht.

---

## Acceptance Criteria (EARS)

### Profile ladder

- **REQ-1**: WHEN a user with role `personal` calls `GET /api/app/knowledge-bases-with-access`, THEN the response SHALL contain only KBs owned by that user (slug pattern `personal-{user_id}`), regardless of any KB's `default_org_role`.
- **REQ-2**: WHEN a user with role `company` calls the same endpoint, THEN the response SHALL contain personal KBs + every org-scoped KB they have read access to via `default_org_role` or explicit grants.
- **REQ-3**: WHILE a user has role `personal` or `company`, the system SHALL reject KB source-creation with `connector_type ∉ {'url','upload'}` with HTTP 403 and `error_code: 'connector_not_allowed_for_profile'`.
- **REQ-4**: WHILE a user has role `kb_manager`, `group_manager`, or `admin`, the system SHALL accept any registered connector type at KB source-creation.
- **REQ-5**: IF a user has role `personal` or `company` AND their personal KB count >= 5, THEN the system SHALL reject `POST /api/app/knowledge-bases` with HTTP 403 and the existing `kb.quota_blocked` event.
- **REQ-6**: WHEN a user with role `company` posts content via the chat-flow's "add to org KB" affordance, THEN the system SHALL accept the upload/link if the target KB grants them at least `viewer` role and append the document; users with role `personal` SHALL NOT see this affordance.
- **REQ-7**: WHEN a user with role `group_manager` calls any `/api/admin/groups/*` or `/api/admin/groups/{id}/members/*` endpoint, THEN the system SHALL accept it; calls to `/api/admin/users`, `/api/admin/billing`, `/api/admin/settings`, `/api/admin/domains` SHALL return 403.

### Add-on toggles

- **REQ-8**: WHEN an admin calls `PATCH /api/admin/settings/addons` with `{enabled_addons: ['scribe']}`, THEN `portal_orgs.enabled_addons` SHALL update and `GET /api/admin/settings/addons` SHALL reflect the change.
- **REQ-9**: WHILE `'scribe' ∉ portal_orgs.enabled_addons`, calls to scribe-protected endpoints by users in that org SHALL return 403 even when the user has `scribe` in their `portal_user_products` row.
- **REQ-10**: `'docs'` SHALL be a distinct product flag (not aliased to `'knowledge'`); routes under `/app/docs/*` SHALL gate on the `docs` product, not on `knowledge`.

### Migration

- **REQ-11**: WHEN the data-migration runs against the portal database, THEN existing role values SHALL map as follows: `admin → admin`, `group-admin → group_manager`, `member → personal`. The Alembic CHECK constraint SHALL be updated to permit only the five new enum values.

### UI

- **REQ-13**: WHEN an admin opens `/admin/users/{id}/edit`, the page SHALL render a profile-radio with the five rungs and their descriptions; selection SHALL persist via `PATCH /api/admin/users/{id}/role`.
- **REQ-14**: WHILE the tenant has `enabled_addons=[]`, the Add-on access checkboxes on `/admin/users/{id}/edit` SHALL render disabled with a tooltip referencing `/admin/settings`.
- **REQ-15**: WHEN a user with role `personal` lands on `/app`, the sidebar SHALL show only `/app/chat` and `/app/knowledge`; `/app/transcribe`, `/app/docs`, and any admin items SHALL be absent.

### Capabilities API contract

- **REQ-16**: `GET /api/me` (or the equivalent capability-resolution endpoint) SHALL return `effective_role` and `effective_capabilities` (set of capability strings) reflecting the user's role + the tenant's `enabled_addons`.

---

## Open Questions (decide before /run)

1. **Profile naming in DB enum** — `personal | company | kb_manager | group_manager | admin` is mijn voorkeur (kort, machine-friendly). Alternatief: `personal_chat | company_chat | knowledge_manager | group_manager | admin` (matcht UI-labels 1-op-1 maar verbose). Default = bovenste tot Mark anders aangeeft.
2. **Plan ↔ profile mapping**. Welke profielen mag een tenant op welk plan toekennen? Voorgesteld:
   ```
   free          → personal only
   core          → personal, company, group_manager, admin
   professional  → personal, company, group_manager, admin
   complete      → all five (kb_manager unlocked)
   ```
   Dat houdt `kb_manager` als de "complete-plan upgrade" voor wie connectors wil — Company tier kan vandaag al group/admin toekennen, maar geen connector-builder. Akkoord, of ander mapping?
3. **`is_group_admin` per-membership boolean** — laten staan voor delegated team-admins, of opheffen omdat Group manager nu een rol-tier is? Voorkeur: laten staan, maar geen frontend-UI voor toekennen totdat we een use-case zien.
4. **Profile-preset bundles** — moet de admin-UI ook system-groups behouden zoals "Personal chat", "Company chat", etc. (in jouw screenshot staan die al), of werkt alles via de profile-radio op user-niveau? Voorkeur: profile-radio is canoniek; system-groups blijven bestaan voor invite-bulk-flows.
5. **Quota voor Company chat** — zelfde 5/20 als Personal, of mag Company unlimited eigen KBs hebben (omdat ze toch al org-KBs zien)? Voorkeur: zelfde 5/20, anders verschuift de quotum-druk onbedoeld.
6. **Org-KB write voor Company chat (REQ-6)** — moet dat een nieuwe append-endpoint worden, of mappen we het op de bestaande `add-source` flow met een rolcheck? Voorkeur: bestaande flow + rolcheck. Lichter.
7. **Scribe / Docs koppeling** — `/app/docs` wordt afgesplitst van het `knowledge`-product en aan een eigen `docs`-product gehangen. `enabled_addons` start op `[]` voor alle tenants; admins zetten ze aan waar nodig.

---

## Implementation Notes (non-binding)

- De huidige `portal_docs_libraries` + `portal_group_docs_access` tabellen ([z2a3b4c5d6e7](klai-portal/backend/alembic/versions/z2a3b4c5d6e7_add_kb_and_docs_libraries.py)) zijn vandaag dood schema. Deze SPEC laat ze voorlopig met rust — alleen het product-flag-spoor wordt gebruikt. Een aparte cleanup-SPEC kan later beslissen om die tabellen alsnog levend te maken of te droppen.
- Default role in Alembic ([g7h8i9j0k1l2](klai-portal/backend/alembic/versions/g7h8i9j0k1l2_add_role_to_portal_users.py#L21)) staat op `'admin'`. Aanpassen naar `'company'` in dezelfde migratie als de enum-uitbreiding. Nieuwe gebruikers via invite zetten de rol expliciet op basis van profile-radio, dus de default-rol heeft alleen effect op edge-cases (eerste user in een org via provisioning, dan blijft `admin` correct via expliciete logica in `provisioning/orchestrator.py`).
- `PROFILE_CAPABILITIES` overlapt grotendeels met `PLAN_LIMITS.capabilities` van SPEC-PORTAL-UNIFY-KB-001. Voorstel: `effective_capabilities(user) = role_caps ∩ plan_caps` (intersectie — wat zowel profiel als plan toestaan). Dat behoudt plan als ceiling én profiel als floor.
- Voor `klai-litellm-hook` en partner-API: deze SPEC laat externe surface ongemoeid. De partner-API authenticeert via API-keys met eigen scope-flags; die mappen op capabilities, niet op profielen.
