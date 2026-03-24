---
id: SPEC-AUTH-004
version: "1.0.0"
status: draft
created: "2026-03-24"
updated: "2026-03-24"
author: MoAI
priority: high
issue_number: 23
---

## HISTORY

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0.0 | 2026-03-24 | MoAI | Initial draft |

# SPEC-AUTH-004: Group-Based Product Entitlements

## Overview

Per-user producttoewijzing schaalt niet voor enterprise-gebruik. De industriestandaard is groepsgebaseerd: wijs producten toe aan groepen, gebruikers erven toegang via lidmaatschap. "Voeg toe aan chat-users groep" is een enkele actie in plaats van per gebruiker toggelen. Deze specificatie introduceert `portal_group_products` als de primaire manier om producten te beheren, terwijl de bestaande per-user toewijzingen (`portal_user_products`) functioneel blijven als dual-mode compatibiliteitslaag.

## Environment

- **Runtime:** FastAPI backend (Python 3.13+, async)
- **Database:** PostgreSQL via SQLAlchemy 2.0 async, Alembic migrations
- **Auth provider:** Zitadel (JWT enrichment via Pre-access-token-creation Action)
- **Frontend:** React portal admin interface (TanStack Router, TanStack Query)
- **Bestaande tabellen:** `portal_orgs`, `portal_users`, `portal_user_products`, `portal_groups`, `portal_group_memberships`
- **Bestaande endpoints:** groep CRUD in `app/api/groups.py`, user product CRUD in `app/api/admin.py:449-601`
- **Bestaande dependencies:** `require_product()` in `app/api/dependencies.py`
- **JWT enrichment:** `/internal/users/{id}/products` in `app/api/internal.py:66-82`

## Assumptions

- A1: Groepen bestaan al met lidmaatschapsbeheer (SPEC-AUTH-001). Deze SPEC voegt alleen producttoewijzing aan groepen toe.
- A2: De plan-to-products mapping blijft een applicatie-constante in `PLAN_PRODUCTS` (SPEC-AUTH-002). Groepsproducten zijn ook gebonden aan het plan-plafond.
- A3: JWT tokens hebben een korte expiry (~15 minuten). Stale product claims na groepswijziging zijn acceptabel voor de duur van een token-lifetime.
- A4: De `get_user_effective_products()` functie moet performant zijn: de join over group_memberships en group_products mag niet meer dan ~50ms toevoegen aan de JWT enrichment call.
- A5: Bestaande `portal_user_products` records blijven functioneel. De backend ondersteunt dual-mode (directe + groepsgebaseerde toewijzingen). Alleen de admin UI voor nieuwe per-user toewijzingen wordt verwijderd.
- A6: Migratie van bestaande per-user toewijzingen naar groepen voor organisaties die voor deze feature bestaan is out of scope (follow-up taak).

## Requirements

### R1 -- Ubiquitous: Effectieve Producttoegang

Het systeem berekent de effectieve producttoegang van een gebruiker **altijd** als de unie van: (1) directe toewijzingen via `portal_user_products` en (2) groepsgeerfde toewijzingen via `portal_group_memberships` -> `portal_group_products`.

### R2 -- Event-driven: Groepsproduct Toewijzen

**WHEN** een org-admin een product toewijst aan een groep **THEN** wordt een `portal_group_products` record aangemaakt met `group_id`, `org_id`, `product`, `enabled_at` en `enabled_by`, mits het product binnen het plan-plafond van de organisatie valt.

### R3 -- Event-driven: Groepsproduct Intrekken

**WHEN** een org-admin een product intrekt van een groep **THEN** wordt het corresponderende `portal_group_products` record verwijderd en verliezen groepsleden die het product niet via een andere route (directe toewijzing of ander groepslidmaatschap) ontvangen, de toegang bij de volgende JWT refresh.

### R4 -- Unwanted Behavior: Plan-plafond Handhaving voor Groepen

Het systeem **mag niet** toestaan dat een product aan een groep wordt toegewezen als dat product niet is opgenomen in het plan van de organisatie (bijv. "knowledge" toewijzen aan een groep in een "professional" org).

### R5 -- Event-driven: Plan Downgrade met Groepsproducten

**WHEN** een plan downgrade plaatsvindt **THEN** verwijdert het systeem alle `portal_group_products` records die producten bevatten die het nieuwe plan-plafond overschrijden, aanvullend op de bestaande logica die per-user toewijzingen intrekt.

### R6 -- Event-driven: Standaardgroepen bij Org-aanmaak

**WHEN** een nieuwe organisatie wordt aangemaakt **THEN** maakt het systeem standaardgroepen aan voor elk product in het plan van de organisatie: "Chat users" (product: chat), "Scribe users" (product: scribe, indien in plan), "Knowledge users" (product: knowledge, indien in plan), elk met het corresponderende product direct toegewezen.

### R7 -- State-driven: JWT Enrichment met Groepsproducten

**IF** de interne endpoint `/internal/users/{id}/products` wordt aangeroepen **THEN** retourneert het systeem de effectieve productenlijst (unie van directe en groepsgebaseerde toewijzingen) zodat de Zitadel Action de `klai:products` JWT claim correct vult.

### R8 -- Optional Feature: Leesbare Effectieve Producten in Admin UI

**Waar mogelijk** toont de gebruiker-bewerkingspagina een alleen-lezen "Effectieve producten" sectie die per product aangeeft of toegang via directe toewijzing of groepslidmaatschap (met groepsnaam) wordt verkregen.

## Specifications

### S1: Database Schema -- `portal_group_products`

| Kolom | Type | Constraints |
|-------|------|------------|
| `id` | UUID | PK, default gen_random_uuid() |
| `group_id` | UUID | FK -> portal_groups(id) ON DELETE CASCADE, NOT NULL |
| `org_id` | UUID | FK -> portal_orgs(id), NOT NULL (gedenormaliseerd) |
| `product` | VARCHAR(32) | NOT NULL |
| `enabled_at` | TIMESTAMPTZ | NOT NULL, default now() |
| `enabled_by` | VARCHAR(255) | NOT NULL (Zitadel user ID van de admin) |

**Indexes:**
- `ix_group_products_group_id` op `(group_id)`
- `ix_group_products_org_product` op `(org_id, product)`

**Unique constraint:** `uq_group_products_group_product` op `(group_id, product)`

### S2: API Endpoints

| Method | Path | Beschrijving | Autorisatie |
|--------|------|-------------|-------------|
| GET | `/api/admin/groups/{group_id}/products` | Lijst producten van groep | org admin |
| POST | `/api/admin/groups/{group_id}/products` | Wijs product toe aan groep | org admin (NIET group admin) |
| DELETE | `/api/admin/groups/{group_id}/products/{product}` | Trek product in van groep | org admin (NIET group admin) |

### S3: Entitlement Resolution

De `get_user_effective_products(user_id, db)` functie retourneert een `set[str]` met:
- Alle producten uit `portal_user_products` WHERE `zitadel_user_id = user_id`
- UNION alle producten uit `portal_group_products` WHERE `group_id IN (SELECT group_id FROM portal_group_memberships WHERE zitadel_user_id = user_id)`

### S4: Frontend Wijzigingen

- **Groep-detailpagina:** Products sectie met Switch toggles per plan-product (parallel aan de bestaande leden-sectie)
- **Gebruiker-bewerkingspagina:** Verwijder per-user product toggles. Vervang door alleen-lezen "Effectieve producten" sectie die bron (direct/groep) toont

### S5: Traceability Tags

| Tag | Requirement | Bestanden |
|-----|-------------|-----------|
| AUTH-004-R1 | Effectieve producttoegang | `app/core/products.py`, `app/api/dependencies.py` |
| AUTH-004-R2 | Groepsproduct toewijzen | `app/api/groups.py`, `app/models/groups.py` |
| AUTH-004-R3 | Groepsproduct intrekken | `app/api/groups.py` |
| AUTH-004-R4 | Plan-plafond groepen | `app/api/groups.py`, `app/core/plans.py` |
| AUTH-004-R5 | Plan downgrade groepen | `app/api/admin.py` |
| AUTH-004-R6 | Standaardgroepen | `app/api/admin.py` of `app/core/orgs.py` |
| AUTH-004-R7 | JWT enrichment | `app/api/internal.py` |
| AUTH-004-R8 | Effectieve producten UI | `routes/admin/users/$userId/edit.tsx`, `routes/admin/groups/$groupId/index.tsx` |
