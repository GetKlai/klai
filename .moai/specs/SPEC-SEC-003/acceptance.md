# SPEC-SEC-003: Acceptance Criteria

## AC-1: Fase 1 — Veilige tabellen hebben RLS

**Given** de Fase 1 migratie is toegepast
**When** ik de RLS-policies opvraag via psql
**Then** hebben de volgende tabellen `tenant_isolation` policies:
- `portal_kb_tombstones` — `USING (org_id = app.current_org_id)`
- `portal_user_kb_access` — `USING (org_id = app.current_org_id)`
- `portal_retrieval_gaps` — `USING (org_id = app.current_org_id)`
- `portal_taxonomy_nodes` — `USING (kb_id IN (SELECT ... WHERE org_id = app.current_org_id))`
- `portal_taxonomy_proposals` — `USING (kb_id IN (SELECT ... WHERE org_id = app.current_org_id))`

**Verificatie:**
```sql
SELECT schemaname, tablename, policyname, cmd, qual
FROM pg_policies
WHERE tablename IN (
  'portal_kb_tombstones', 'portal_user_kb_access', 'portal_retrieval_gaps',
  'portal_taxonomy_nodes', 'portal_taxonomy_proposals'
);
```

---

## AC-2: Fase 2a — product_events INSERT werkt zonder tenant context

**Given** de Fase 2a migratie is toegepast
**And** er is GEEN `app.current_org_id` ingesteld (geen `set_tenant()`)
**When** `emit_event()` een ProductEvent INSERT uitvoert via independent session
**Then** wordt het event succesvol opgeslagen (geen RLS-fout)

**And given** er IS een `app.current_org_id` ingesteld
**When** een SELECT op `product_events` wordt uitgevoerd
**Then** worden alleen events voor die org geretourneerd

---

## AC-3: Fase 2a — vexa_meetings background UPDATE werkt zonder tenant context

**Given** de Fase 2a migratie is toegepast
**And** er is GEEN `app.current_org_id` ingesteld
**When** de vexa background poller een meeting-status UPDATE uitvoert
**Then** wordt de status succesvol gewijzigd (geen RLS-fout)

**And given** er IS een `app.current_org_id` ingesteld
**When** een authenticated endpoint meetings opvraagt via SELECT
**Then** worden alleen meetings voor die org geretourneerd

---

## AC-4: Fase 2b — Interne endpoints werken na RLS op portal_users

**Given** de code-fix in `internal.py` is gedeployed
**And** de Fase 2b migratie is toegepast
**When** het LiteLLM hook endpoint `/api/internal/v1/users/{librechat_user_id}/feature/knowledge` wordt aangeroepen
**Then** retourneert het endpoint correct de KB-voorkeuren van de gebruiker (status 200)

**And when** het endpoint `/api/internal/user-language` wordt aangeroepen
**Then** retourneert het de taalvoorkeur van de gebruiker (status 200)

**And when** het endpoint `/api/internal/users/{zitadel_user_id}/products` wordt aangeroepen
**Then** retourneert het de productentitlements van de gebruiker (status 200)

---

## AC-5: Fase 2b — Connector sync-status werkt na RLS

**Given** de code-fix in `internal.py` is gedeployed
**And** de Fase 2b migratie is toegepast
**When** het endpoint `/api/internal/connectors/{connector_id}/sync-status` wordt aangeroepen
**Then** retourneert het de sync-status van de connector (status 200)

---

## AC-6: Cross-tenant isolatie verified

**Given** alle migraties zijn toegepast
**When** een authenticated request met `app.current_org_id = X` een query uitvoert op een RLS-beschermde tabel
**Then** worden GEEN rijen geretourneerd met `org_id != X`

Dit geldt voor alle 16 tabellen met RLS (6 bestaand + 10 nieuw).

---

## AC-7: Documentatie bijgewerkt

**Given** alle migraties en code-fixes zijn gedeployed
**Then** bevat Serena's `domain-model` memory een complete RLS-sectie met alle 16 tabellen
**And** bevat `.claude/rules/klai/pitfalls/security.md` een RLS coverage-tabel

---

## AC-8: Rollback werkt

**Given** een migratie is toegepast
**When** `alembic downgrade -1` wordt uitgevoerd
**Then** worden de RLS-policies verwijderd en RLS gedisabled op de betreffende tabellen
**And** is het systeem functioneel identiek aan de pre-migratie staat

---

## Edge Cases

### EC-1: Signup flow — user aanmaken zonder tenant context

**Given** een nieuwe gebruiker registreert zich (signup endpoint)
**When** een `PortalUser` INSERT wordt uitgevoerd
**Then** moet de INSERT slagen (de signup endpoint roept `set_tenant()` aan na org-creatie)

Verificatie: test de volledige signup flow na Fase 2b deployment.

### EC-2: Webhook zonder tenant context

**Given** een Vexa `completed` webhook arriveert
**When** de webhook handler de meeting-status UPDATE uitvoert
**Then** slaagt de UPDATE (background task UPDATE-policy staat dit toe)

### EC-3: Nullable org_id op vexa_meetings

**Given** een meeting met `org_id = NULL` (edge case uit vroege data)
**When** een SELECT met RLS actief wordt uitgevoerd
**Then** wordt deze meeting NIET geretourneerd (NULL != any org_id)
**And** dit is acceptabel gedrag — meetings zonder org_id zijn orphans

---

## Quality Gates

- [ ] Alle migraties draaien zonder fouten (`alembic upgrade head`)
- [ ] ruff check: 0 errors
- [ ] pyright: 0 errors
- [ ] Alle interne endpoints getest met curl na deployment
- [ ] pg_policies query toont 16+ policies (6 bestaand + 10+ nieuw)
- [ ] Geen cross-tenant data zichtbaar in steekproef-query's
