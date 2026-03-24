---
id: SPEC-FE-001
document: plan
version: "1.0.0"
status: draft
created: "2026-03-24"
updated: "2026-03-24"
---

# SPEC-FE-001: Implementatieplan

## Overzicht

Dit plan beschrijft de implementatiestrategie voor het admin panel uitbreiding. De taken zijn geordend op afhankelijkheid: de backend-wijziging eerst (blocker), daarna users table, user edit, groups, en tot slot navigatie/i18n.

## Technische constraints

- **Geen nieuwe dependencies:** gebruik uitsluitend bestaande shadcn/ui componenten, TanStack Query/Router/Table, lucide-react icons
- **Bestaande patronen volgen:** `delete-confirm-button.tsx` voor destructieve acties, `columnHelper` voor tabelkolommen, `createFileRoute()` voor routes
- **i18n verplicht:** alle user-facing tekst via Paraglide keys in zowel `nl.json` als `en.json`
- **Auth header:** alle API calls gebruiken `auth.user?.access_token` uit `react-oidc-context`

## Taakindeling

### Taak 1: Backend -- `status` veld toevoegen aan `UserOut` (Prioriteit: Hoog -- Blocker)

**Wat:** Voeg `status: str` toe aan het `UserOut` Pydantic model in de backend admin module, afgeleid van Zitadel user state.

**Bestanden te wijzigen:**
- `portal/backend/app/routers/admin.py` -- `UserOut` model uitbreiden met `status` veld
- `portal/backend/app/routers/admin.py` -- query/mapping logica om Zitadel user state (active/suspended/deactivated) naar `status` string te mappen

**Referentie-implementatie:** Bestaande velden in `UserOut` (zoals `invite_pending`) voor patroon.

**Risico:** Als de Zitadel API geen direct user state veld biedt, moet dit via een extra API call of de bestaande data worden afgeleid. Graceful degradation: toon geen badge als `status` undefined is.

**MX tags:** De bestaande functies `suspend_user`, `reactivate_user`, `offboard_user` in de backend hebben `# @MX:ANCHOR fan_in=8` annotaties. Wijzig deze niet.

---

### Taak 2: Users table -- Status badge kolom + lifecycle action menu (Prioriteit: Hoog)

**Wat:** Voeg een status badge kolom toe aan de users tabel en een actiemenu (MoreVertical dropdown) met lifecycle acties per rij.

**Bestanden te wijzigen:**
- `portal/frontend/src/routes/admin/users/index.tsx` -- nieuwe kolom toevoegen aan `columnHelper`, actiemenu component

**Nieuwe componenten/functies:**
- Status badge rendering functie: `warning` badge voor suspended, `destructive` voor offboarded, `default`/geen voor active
- Action menu met `DropdownMenu` (shadcn/ui): Suspend (alleen voor active), Reactivate (alleen voor suspended), Offboard (voor active + suspended, destructieve bevestiging), Delete (alleen voor `invite_pending`)
- TanStack Query mutations: `useSuspendUser()`, `useReactivateUser()`, `useOffboardUser()` met query invalidation op `['admin', 'users']`

**Referentie-implementatie:**
- Bestaande kolommen in users table voor `columnHelper` patroon
- `delete-confirm-button.tsx` voor destructieve bevestiging
- Bestaande `DropdownMenu` gebruik in de applicatie

**UI gedrag:**
- Na succesvolle actie: success toast + automatische tabelverversing via query invalidation
- Bij 409 (conflict, bijv. al geschorst): error toast met backend foutmelding
- Offboard bevestiging: Dialog met waarschuwingstekst over onomkeerbare gevolgen (verwijdert lidmaatschappen + producten, deactiveert in Zitadel)

---

### Taak 3: User edit page -- Product toggles + lifecycle actieknoppen (Prioriteit: Hoog)

**Wat:** Voeg een product management sectie en context-sensitive lifecycle actieknoppen toe aan de user edit pagina.

**Bestanden te wijzigen:**
- `portal/frontend/src/routes/admin/users/$userId/edit.tsx` -- product toggles sectie + lifecycle knoppen

**Nieuwe componenten/functies:**
- Product toggles sectie:
  - `useQuery(['admin', 'products'])` voor beschikbare producten (`GET /api/admin/products`)
  - `useQuery(['admin', 'users', userId, 'products'])` voor huidige gebruikersproducten
  - Toggle/switch per product: checked = toegewezen, unchecked = niet toegewezen
  - `useMutation` voor assign (`POST`) en revoke (`DELETE`) met query invalidation
  - Toon `enabled_at` en `enabled_by` bij toegewezen producten
- Lifecycle actieknoppen:
  - Context-sensitive: toon alleen relevante acties op basis van user status en `invite_pending`
  - Zelfde mutations als Taak 2, hergebruik of extraheer naar gedeelde hooks
  - Offboard knop: `destructive` button variant met bevestigingsdialoog

**Referentie-implementatie:**
- Bestaande form layout op de edit pagina
- Switch/toggle componenten uit shadcn/ui

---

### Taak 4: Groups lijstpagina (Prioriteit: Hoog)

**Wat:** Nieuwe pagina `/admin/groups` met groepenoverzicht en create-functionaliteit.

**Bestanden te creeren:**
- `portal/frontend/src/routes/admin/groups/index.tsx` -- groups route met `createFileRoute()`

**Functionaliteit:**
- Tabel met kolommen: naam, beschrijving, aanmaakdatum, aangemaakt door
- Create-knop opent Dialog/Sheet met formulier:
  - Naam (verplicht, text input)
  - Beschrijving (optioneel, textarea)
  - Submit via `useMutation` naar `POST /api/admin/groups`
  - 409 afhandeling: toon foutmelding "Groepsnaam bestaat al"
- Rij-acties: klik navigeert naar group detail pagina
- Lege staat: informatief bericht wanneer er geen groepen zijn, met create-knop

**Referentie-implementatie:**
- Users table voor tabel- en kolompatroon
- Bestaande Dialog/form patronen in de applicatie

---

### Taak 5: Group detail/members pagina (Prioriteit: Hoog)

**Wat:** Nieuwe pagina `/admin/groups/$groupId` met groepdetails en ledenbeheer.

**Bestanden te creeren:**
- `portal/frontend/src/routes/admin/groups/$groupId/index.tsx` -- group detail route

**Functionaliteit:**
- Groepsinformatie header: naam, beschrijving (inline bewerkbaar of via edit dialog)
- Groep bewerken: `PATCH /api/admin/groups/{id}` met naam en beschrijving
- Groep verwijderen: destructieve bevestiging met waarschuwing over cascade (verwijdert alle lidmaatschappen), `DELETE /api/admin/groups/{id}`
- Ledentabel met kolommen: gebruikersnaam (voornaam + achternaam), email, group admin status (badge), lid sinds
- Lid toevoegen:
  - Dropdown/combobox met bestaande org-gebruikers (uit `GET /api/admin/users`)
  - Filter op naam/email
  - `POST /api/admin/groups/{id}/members` met `{zitadel_user_id}`
  - 409 afhandeling: "Gebruiker is al lid"
- Lid verwijderen: `DELETE /api/admin/groups/{id}/members/{user_id}` met bevestiging
- Group admin toggle: Switch/checkbox per lid, `PATCH .../members/{user_id}` met `{is_group_admin: bool}`
- Lege staat: bericht wanneer groep geen leden heeft

**Referentie-implementatie:**
- Users table voor tabel- en actiepatroon
- User edit page voor detail/edit layout

---

### Taak 6: Admin home + sidebar updates (Prioriteit: Medium)

**Wat:** Groups navigatie-item toevoegen aan admin sidebar en overzichtskaart aan admin home.

**Bestanden te wijzigen:**
- Admin sidebar component (locatie te identificeren in bestaande code) -- nav-item toevoegen
- Admin home/dashboard component -- overzichtskaart toevoegen

**Details:**
- Sidebar: "Groups" of "Groepen" (i18n) nav-item met `Users` of `FolderKanban` icoon uit lucide-react, navigeert naar `/admin/groups`
- Admin home kaart: toon totaal aantal groepen, klikbaar naar `/admin/groups`
- Kaart data: hergebruik `useQuery(['admin', 'groups'])` of voeg count endpoint toe

---

### Taak 7: i18n keys (Prioriteit: Hoog -- doorlopend bij elke taak)

**Wat:** Alle nieuwe user-facing tekst toevoegen aan zowel `nl.json` als `en.json`.

**Bestanden te wijzigen:**
- `portal/frontend/src/paraglide/messages/nl.json`
- `portal/frontend/src/paraglide/messages/en.json`

**Volledige key-lijst:**

**User status:**
- `admin_users_status_active` -- NL: "Actief" / EN: "Active"
- `admin_users_status_suspended` -- NL: "Geschorst" / EN: "Suspended"
- `admin_users_status_offboarded` -- NL: "Offboarded" / EN: "Offboarded"

**Lifecycle acties:**
- `admin_users_action_suspend` -- NL: "Schorsen" / EN: "Suspend"
- `admin_users_action_reactivate` -- NL: "Heractiveren" / EN: "Reactivate"
- `admin_users_action_offboard` -- NL: "Offboarden" / EN: "Offboard"
- `admin_users_action_delete` -- NL: "Verwijderen" / EN: "Delete"
- `admin_users_confirm_suspend_title` -- NL: "Gebruiker schorsen" / EN: "Suspend user"
- `admin_users_confirm_suspend_description` -- NL: "Weet je zeker dat je deze gebruiker wilt schorsen?" / EN: "Are you sure you want to suspend this user?"
- `admin_users_confirm_offboard_title` -- NL: "Gebruiker offboarden" / EN: "Offboard user"
- `admin_users_confirm_offboard_description` -- NL: "Dit is onomkeerbaar. Alle lidmaatschappen en producten worden verwijderd en het account wordt gedeactiveerd in Zitadel." / EN: "This is irreversible. All memberships and products will be removed and the account will be deactivated in Zitadel."
- `admin_users_success_suspended` -- NL: "Gebruiker is geschorst" / EN: "User has been suspended"
- `admin_users_success_reactivated` -- NL: "Gebruiker is geheractiveerd" / EN: "User has been reactivated"
- `admin_users_success_offboarded` -- NL: "Gebruiker is geoffboard" / EN: "User has been offboarded"
- `admin_users_error_already_suspended` -- NL: "Gebruiker is al geschorst" / EN: "User is already suspended"

**Products:**
- `admin_users_products_title` -- NL: "Producten" / EN: "Products"
- `admin_users_products_empty` -- NL: "Geen producten toegewezen" / EN: "No products assigned"
- `admin_users_products_enabled_at` -- NL: "Ingeschakeld op" / EN: "Enabled at"
- `admin_users_products_enabled_by` -- NL: "Ingeschakeld door" / EN: "Enabled by"
- `admin_users_products_assign_success` -- NL: "Product toegewezen" / EN: "Product assigned"
- `admin_users_products_revoke_success` -- NL: "Product ingetrokken" / EN: "Product revoked"

**Groups -- lijst:**
- `admin_groups_title` -- NL: "Groepen" / EN: "Groups"
- `admin_groups_empty` -- NL: "Nog geen groepen aangemaakt" / EN: "No groups created yet"
- `admin_groups_empty_description` -- NL: "Maak een groep aan om gebruikers te organiseren." / EN: "Create a group to organize users."
- `admin_groups_create` -- NL: "Groep aanmaken" / EN: "Create group"
- `admin_groups_name` -- NL: "Naam" / EN: "Name"
- `admin_groups_name_placeholder` -- NL: "Groepsnaam" / EN: "Group name"
- `admin_groups_description` -- NL: "Beschrijving" / EN: "Description"
- `admin_groups_description_placeholder` -- NL: "Optionele beschrijving" / EN: "Optional description"
- `admin_groups_created_at` -- NL: "Aangemaakt op" / EN: "Created at"
- `admin_groups_created_by` -- NL: "Aangemaakt door" / EN: "Created by"
- `admin_groups_success_created` -- NL: "Groep aangemaakt" / EN: "Group created"
- `admin_groups_error_duplicate` -- NL: "Een groep met deze naam bestaat al" / EN: "A group with this name already exists"

**Groups -- detail:**
- `admin_groups_edit` -- NL: "Groep bewerken" / EN: "Edit group"
- `admin_groups_delete` -- NL: "Groep verwijderen" / EN: "Delete group"
- `admin_groups_confirm_delete_title` -- NL: "Groep verwijderen" / EN: "Delete group"
- `admin_groups_confirm_delete_description` -- NL: "Alle lidmaatschappen worden verwijderd. Dit kan niet ongedaan worden gemaakt." / EN: "All memberships will be removed. This cannot be undone."
- `admin_groups_success_updated` -- NL: "Groep bijgewerkt" / EN: "Group updated"
- `admin_groups_success_deleted` -- NL: "Groep verwijderd" / EN: "Group deleted"

**Groups -- leden:**
- `admin_groups_members_title` -- NL: "Leden" / EN: "Members"
- `admin_groups_members_empty` -- NL: "Deze groep heeft nog geen leden" / EN: "This group has no members yet"
- `admin_groups_members_add` -- NL: "Lid toevoegen" / EN: "Add member"
- `admin_groups_members_remove` -- NL: "Verwijderen" / EN: "Remove"
- `admin_groups_members_admin` -- NL: "Groepsbeheerder" / EN: "Group admin"
- `admin_groups_members_joined_at` -- NL: "Lid sinds" / EN: "Member since"
- `admin_groups_members_search_placeholder` -- NL: "Zoek gebruiker op naam of email..." / EN: "Search user by name or email..."
- `admin_groups_members_success_added` -- NL: "Lid toegevoegd" / EN: "Member added"
- `admin_groups_members_success_removed` -- NL: "Lid verwijderd" / EN: "Member removed"
- `admin_groups_members_error_already_member` -- NL: "Gebruiker is al lid van deze groep" / EN: "User is already a member of this group"
- `admin_groups_members_admin_toggled` -- NL: "Groepsbeheerder status bijgewerkt" / EN: "Group admin status updated"

**Admin sidebar/home:**
- `admin_nav_groups` -- NL: "Groepen" / EN: "Groups"
- `admin_home_groups_title` -- NL: "Groepen" / EN: "Groups"
- `admin_home_groups_count` -- NL: "Totaal aantal groepen" / EN: "Total groups"

## Risico's en mitigatie

| Risico | Impact | Mitigatie |
|--------|--------|-----------|
| `status` veld niet beschikbaar in backend | Users table kan geen badge tonen | Graceful degradation: verberg badge kolom als `status` undefined; doe backend-wijziging eerst |
| 409 foutafhandeling inconsistent | Slechte UX bij duplicate acties | Centraliseer error handling in mutation `onError` callbacks; parse backend error message |
| Grote i18n key-lijst | Kans op ontbrekende vertalingen | Genereer alle keys in een batch; valideer completeness na implementatie |
| Group member toevoegen: gebruikerslijst groot | Performance bij grote organisaties | Gebruik search/filter met debounce in combobox; overweeg server-side filtering |

## Architectuurbeslissingen

1. **Gedeelde mutation hooks:** Extraheer lifecycle mutations (`useSuspendUser`, etc.) naar gedeelde hooks zodat users table en user edit page dezelfde logica gebruiken.
2. **API client functies:** Groepeer nieuwe API functies in een `admin-api.ts` module (of voeg toe aan bestaande API module).
3. **Query keys:** Volg bestaand patroon met arrays: `['admin', 'users']`, `['admin', 'groups']`, `['admin', 'users', userId, 'products']`.
4. **Geen nieuwe dependencies:** Alle UI wordt gebouwd met bestaande shadcn/ui componenten.
