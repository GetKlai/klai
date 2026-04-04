---
id: SPEC-FE-001
version: "1.0.0"
status: completed
created: "2026-03-24"
updated: "2026-04-03"
author: MoAI
priority: high
issue_number: 0
---

# SPEC-FE-001: Admin Panel -- User Lifecycle, Product Management & Groups

| Versie | Datum      | Auteur | Wijziging                          |
|--------|------------|--------|------------------------------------|
| 1.0.0  | 2026-03-24 | MoAI   | Initiele versie met alle vereisten |

## Overview

Dit SPEC definieert de frontend-uitbreiding van het Klai portal admin panel. De backend API's voor user lifecycle management, product management en group management zijn al geimplementeerd. Dit SPEC beschrijft de frontend implementatie die deze API's integreert in de bestaande React-applicatie.

Het bouwt voort op de bestaande admin-pagina's (`/admin/users`, `/admin/users/$userId/edit`) en voegt nieuwe functionaliteit toe: statusweergave, lifecycle-acties, product-toggles, en een compleet nieuw groups-domein.

**Scope (hard begrensd):**
1. Users table: status badge kolom + lifecycle acties
2. User edit page: product toggles + lifecycle actieknoppen
3. Groups pages: volledig CRUD + membership management
4. Admin sidebar + home: groups navigatie en overzichtskaart
5. Minor backend change: `status` veld toevoegen aan `UserOut`

**Buiten scope (nu):** bulk import, group admin view (leden die groepen beheren), products op groepsniveau.

**Toekomstige scope (aparte SPEC):**
- **Audit log viewer** (`/admin/audit-log`) -- Backend geimplementeerd in AUTH-003 (`GET /api/admin/audit-log`, paginated + filterable op action/resource_type). Frontend nog niet gebouwd.
- **Meeting visibility selector** -- AUTH-003 R10: visibility dropdown op meeting aanmaken ("Personal" / groepsnaam) + lock/group icoon op meeting list. Backend aanwezig (`group_id` op `vexa_meetings`).

## Environment

| Component          | Versie / Detail                                     |
|--------------------|-----------------------------------------------------|
| Runtime            | React 18+ SPA, Vite bundler                         |
| Language           | TypeScript 5+                                       |
| Routing            | TanStack Router (file-based, `createFileRoute()`)   |
| State / Data       | TanStack Query (`useQuery`, `useMutation`)          |
| UI Components      | shadcn/ui (badge, button, dialog, dropdown, table)  |
| i18n               | Paraglide (`nl.json` + `en.json`)                   |
| Auth               | react-oidc-context (`auth.user?.access_token`)      |
| Table              | TanStack React Table met `columnHelper`             |
| Icons              | lucide-react                                        |
| Backend            | FastAPI (Python), endpoints onder `/api/admin/`     |

## Assumptions

1. **Backend `status` veld:** Het `UserOut` schema in `admin.py` bevat momenteel geen `status` veld. Een minor backend-wijziging is vereist om dit toe te voegen (ophalen uit Zitadel user state). Dit is een blokkerende afhankelijkheid voor de status badge kolom.

2. **Lifecycle endpoints bestaan:** De endpoints `POST /api/admin/users/{id}/suspend`, `/reactivate`, en `/offboard` zijn volledig geimplementeerd en retourneren `{message}` bij succes.

3. **Groups endpoints bestaan:** Alle group CRUD en membership endpoints zijn geimplementeerd: `GET/POST /api/admin/groups`, `PATCH/DELETE /api/admin/groups/{id}`, `GET/POST/DELETE /api/admin/groups/{id}/members`, `PATCH .../members/{user_id}`.

4. **Product endpoints bestaan:** `GET /api/admin/products` retourneert beschikbare producten voor het plan-plafond van de organisatie. `GET/POST/DELETE /api/admin/users/{id}/products` voor toewijzing en intrekking.

5. **Bestaande patronen:** De applicatie volgt al het patroon van `delete-confirm-button.tsx` voor destructieve acties en inline twee-staps bevestiging.

6. **Badge varianten beschikbaar:** shadcn/ui badge ondersteunt: `default`, `secondary`, `accent`, `outline`, `success`, `warning`, `destructive`.

## Requirements

### R1 -- User Status Visibility (Ubiquitous)

The system **shall** display a status badge on each row in the admin users table, using badge variant `warning` for suspended users, `destructive` for offboarded users, and no badge (or `default`/`success`) for active users.

**Traceability:** Requires backend change to add `status` field to `UserOut` in `admin.py`.

### R2 -- Lifecycle Actions (Event-Driven)

**When** an admin clicks a lifecycle action (suspend, reactivate, or offboard) from the user row action menu or user edit page, the system **shall** call the corresponding backend endpoint (`POST /api/admin/users/{id}/suspend|reactivate|offboard`), display a confirmation dialog for destructive actions (offboard), show a success toast on completion, and invalidate the users query to refresh the table.

**Constraints:**
- Suspend: beschikbaar voor actieve gebruikers
- Reactivate: beschikbaar voor geschorste gebruikers
- Offboard: beschikbaar voor actieve en geschorste gebruikers, met destructieve bevestigingsdialoog inclusief waarschuwingstekst

### R3 -- Delete vs Offboard Logic (State-Driven)

**If** a user has `invite_pending = true`, **then** the system **shall** show a delete action (calling `DELETE /api/admin/users/{id}`). **If** the user is not invite_pending, **then** the system **shall** show offboard instead of delete, and the delete action **shall not** be available.

### R4 -- Product Management (Event-Driven)

**When** an admin toggles a product on the user edit page, the system **shall** call `POST /api/admin/users/{id}/products` with `{product}` to assign, or `DELETE /api/admin/users/{id}/products/{product}` to revoke, and update the UI optimistically or upon confirmation.

**Data sources:**
- Beschikbare producten: `GET /api/admin/products`
- Huidige gebruikersproducten: `GET /api/admin/users/{id}/products`

### R5 -- Group Management (Ubiquitous)

The system **shall** provide a complete group management interface with:

- **Group list page** (`/admin/groups`): tabel met naam, beschrijving, aanmaakdatum, aangemaakt door; create-knop met naam (verplicht) en beschrijving (optioneel) invoer; 409 duplicate name afhandeling; lege staat wanneer er geen groepen zijn.
- **Group detail/members page** (`/admin/groups/$groupId`): ledentabel met gebruikersnaam, email, group admin status, lid sinds; leden toevoegen uit bestaande org-gebruikers; leden verwijderen; group admin toggle (`is_group_admin`); groep bewerken (naam, beschrijving); groep verwijderen met waarschuwing over cascade effect op lidmaatschappen.
- **Admin sidebar**: Groups navigatie-item met `Users` of `FolderKanban` icoon uit lucide-react.
- **Admin home**: Groups overzichtskaart met totaal aantal groepen.

## Specifications

### Nieuwe routes en bestanden

| Route / Bestand                                  | Doel                                      |
|--------------------------------------------------|-------------------------------------------|
| `admin/groups/index.tsx`                         | Groups lijstpagina met CRUD               |
| `admin/groups/$groupId/index.tsx`                | Group detail met ledenbeheer              |
| Wijziging: `admin/users/index.tsx`               | Status badge kolom + lifecycle acties     |
| Wijziging: `admin/users/$userId/edit.tsx`        | Product toggles + lifecycle knoppen       |
| Wijziging: admin sidebar component               | Groups nav-item toevoegen                 |
| Wijziging: admin home/dashboard                  | Groups overzichtskaart toevoegen          |

### Backend wijziging

- **Bestand:** `admin.py` (of equivalent in portal backend)
- **Wijziging:** `status` veld toevoegen aan `UserOut` Pydantic model
- **Bron:** Zitadel user state (active / suspended / deactivated)

### API client functies (nieuw)

Alle functies gebruiken `auth.user?.access_token` voor authenticatie headers.

**User lifecycle:**
- `suspendUser(userId: string): Promise<{message: string}>`
- `reactivateUser(userId: string): Promise<{message: string}>`
- `offboardUser(userId: string): Promise<{message: string}>`

**Products:**
- `getUserProducts(userId: string): Promise<{products: Product[]}>`
- `getAvailableProducts(): Promise<Product[]>`
- `assignProduct(userId: string, product: string): Promise<void>`
- `revokeProduct(userId: string, product: string): Promise<void>`

**Groups:**
- `getGroups(): Promise<{groups: Group[]}>`
- `createGroup(data: {name: string, description?: string}): Promise<Group>`
- `updateGroup(groupId: string, data: {name?: string, description?: string}): Promise<Group>`
- `deleteGroup(groupId: string): Promise<void>`
- `getGroupMembers(groupId: string): Promise<{members: GroupMember[]}>`
- `addGroupMember(groupId: string, userId: string): Promise<void>`
- `removeGroupMember(groupId: string, userId: string): Promise<void>`
- `updateGroupMember(groupId: string, userId: string, data: {is_group_admin: boolean}): Promise<void>`

### i18n key namespaces

Alle nieuwe i18n keys volgen het patroon `admin_{section}_{element}_{state}`. Beide taalbestanden (`nl.json` en `en.json`) moeten worden bijgewerkt. Zie `plan.md` voor de volledige lijst van benodigde keys.

### Traceability

| Requirement | Routes / Bestanden                                | API Endpoints                                              |
|-------------|---------------------------------------------------|------------------------------------------------------------|
| R1          | `admin/users/index.tsx`                           | `GET /api/admin/users` (met `status` veld)                 |
| R2          | `admin/users/index.tsx`, `$userId/edit.tsx`        | `POST .../suspend`, `.../reactivate`, `.../offboard`       |
| R3          | `admin/users/index.tsx`, `$userId/edit.tsx`        | `DELETE /api/admin/users/{id}`                             |
| R4          | `admin/users/$userId/edit.tsx`                     | `GET/POST/DELETE .../products`                             |
| R5          | `admin/groups/index.tsx`, `$groupId/index.tsx`     | Alle `/api/admin/groups` endpoints                         |
