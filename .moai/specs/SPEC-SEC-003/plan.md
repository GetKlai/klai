# SPEC-SEC-003: Implementation Plan

## Fase 1 — Veilige tabellen (1 Alembic-migratie)

### Task 1.1: Alembic-migratie voor 5 veilige tabellen

Bestand: `klai-portal/backend/alembic/versions/<hash>_add_rls_phase2_safe_tables.py`

Tabellen met directe org_id:
```python
for table in ("portal_kb_tombstones", "portal_user_kb_access", "portal_retrieval_gaps"):
    _enable_rls(table)       # ALTER TABLE ... ENABLE/FORCE ROW LEVEL SECURITY
    _create_org_policy(table) # CREATE POLICY tenant_isolation ... USING (org_id = _T)
```

Tabellen met indirecte org_id (via kb_id):
```python
for table in ("portal_taxonomy_nodes", "portal_taxonomy_proposals"):
    _enable_rls(table)
    # Subquery policy via parent knowledge base
    op.execute(
        f"CREATE POLICY tenant_isolation ON {table} "
        f"USING (kb_id IN (SELECT id FROM portal_knowledge_bases WHERE org_id = {_T}))"
    )
```

Hergebruik `_enable_rls()` en `_T` patronen uit bestaande migratie `c5d6e7f8a9b0`.

### Task 1.2: Verificatie Fase 1

- Run migratie lokaal
- Verify via `psql`: `SELECT tablename, policyname FROM pg_policies WHERE tablename IN (...)`
- Test authenticated endpoints die deze tabellen raken
- ruff check + pyright

---

## Fase 2a — Split policies voor background-task tabellen (1 Alembic-migratie)

### Task 2a.1: Alembic-migratie voor product_events en vexa_meetings

Bestand: `klai-portal/backend/alembic/versions/<hash>_add_rls_phase2_background_tables.py`

**product_events** (INSERT-only vanuit background tasks):
```python
_enable_rls("product_events")
op.execute(f"CREATE POLICY tenant_read ON product_events FOR SELECT USING (org_id = {_T})")
op.execute("CREATE POLICY tenant_write ON product_events FOR INSERT WITH CHECK (true)")
```

**vexa_meetings** (INSERT + UPDATE vanuit background tasks):
```python
_enable_rls("vexa_meetings")
# SELECT: alleen eigen org
op.execute(f"CREATE POLICY tenant_read ON vexa_meetings FOR SELECT USING (org_id = {_T})")
# INSERT: altijd toestaan (meeting aanmaken kan vanuit webhook context)
op.execute("CREATE POLICY tenant_write ON vexa_meetings FOR INSERT WITH CHECK (true)")
# UPDATE: eigen org OF geen tenant context (background poller/cleanup)
op.execute(
    f"CREATE POLICY tenant_update ON vexa_meetings FOR UPDATE "
    f"USING (org_id = {_T} OR NULLIF(current_setting('app.current_org_id', true), '') IS NULL)"
)
```

### Task 2a.2: Verificatie Fase 2a

- Verify `emit_event()` nog werkt na migratie (INSERT zonder tenant context)
- Verify vexa background poller meetings kan updaten
- Verify authenticated meeting-endpoints correct gefilterd zijn
- ruff check + pyright

---

## Fase 2b — Code-fixes + RLS voor interne endpoints (code + 1 migratie)

### Task 2b.1: Fix internal.py — set_tenant na resource lookup

Bestand: `klai-portal/backend/app/api/internal.py`

**Pattern:** Haal resource op zonder RLS (via primary key), zet tenant context, voer verdere queries uit.

Probleem: Na RLS op `portal_users` kan de eerste lookup niet meer zonder tenant context.
Oplossing: Gebruik `text()` raw SQL voor de initiële lookup (bypass ORM's RLS), dan `set_tenant()`.

```python
# /api/internal/user-language
stmt = text("SELECT preferred_language, org_id FROM portal_users WHERE zitadel_user_id = :uid")
row = (await db.execute(stmt, {"uid": user_id})).first()
if row:
    await set_tenant(db, row.org_id)
    # verdere queries zijn nu RLS-beschermd
```

Alternatief: Maak een aparte PostgreSQL-role voor interne service calls die RLS bypassed. Dit is complexer maar schoner op lange termijn.

**Aanbevolen aanpak:** Raw SQL voor initiële lookup. Simpeler, bewezen patroon (audit_log gebruikt ook raw SQL).

Endpoints om te fixen:
1. `/api/internal/user-language` (regel ~52-74)
2. `/api/internal/users/{zitadel_user_id}/products` (regel ~81-94)
3. `/api/internal/v1/users/{librechat_user_id}/feature/knowledge` (regel ~202-288)
4. `/api/internal/connectors/{connector_id}/sync-status` (regel ~159-190)

### Task 2b.2: Alembic-migratie voor portal_users, portal_user_products, portal_connectors

Bestand: `klai-portal/backend/alembic/versions/<hash>_add_rls_phase2_user_tables.py`

```python
for table in ("portal_users", "portal_user_products", "portal_connectors"):
    _enable_rls(table)
    _create_org_policy(table)
```

Standaard `tenant_isolation` USING policy — alle code paths roepen nu `set_tenant()` aan.

### Task 2b.3: Verificatie Fase 2b

- Test alle interne endpoints met curl/httpx
- Verify LibreChat KB feature check werkt (meest kritieke pad)
- Verify connector sync-status endpoint werkt
- Verify `get_effective_products()` werkt vanuit interne context
- ruff check + pyright

---

## Fase 3 — Documentatie

### Task 3.1: Serena domain-model memory update

Voeg RLS-sectie toe aan `domain-model` memory met complete tabel.

### Task 3.2: Security pitfalls update

Voeg RLS coverage-tabel toe aan `.claude/rules/klai/pitfalls/security.md`.

---

## Implementatievolgorde

```
Fase 1 (veilig, kan direct)
  └── Task 1.1: Migratie 5 veilige tabellen
  └── Task 1.2: Verificatie

Fase 2a (split policies)
  └── Task 2a.1: Migratie product_events + vexa_meetings
  └── Task 2a.2: Verificatie

Fase 2b (code-fixes + RLS)
  └── Task 2b.1: Fix internal.py endpoints
  └── Task 2b.2: Migratie portal_users + portal_user_products + portal_connectors
  └── Task 2b.3: Verificatie

Fase 3 (docs)
  └── Task 3.1: Serena memory
  └── Task 3.2: Security pitfalls
```

## Risico-mitigatie

- Deploy Fase 1 apart en monitor 24h voordat Fase 2 wordt gedeployed
- Fase 2b code-fixes MOETEN gedeployed worden VOOR de RLS-migratie (anders breken interne endpoints)
- Rollback: elke migratie heeft een `downgrade()` die policies dropt en RLS disablet

## Bestanden die gewijzigd worden

| Bestand | Wijziging |
|---------|-----------|
| `alembic/versions/*_rls_phase2_safe.py` | Nieuwe migratie (Fase 1) |
| `alembic/versions/*_rls_phase2_background.py` | Nieuwe migratie (Fase 2a) |
| `alembic/versions/*_rls_phase2_users.py` | Nieuwe migratie (Fase 2b) |
| `app/api/internal.py` | set_tenant + raw SQL in 4 endpoints |
| `.serena/memories/domain-model.md` | RLS-sectie toevoegen |
| `.claude/rules/klai/pitfalls/security.md` | RLS coverage-tabel |
